# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Live XRPL testnet integration tests for xrpl_agent_id.

Run with:
    /usr/bin/python3 -m pytest tests/test_integration_ledger_live.py -v --run-live

These tests hit the public XRPL testnet. They:
  1. Create fresh funded wallets via the testnet faucet
  2. Issue a Credential via CredentialCreate (issuer signs)
  3. Accept the Credential via CredentialAccept (subject signs)
  4. Write a DID Document via DIDSet (subject signs)
  5. Verify the credential is resolvable via ledger_entry

Outputs are written to ../results/ for the issuance flow document.
Skipped by default to keep CI green; opt in with --run-live.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from xrpl_agent_id import (
    AgentIdentity,
    Authority,
    CredentialType,
    did_from_account,
)
from xrpl_agent_id.network import get_client, get_network

# Gate all tests in this module behind the RUN_LIVE env var.
# Set RUN_LIVE=1 to enable; otherwise skipped (keeps CI green).
_RUN_LIVE = os.environ.get("RUN_LIVE", "").lower() in ("1", "true", "yes")
pytestmark = pytest.mark.skipif(
    not _RUN_LIVE,
    reason="live testnet test (set RUN_LIVE=1 to enable)",
)


RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def _fund_wallet_via_faucet() -> dict:
    """Hit the testnet faucet to get a fresh wallet."""
    import urllib.request

    req = urllib.request.Request(
        "https://faucet.altnet.rippletest.net/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _wait_for_balance(client, address: str, min_drops: int = 50_000_000, timeout_s: int = 30) -> int:
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


@pytest.fixture(scope="module")
def live_wallets():
    """Fund two fresh wallets (subject + issuer) from the testnet faucet."""
    print("\n--- Funding wallets from testnet faucet ---")
    subject_data = _fund_wallet_via_faucet()
    issuer_data = _fund_wallet_via_faucet()

    subject = AgentIdentity.from_seed(subject_data["seed"], network="testnet")
    issuer = Authority.from_seed(issuer_data["seed"], network="testnet")

    client = get_client("testnet")
    _wait_for_balance(client, subject.address)
    _wait_for_balance(client, issuer.address)

    wallets = {
        "subject_seed": subject_data["seed"],
        "issuer_seed": issuer_data["seed"],
        "subject_address": subject.address,
        "issuer_address": issuer.address,
        "subject_did": subject.did,
        "issuer_did": issuer.did,
    }
    print(f"Subject: {subject.address} (DID: {subject.did})")
    print(f"Issuer:  {issuer.address} (DID: {issuer.did})")

    return wallets


# --- Tests -----------------------------------------------------------

def test_01_wallets_funded(live_wallets):
    """Both wallets have funded balances."""
    client = get_client("testnet")
    subject_bal = _wait_for_balance(client, live_wallets["subject_address"], min_drops=10_000_000)
    issuer_bal = _wait_for_balance(client, live_wallets["issuer_address"], min_drops=10_000_000)
    assert subject_bal >= 10_000_000
    assert issuer_bal >= 10_000_000


def test_02_issue_credential(live_wallets):
    """Issuer creates a CredentialCreate tx; ledger records a Credential entry."""
    issuer = Authority.from_seed(live_wallets["issuer_seed"], network="testnet")
    subject = AgentIdentity.from_seed(live_wallets["subject_seed"], network="testnet")

    print(f"\n--- Issuer {issuer.address} issuing credential to {subject.address} ---")

    # Use CredentialType enum value
    cred = issuer.issue_credential(
        subject=subject.address,
        credential_type=CredentialType.AGENT_ID_V1.value.encode(),
        uri="ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k",
    )

    print(f"CredentialCreate submitted:")
    print(f"  Issuer:   {cred.issuer}")
    print(f"  Subject:  {cred.subject}")
    print(f"  Type:     {cred.credential_type!r}")
    print(f"  URI:      {cred.uri}")
    print(f"  hex_type: {cred.hex_type}")

    assert cred.issuer == live_wallets["issuer_did"]
    assert cred.subject == live_wallets["subject_did"]
    assert cred.credential_type == CredentialType.AGENT_ID_V1.value.encode()
    assert not cred.accepted  # not yet accepted


def test_03_accept_credential(live_wallets):
    """Subject submits CredentialAccept; credential becomes queryable."""
    issuer = Authority.from_seed(live_wallets["issuer_seed"], network="testnet")
    subject = AgentIdentity.from_seed(live_wallets["subject_seed"], network="testnet")

    print(f"\n--- Issuer issuing again for accept test ---")
    # Issue again (we use a fresh credential each run for clean state)
    cred_type = CredentialType.EVAL_PASSED.value.encode()
    issuer.issue_credential(
        subject=subject.address,
        credential_type=cred_type,
        uri="ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k",
    )

    print(f"--- Subject accepting credential ---")
    accept_hash = subject.accept_credential(
        issuer=issuer.address,
        credential_type=cred_type,
    )
    print(f"CredentialAccept tx_hash: {accept_hash}")
    assert accept_hash
    assert len(accept_hash) == 64

    # Verify via has_credential()
    print(f"--- Verifying via ledger lookup ---")
    has_it = subject.has_credential(
        issuer=issuer.address,
        credential_type=cred_type,
    )
    print(f"has_credential: {has_it}")
    assert has_it, "ledger should return the accepted credential entry"


def test_04_set_did_document(live_wallets):
    """Subject writes a DID Document via DIDSet (URI-only pattern); resolve_did returns it."""
    subject = AgentIdentity.from_seed(live_wallets["subject_seed"], network="testnet")

    print(f"\n--- Subject writing DID via DIDSet (URI-only pattern) ---")
    # Full W3C DID Document with Multikey is ~495 bytes — over 256-byte cap.
    # Use the URI-only pattern: store the URI on-ledger, full doc off-ledger.
    did_uri = f"https://xrpl-agent-id.example/did/{subject.address}"
    doc_hash = subject.set_did_uri(did_uri)
    print(f"DIDSet (URI only) tx_hash: {doc_hash}")
    assert doc_hash
    assert len(doc_hash) == 64

    # Wait for the next ledger to close so the entry is queryable
    time.sleep(5)

    print(f"--- Resolving DID from ledger ---")
    resolved = subject.resolve_did_document()
    print(f"Resolved DID: {resolved.id}")
    assert resolved.id == subject.did

    # The on-ledger DID entry has only the URI, no DIDDocument.
    # resolve_did() should still succeed by synthesizing a minimal document
    # or by returning the URI for the caller to fetch.


def test_05_revoke_credential(live_wallets):
    """Issuer can revoke a previously issued credential.

    Revocation uses CredentialCreate with a `revoke://` URI marker so the
    audit trail stays on-ledger. The original credential entry remains
    visible (with its acceptance flag) but the latest CredentialCreate
    for the (issuer, subject, type) triple marks revocation.
    """
    issuer = Authority.from_seed(live_wallets["issuer_seed"], network="testnet")
    subject_addr = live_wallets["subject_address"]

    print(f"\n--- Issuer revoking EVAL_PASSED credential for {subject_addr} ---")
    cred_type = CredentialType.EVAL_PASSED.value.encode()
    revoke_hash = issuer.revoke_credential(
        subject=subject_addr,
        credential_type=cred_type,
    )
    print(f"Revoke CredentialCreate tx_hash: {revoke_hash}")
    assert revoke_hash
    assert len(revoke_hash) == 64


def test_06_verify_set_and_trust_registry(live_wallets):
    """Authority.verify_set and TrustRegistry.check return expected results.

    Issues a fresh credential (so this test does not depend on test_03
    or test_05 state), then verifies it via both verify_set and
    TrustRegistry.
    """
    from xrpl_agent_id import TrustRegistry

    issuer = Authority.from_seed(live_wallets["issuer_seed"], network="testnet")
    subject = AgentIdentity.from_seed(live_wallets["subject_seed"], network="testnet")
    subject_addr = live_wallets["subject_address"]
    issuer_addr = live_wallets["issuer_address"]

    # Issue a fresh credential for this test
    cred_type = CredentialType.VERIFIED_AGENT_OPERATOR.value.encode()
    print(f"\n--- Issuer issuing PRODUCTION_READY credential ---")
    issuer.issue_credential(
        subject=subject_addr,
        credential_type=cred_type,
        uri="ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k",
    )
    print(f"--- Subject accepting ---")
    accept_hash = subject.accept_credential(
        issuer=issuer_addr,
        credential_type=cred_type,
    )
    print(f"CredentialAccept tx_hash: {accept_hash}")

    fake_type = b"NONEXISTENT_CREDENTIAL_TYPE"

    print(f"--- Authority.verify_set({subject_addr}, [...]) ---")
    result = issuer.verify_set(
        agent_did=subject_addr,
        required=[
            (issuer_addr, cred_type),    # should be held
            (issuer_addr, fake_type),    # should NOT be held
        ],
    )
    print(f"verify_set summary: {result.summary()}")
    print(f"  satisfied: {result.all_satisfied}")
    print(f"  missing: {result.missing}")
    assert result.all_satisfied is False
    assert (issuer_addr, fake_type) in result.missing
    assert (issuer_addr, cred_type) not in result.missing

    # TrustRegistry — policy: requires PRODUCTION_READY from this issuer
    print(f"--- TrustRegistry.check({subject_addr}) — require PRODUCTION_READY ---")
    policy = TrustRegistry(network="testnet")
    policy.require(issuer=issuer_addr, credential_type=cred_type)
    check = policy.check(agent_did=subject_addr)
    print(f"TrustCheckResult: {check.summary()}")
    assert check.satisfied is True

    # TrustRegistry — policy: requires a type the agent does NOT have
    print(f"--- TrustRegistry.check — require NONEXISTENT_CREDENTIAL_TYPE ---")
    strict_policy = TrustRegistry(network="testnet")
    strict_policy.require(issuer=issuer_addr, credential_type=fake_type)
    fail_check = strict_policy.check(agent_did=subject_addr)
    print(f"TrustCheckResult: {fail_check.summary()}")
    assert fail_check.satisfied is False
    assert len(fail_check.missing_required) == 1
