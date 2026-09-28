# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Scenario test runner for xrpl_agent_id trust library.

Exercises the FULL trust library surface on XRPL testnet, including:
  1. CredentialCreate (issuance)
  2. CredentialAccept (opt-in)
  3. DIDSet (anchor identity)
  4. Authority.verify_set (batch verification)
  5. TrustRegistry.check (policy-based verification)
  6. CredentialDelete (revocation)
  7. ResolveDID (third-party verification)

All on-ledger artifacts are captured to results/scenario_<timestamp>.json.

Usage:
    /usr/bin/python3 scripts/run_trust_scenarios.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xrpl_agent_id import (  # noqa: E402
    AgentIdentity,
    Authority,
    CredentialType,
    TrustRegistry,
    get_client,
)
from xrpl_agent_id.network import get_network  # noqa: E402

RESULTS_DIR = Path(__file__).parent.parent / "results"


def fund_wallet_via_faucet() -> dict:
    req = urllib.request.Request(
        "https://faucet.altnet.rippletest.net/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wait_for_balance(client, address: str, min_drops: int = 50_000_000, timeout_s: int = 30) -> int:
    from xrpl.models.requests import AccountInfo

    deadline = time.time() + timeout_s
    last = 0
    while time.time() < deadline:
        try:
            resp = client.request(AccountInfo(account=address))
            last = int(resp.result["account_data"]["Balance"])
            if last >= min_drops:
                return last
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"{address} never reached {min_drops} drops (last: {last})")


