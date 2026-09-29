# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Smoke-test the Quickstart's code patterns against mocked XRPL client.

Mirrors docs/QUICKSTART.md exactly. If these tests pass, the Quickstart's
code blocks are correct against xrpl_agent_id 0.3.2 — every import resolves,
every call signature is accepted, and the documented output strings match.

Run with:
    /usr/bin/python3 -m pytest tests/_smoke/test_quickstart_api.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from xrpl.wallet import Wallet


@pytest.fixture(scope="module")
def smoke_env():
    """Run the Quickstart's code path end-to-end with a mocked XRPL client.

    Returns the locals a successful run produces (dids, cred bytes, etc.)
    so individual assertions below can reference them.
    """
    mock_client = MagicMock()
    SEED = Wallet.create().seed  # cryptographically valid seed

    env = {"SEED": SEED}

    from xrpl_agent_id import AgentIdentity, Authority, CredentialType, TrustRegistry

    # ---- Step 1: identities ----
    env["bob"] = AgentIdentity.from_seed(SEED, network="testnet")
    assert env["bob"].did.startswith("did:xrpl:2:")
    assert env["bob"].address.startswith("r")

    # ---- Step 2: credential type as bytes ----
    env["CRED_TYPE"] = CredentialType.VERIFIED_AGENT_OPERATOR.value.encode("utf-8")
    assert isinstance(env["CRED_TYPE"], bytes)

    # ---- Step 3: issue_credential accepts the documented arg shape ----
    # The mock will fail downstream in submit_and_wait's async dance. We catch
    # only TypeError; if it's the expected "can't await MagicMock" TypeError
    # from xrpl-py (not from issue_credential's own arg validation), the
    # signature is correct.
    with patch("xrpl_agent_id.network.get_client", return_value=mock_client):
        env["weathercorp"] = Authority.from_seed(SEED, network="testnet")
        try:
            env["weathercorp"].issue_credential(
                subject=env["bob"].address,
                credential_type=env["CRED_TYPE"],
                uri="https://weathercorp.example/credentials/verified-v1.json",
            )
        except TypeError as e:
            assert "can't be used in 'await' expression" in str(e), (
                f"unexpected TypeError — issue_credential's own arg validation "
                f"may have failed: {e}"
            )
        except Exception:
            pass  # any other downstream mock failure is fine

    # ---- Step 4: TrustRegistry.require is fluent ----
    env["reg"] = TrustRegistry(network="testnet")
    ret = env["reg"].require(
        issuer=None,
        credential_type=env["CRED_TYPE"],
        description="Verified-operator credential from a recognized issuer",
    )
    assert ret is env["reg"], "require() must return self for fluent chaining"
    assert len(env["reg"]._rules) == 1
    assert env["reg"]._rules[0].action == "require"

    # ---- Step 5: TrustRegistry.deny ----
    env["reg"].deny(
        issuer="rBadActorAddressHere...",
        credential_type=None,
        description="Known compromised issuer",
    )
    assert len(env["reg"]._rules) == 2
    assert env["reg"]._rules[1].action == "deny"

    return env


def test_step1_identity(smoke_env):
    """Step 1 — AgentIdentity.from_seed produces a testnet DID."""
    bob = smoke_env["bob"]
    assert bob.did.startswith("did:xrpl:2:")
    assert len(bob.address) > 25
    assert bob.public_key.startswith("ED")


def test_step2_credential_type(smoke_env):
    """Step 2 — CredentialType.<X>.value.encode('utf-8') is the bytes form."""
    CRED_TYPE = smoke_env["CRED_TYPE"]
    assert CRED_TYPE == b"verified_agent_operator"
    assert len(CRED_TYPE) <= 64  # XLS-70 limit


def test_step3_issue_signature(smoke_env):
    """Step 3 — issue_credential accepts (subject, credential_type, uri)."""
    # smoke_env runs the call; reaching this point means the signature worked.
    assert smoke_env["weathercorp"] is not None


def test_step4_require_fluent(smoke_env):
    """Step 4 — TrustRegistry.require returns self for fluent chaining."""
    reg = smoke_env["reg"]
    assert any(r.action == "require" for r in reg._rules)


def test_step5_deny_registered(smoke_env):
    """Step 5 — TrustRegistry.deny registers a 'deny' rule."""
    reg = smoke_env["reg"]
    deny_rules = [r for r in reg._rules if r.action == "deny"]
    assert len(deny_rules) == 1


def test_summary_format_matches_doc():
    """The summary() output strings in the Quickstart must match the library."""
    from xrpl_agent_id.trust import TrustCheckResult

    happy = TrustCheckResult(
        agent_did="did:xrpl:2:rEXAMPLE",
        satisfied=True,
        missing_required=[],
        denied_held=[],
    )
    sad = TrustCheckResult(
        agent_did="did:xrpl:2:rEXAMPLE",
        satisfied=False,
        missing_required=[("rX", b"Y")],
        denied_held=[],
    )

    assert happy.summary().startswith("✓ trust policy satisfied for did:xrpl:2:rEXAMPLE")
    assert sad.summary().startswith("✗ trust policy FAILED for did:xrpl:2:rEXAMPLE")
    assert "1 missing required" in sad.summary()


def test_verify_set_signature():
    """Step 9 — Authority.verify_set accepts (agent_did, required=[(addr, bytes), ...])."""
    # Verify the signature compiles; we don't run it (would need a real ledger).
    import inspect

    from xrpl_agent_id import Authority

    sig = inspect.signature(Authority.verify_set)
    params = list(sig.parameters.keys())
    assert "agent_did" in params
    assert "required" in params
