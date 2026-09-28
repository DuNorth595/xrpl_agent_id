# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""End-to-end Agent ID issuance demo on XRPL testnet.

Exercises the full flow:
  1. Fund two fresh wallets via the testnet faucet
  2. Issuer submits CredentialCreate (subject: agent wallet)
  3. Subject submits CredentialAccept
  4. Subject writes a DID Document via DIDSet
  5. Anyone can resolve the DID + verify the credential via ledger_entry
  6. All on-ledger artifacts (tx hashes, ledger indexes, raw ledger entries)
     are captured to results/issuance_testnet_<timestamp>.json

Usage:
    /usr/bin/python3 scripts/issue_agent_id.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Make the package importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from xrpl_agent_id import AgentIdentity, Authority, CredentialType, get_client  # noqa: E402
from xrpl_agent_id.network import get_network  # noqa: E402


RESULTS_DIR = Path(__file__).parent.parent / "results"


def fund_wallet_via_faucet() -> dict:
    """Hit the testnet faucet to get a fresh wallet."""
    req = urllib.request.Request(
        "https://faucet.altnet.rippletest.net/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wait_for_balance(client, address: str, min_drops: int = 50_000_000, timeout_s: int = 30) -> int:
    """Wait until the account has at least `min_drops` drops of XRP."""
    from xrpl.models.requests import AccountInfo

    deadline = time.time() + timeout_s
    last_balance = 0
    while time.time() < deadline:
        try:
            resp = client.request(AccountInfo(account=address))
            bal = int(resp.result["account_data"]["Balance"])
            last_balance = bal
            if bal >= min_drops:
                return bal
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(
        f"Account {address} never reached {min_drops} drops (last: {last_balance})"
    )


def ledger_entry(client, **kwargs) -> dict:
    """Wrapper around xrpl.ledger_entry that returns the node or raises."""
    from xrpl.models.requests import LedgerEntry

    resp = client.request(LedgerEntry(**kwargs))
    node = (resp.result or {}).get("node")
    if not node:
        raise ValueError(f"No ledger entry found for {kwargs}")
    return node


def main() -> dict:
    """Run the full issuance flow and return a results dict."""
    started_at = datetime.now(timezone.utc).isoformat()
    client = get_client("testnet")
    network = get_network("testnet")

    print("=" * 70)
    print("xrpl_agent_id — Live Testnet Issuance Demo")
    print("=" * 70)
    print(f"Started: {started_at}")
    print(f"Network: {network.name} (id={network.network_id})")
    print(f"JSON-RPC: {network.json_rpc_url}")
    print()

    # ---- Step 1: fund wallets -------------------------------------------
    print("[1/6] Funding subject (Agent) wallet from testnet faucet...")
    subject_data = fund_wallet_via_faucet()
    print(f"  address: {subject_data['account']['address']}")
    print(f"  seed:    {subject_data['seed'][:6]}...{subject_data['seed'][-4:]}")
    print(f"  balance: {subject_data['amount']} XRP")
    print()

    print("[1/6] Funding issuer (Authority) wallet from testnet faucet...")
    issuer_data = fund_wallet_via_faucet()
    print(f"  address: {issuer_data['account']['address']}")
    print(f"  seed:    {issuer_data['seed'][:6]}...{issuer_data['seed'][-4:]}")
    print(f"  balance: {issuer_data['amount']} XRP")
    print()

    subject = AgentIdentity.from_seed(subject_data["seed"], network="testnet")
    issuer = Authority.from_seed(issuer_data["seed"], network="testnet")

    wait_for_balance(client, subject.address)
    wait_for_balance(client, issuer.address)
    print("Both wallets funded and confirmed on-ledger.")
    print()

    results: dict = {
        "started_at": started_at,
        "network": {
            "name": network.name,
            "network_id": network.network_id,
            "json_rpc_url": network.json_rpc_url,
        },
        "subject": {
            "address": subject.address,
            "did": subject.did,
            "public_key": subject.public_key,
            # seed intentionally omitted — security
        },
        "issuer": {
            "address": issuer.address,
            "did": issuer.did,
            "public_key": issuer.public_key,
        },
        "steps": {},
    }

    # ---- Step 2: issuer builds CredentialCreate -------------------------
    print("[2/6] Issuer (Authority) building CredentialCreate transaction...")
    cred_type_bytes = CredentialType.AGENT_ID_V1.value.encode()
    cred_uri = "ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k"
    print(f"  credential_type: {CredentialType.AGENT_ID_V1.value!r} ({cred_type_bytes.hex()})")
    print(f"  uri:             {cred_uri}")
    print()

    # We submit via a manual path so we can capture the tx hash + ledger index
    # from the response (submit_and_wait in AgentIdentity.issue_credential
    # discards them).
    from xrpl.models.transactions import CredentialCreate
    from xrpl.transaction import submit_and_wait

    cred_obj = None
    from xrpl_agent_id.credential import Credential

    cred_obj = Credential(
        issuer=issuer.did,
        subject=subject.did,
        credential_type=cred_type_bytes,
        uri=cred_uri,
        accepted=False,
    )
    tx_fields = cred_obj.to_ledger_fields()
    create_tx = CredentialCreate(account=issuer.address, **tx_fields)
    create_resp = submit_and_wait(create_tx, client, issuer.wallet)
    create_result = create_resp.result or {}
    create_hash = create_result.get("hash")
    create_meta = create_result.get("meta") or {}
    create_tx_result = create_meta.get("TransactionResult")
    create_ledger_index = create_result.get("ledger_index")
    print(f"  tx_hash:         {create_hash}")
    print(f"  tx_result:       {create_tx_result}")
    print(f"  ledger_index:    {create_ledger_index}")
    print()

    results["steps"]["step_2_credential_create"] = {
        "tx_hash": create_hash,
        "tx_result": create_tx_result,
        "ledger_index": create_ledger_index,
        "credential_type_hex": cred_obj.hex_type,
        "uri": cred_uri,
        "fields": tx_fields,
        "tx_json": create_tx.to_xrpl() if hasattr(create_tx, "to_xrpl") else None,
    }

    if create_tx_result != "tesSUCCESS":
        print(f"  !! CredentialCreate did not succeed: {create_tx_result}")
        return results

    # ---- Step 3: subject accepts ----------------------------------------
    print("[3/6] Subject submitting CredentialAccept...")
    from xrpl.models.transactions import CredentialAccept

    accept_tx = CredentialAccept(
        account=subject.address,
        issuer=issuer.address,
        credential_type=cred_type_bytes.hex().upper(),
    )
    accept_resp = submit_and_wait(accept_tx, client, subject.wallet)
    accept_result = accept_resp.result or {}
    accept_hash = accept_result.get("hash")
    accept_meta = accept_result.get("meta") or {}
    accept_tx_result = accept_meta.get("TransactionResult")
    accept_ledger_index = accept_result.get("ledger_index")
    print(f"  tx_hash:         {accept_hash}")
    print(f"  tx_result:       {accept_tx_result}")
    print(f"  ledger_index:    {accept_ledger_index}")
    print()

    results["steps"]["step_3_credential_accept"] = {
        "tx_hash": accept_hash,
        "tx_result": accept_tx_result,
        "ledger_index": accept_ledger_index,
    }

    if accept_tx_result != "tesSUCCESS":
        print(f"  !! CredentialAccept did not succeed: {accept_tx_result}")
        return results

    # ---- Step 4: subject writes DID Document ----------------------------
    print("[4/6] Subject writing DID Document via DIDSet...")
    from xrpl_agent_id.did import DIDDocument
    from xrpl.models.transactions import DIDSet

    did_doc = DIDDocument(
        id=subject.did,
        verification_method=[
            {
                "id": f"{subject.did}#keys-1",
                "type": "Multikey",
                "controller": subject.did,
                "publicKeyHex": subject.public_key,
            }
        ],
        authentication=[f"{subject.did}#keys-1"],
    )
    doc_json = did_doc.to_json()
    doc_hex = json.dumps(doc_json, separators=(",", ":")).encode("utf-8").hex().upper()
    print(f"  DID Document size: {len(doc_hex) // 2} bytes (cap is 256)")

    did_tx = DIDSet(account=subject.address, did_document=doc_hex)
    did_resp = submit_and_wait(did_tx, client, subject.wallet)
    did_result = did_resp.result or {}
    did_hash = did_result.get("hash")
    did_meta = did_result.get("meta") or {}
    did_tx_result = did_meta.get("TransactionResult")
    did_ledger_index = did_result.get("ledger_index")
    print(f"  tx_hash:         {did_hash}")
    print(f"  tx_result:       {did_tx_result}")
    print(f"  ledger_index:    {did_ledger_index}")
    print()

    results["steps"]["step_4_did_set"] = {
        "tx_hash": did_hash,
        "tx_result": did_tx_result,
        "ledger_index": did_ledger_index,
        "did_document_bytes": len(doc_hex) // 2,
        "did_document_json": doc_json,
        "did_document_hex": doc_hex,
    }

    if did_tx_result != "tesSUCCESS":
        print(f"  !! DIDSet did not succeed: {did_tx_result}")
        return results

    # ---- Step 5: anyone verifies ----------------------------------------
    print("[5/6] Verifying on-ledger (simulating a third-party verifier)...")
    time.sleep(5)  # wait for ledger to close so the entry is queryable

    # 5a. Resolve DID
    print(f"  5a. resolve_did({subject.did})")
    did_node = ledger_entry(client, did=subject.address)
    results["steps"]["step_5a_resolve_did"] = {
        "method": "ledger_entry(did=<account>)",
        "account": subject.address,
        "node": did_node,
    }
    print(f"      ✓ DIDDocument present: {'DIDDocument' in did_node}")
    print(f"      ✓ URI present:         {'URI' in did_node}")

    # 5b. Verify credential exists
    print(f"  5b. verify_credential(issuer={issuer.address}, subject={subject.address})")
    from xrpl.models.requests.ledger_entry import Credential as LedgerCredential

    cred_node = ledger_entry(
        client,
        credential=LedgerCredential(
            subject=subject.address,
            issuer=issuer.address,
            credential_type=cred_type_bytes.hex().upper(),
        ),
    )
    results["steps"]["step_5b_verify_credential"] = {
        "method": "ledger_entry(credential={subject, issuer, credential_type})",
        "params": {
            "subject": subject.address,
            "issuer": issuer.address,
            "credential_type": cred_type_bytes.hex().upper(),
        },
        "node": cred_node,
    }
    print(f"      ✓ Credential entry found: Issuer={cred_node.get('Issuer')}")
    print(f"                              Subject={cred_node.get('Subject')}")

    # ---- Step 6: summary -------------------------------------------------
    print()
    print("[6/6] Issuance complete. Summary:")
    print(f"  Subject DID:  {subject.did}")
    print(f"  Issuer DID:   {issuer.did}")
    print(f"  CredentialCreate tx:   {create_hash}")
    print(f"  CredentialAccept tx:   {accept_hash}")
    print(f"  DIDSet tx:             {did_hash}")
    print()
    print("All three are independently verifiable on the public XRPL testnet ledger.")

    results["ended_at"] = datetime.now(timezone.utc).isoformat()
    results["status"] = "complete"

    # Save
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"issuance_testnet_{timestamp}.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"Results written to: {out_path}")

    # Also write a "latest" symlink for easy reference
    latest = RESULTS_DIR / "issuance_testnet_latest.json"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.write_text(json.dumps(results, indent=2, default=str))
    print(f"Latest copy:        {latest}")

    return results


if __name__ == "__main__":
    main()