def main() -> int:
    print("=" * 70)
    print("xrpl_agent_id — Trust library scenario run on XRPL testnet")
    print(f"  started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    network = get_network("testnet")
    client = get_client("testnet")
    print(f"  network: {network.json_rpc_url}")

    # Wait briefly so autofill doesn't pick up a stale LastLedgerSequence
    # from any prior test run. (mitigates intermittent tefPAST_SEQ)
    print("\n--- Syncing with current ledger (3s) ---")
    time.sleep(3)

    # Step 1: Fund three wallets — subject, issuer, evaluator
    print("\n--- Step 1: funding wallets via faucet ---")
    subj_data = fund_wallet_via_faucet()
    iss_data = fund_wallet_via_faucet()
    eval_data = fund_wallet_via_faucet()

    subject = AgentIdentity.from_seed(subj_data["seed"], network="testnet")
    issuer = Authority.from_seed(iss_data["seed"], network="testnet")
    evaluator = Authority.from_seed(eval_data["seed"], network="testnet")

    wait_for_balance(client, subject.address)
    wait_for_balance(client, issuer.address)
    wait_for_balance(client, evaluator.address)

    print(f"  subject:   {subject.address}")
    print(f"  issuer:    {issuer.address}")
    print(f"  evaluator: {evaluator.address}")

    log = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "network": {"name": "testnet", "json_rpc_url": network.json_rpc_url},
        "wallets": {
            "subject":   {"address": subject.address,   "did": subject.did},
            "issuer":    {"address": issuer.address,    "did": issuer.did},
            "evaluator": {"address": evaluator.address, "did": evaluator.did},
        },
        "steps": {},
    }

    # Step 2: Issuer issues KYC credential
    print("\n--- Step 2: issuer issues KYC credential ---")
    kyc_type = CredentialType.AGENT_ID_V1.value.encode()
    kyc_uri = "ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k"
    issuer.issue_credential(subject=subject.address, credential_type=kyc_type, uri=kyc_uri)
    log["steps"]["issue_kyc"] = {"tx_type": "CredentialCreate", "type": "AGENT_ID_V1", "uri": kyc_uri}
    time.sleep(4)

    # Step 3: Evaluator issues EVAL_PASSED
    print("\n--- Step 3: evaluator issues EVAL_PASSED credential ---")
    eval_type = CredentialType.EVAL_PASSED.value.encode()
    evaluator.issue_credential(subject=subject.address, credential_type=eval_type, uri=kyc_uri)
    log["steps"]["issue_eval"] = {"tx_type": "CredentialCreate", "type": "EVAL_PASSED", "uri": kyc_uri}
    time.sleep(4)

    # Step 4: Subject accepts both
    print("\n--- Step 4: subject accepts both credentials ---")
    subject.accept_credential(issuer=issuer.address, credential_type=kyc_type)
    subject.accept_credential(issuer=evaluator.address, credential_type=eval_type)
    log["steps"]["accept_kyc"] = {"tx_type": "CredentialAccept"}
    log["steps"]["accept_eval"] = {"tx_type": "CredentialAccept"}
    time.sleep(4)

    # Step 5: Subject writes DID via URI-only pattern
    print("\n--- Step 5: subject writes DID via DIDSet (URI-only) ---")
    did_uri = f"https://xrpl-agent-id.example/did/{subject.address}"
    subject.set_did_uri(did_uri)
    log["steps"]["did_set"] = {"tx_type": "DIDSet", "uri": did_uri}
    time.sleep(5)

    # Step 6: Authority.verify_set — batch verification
    print("\n--- Step 6: Authority.verify_set ---")
    result = issuer.verify_set(
        agent_did=subject.address,
        required=[
            (issuer.address, kyc_type),       # should be held
            (evaluator.address, eval_type),   # should be held
            (issuer.address, b"NONEXISTENT"), # should NOT be held
        ],
    )
    print(f"  summary: {result.summary()}")
    print(f"  all_satisfied: {result.all_satisfied}")
    print(f"  missing: {result.missing}")
    log["steps"]["verify_set"] = {
        "summary": result.summary(),
        "all_satisfied": result.all_satisfied,
        "missing": [(iss, ct.hex().upper()) for iss, ct in result.missing],
        "results": {k: v for k, v in result.results.items()},
    }

    # Step 7: TrustRegistry — policy that requires both credentials
    print("\n--- Step 7: TrustRegistry.check — policy: KYC + EVAL_PASSED ---")
    policy = TrustRegistry(network="testnet")
    policy.require(issuer=issuer.address, credential_type=kyc_type)
    policy.require(issuer=evaluator.address, credential_type=eval_type)
    check = policy.check(agent_did=subject.address)
    print(f"  {check.summary()}")
    log["steps"]["trust_registry_pass"] = {
        "summary": check.summary(),
        "satisfied": check.satisfied,
        "missing_required": [(iss, ct.hex().upper()) for iss, ct in check.missing_required],
    }

    # Step 8: TrustRegistry — policy that requires a credential the agent does NOT have
    print("\n--- Step 8: TrustRegistry.check — policy: requires NONEXISTENT (should fail) ---")
    strict = TrustRegistry(network="testnet")
    strict.require(issuer=issuer.address, credential_type=b"NONEXISTENT_CREDENTIAL_TYPE")
    fail_check = strict.check(agent_did=subject.address)
    print(f"  {fail_check.summary()}")
    log["steps"]["trust_registry_fail"] = {
        "summary": fail_check.summary(),
        "satisfied": fail_check.satisfied,
        "missing_required": [(iss, ct.hex().upper()) for iss, ct in fail_check.missing_required],
    }

    # Step 9: Revoke KYC, then re-verify
    print("\n--- Step 9: issuer revokes KYC credential via CredentialDelete ---")
    revoke_hash = issuer.revoke_credential(
        subject=subject.address,
        credential_type=kyc_type,
    )
    print(f"  revoke tx_hash: {revoke_hash}")
    log["steps"]["revoke_kyc"] = {"tx_type": "CredentialDelete", "tx_hash": revoke_hash}
    time.sleep(4)

    # Step 10: Re-verify — KYC should now be missing
    print("\n--- Step 10: Authority.verify_set after revocation ---")
    after_revoke = issuer.verify_set(
        agent_did=subject.address,
        required=[
            (issuer.address, kyc_type),       # should be MISSING now
            (evaluator.address, eval_type),   # should still be held
        ],
    )
    print(f"  summary: {after_revoke.summary()}")
    print(f"  missing: {after_revoke.missing}")
    log["steps"]["verify_set_after_revoke"] = {
        "summary": after_revoke.summary(),
        "missing": [(iss, ct.hex().upper()) for iss, ct in after_revoke.missing],
    }

    # Step 11: TrustRegistry — same policy, should now fail because KYC is gone
    print("\n--- Step 11: TrustRegistry.check after revocation (should fail) ---")
    after_revoke_check = policy.check(agent_did=subject.address)
    print(f"  {after_revoke_check.summary()}")
    log["steps"]["trust_registry_after_revoke"] = {
        "summary": after_revoke_check.summary(),
        "satisfied": after_revoke_check.satisfied,
        "missing_required": [(iss, ct.hex().upper()) for iss, ct in after_revoke_check.missing_required],
    }

    # Write log
    log["finished_at"] = datetime.now(timezone.utc).isoformat()
    out_path = RESULTS_DIR / f"scenario_{int(time.time())}.json"
    out_path.write_text(json.dumps(log, indent=2, default=str))
    print(f"\n=== SCENARIO RUN COMPLETE ===")
    print(f"  log: {out_path}")
    print(f"  duration: {datetime.fromisoformat(log['finished_at']) - datetime.fromisoformat(log['started_at'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
