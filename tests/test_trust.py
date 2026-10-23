# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the trust library layer (TrustRegistry, verify_set, revoke).

These tests use unittest.mock to avoid network in CI. Live behavior is
verified in tests/test_integration_ledger_live.py with RUN_LIVE=1.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xrpl_agent_id.authority import Authority, VerificationResult
from xrpl_agent_id.trust import TrustCheckResult, TrustPolicy, TrustRegistry


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------

class TestVerificationResult:
    def test_all_satisfied_when_every_result_true(self):
        r = VerificationResult(
            agent_did="did:xrpl:1:r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n",
            results={"rIssuer1:AABBCC": True, "rIssuer2:DDEEFF": True},
        )
        assert r.all_satisfied is True
        assert r.summary() == "2/2 required credentials satisfied"

    def test_all_satisfied_false_when_any_missing(self):
        r = VerificationResult(
            agent_did="did:xrpl:1:r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n",
            results={"rIssuer1:AABBCC": True, "rIssuer2:DDEEFF": False},
            missing=[("rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", bytes.fromhex("DDEEFF"))],
        )
        assert r.all_satisfied is False
        assert "1/2" in r.summary()


# ---------------------------------------------------------------------------
# TrustPolicy.matches
# ---------------------------------------------------------------------------

class TestTrustPolicyMatches:
    def test_specific_issuer_and_type_match(self):
        p = TrustPolicy(issuers={"rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"}, credential_type=b"KYC", action="require")
        assert p.matches("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"KYC") is True
        assert p.matches("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"EVAL") is False
        assert p.matches("rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", b"KYC") is False

    def test_any_issuer(self):
        p = TrustPolicy(issuers=None, credential_type=b"KYC", action="require")
        assert p.matches("rAnyIssuer", b"KYC") is True
        assert p.matches("rOtherIssuer", b"KYC") is True

    def test_any_type(self):
        p = TrustPolicy(issuers={"rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"}, credential_type=None, action="require")
        assert p.matches("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"ANY") is True
        assert p.matches("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"OTHER") is True


# ---------------------------------------------------------------------------
# TrustRegistry (rule construction)
# ---------------------------------------------------------------------------

class TestTrustRegistryRuleConstruction:
    def test_require_string_issuer(self):
        reg = TrustRegistry(network="testnet")
        reg.require(issuer="rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", credential_type=b"KYC")
        assert len(reg._rules) == 1
        assert reg._rules[0].action == "require"
        assert reg._rules[0].issuers == {"rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"}
        assert reg._rules[0].credential_type == b"KYC"

    def test_require_iterable_issuers(self):
        reg = TrustRegistry(network="testnet")
        reg.require(issuer=["rA", "rB"], credential_type=b"KYC")
        assert reg._rules[0].issuers == {"rA", "rB"}

    def test_require_any_issuer(self):
        reg = TrustRegistry(network="testnet")
        reg.require(issuer=None, credential_type=b"KYC")
        assert reg._rules[0].issuers is None

    def test_deny(self):
        reg = TrustRegistry(network="testnet")
        reg.deny(issuer="rBad", credential_type=b"MALICIOUS")
        assert reg._rules[0].action == "deny"

    def test_fluent_returns_self(self):
        reg = TrustRegistry(network="testnet")
        assert reg.require(issuer="rA", credential_type=b"X") is reg
        assert reg.deny(issuer="rB", credential_type=b"Y") is reg


# ---------------------------------------------------------------------------
# TrustRegistry.check (mocked ledger)
# ---------------------------------------------------------------------------

class TestTrustRegistryCheck:
    # A real LedgerEntry credential response is a dict carrying Flags; the
    # lsfAccepted bit (0x00010000) is what separates an accepted credential from
    # one the subject never signed. Mocking node as a bare string let a credential
    # that merely EXISTS satisfy a trust requirement -- the bug guarded here.
    def _mock_client(self, credential_map: dict[tuple[str, str], bool]):
        """Build a mock client that responds to ledger_entry queries.

        credential_map: {(subject, issuer): credential_type_hex} — what cred_type is held?
        Use False to indicate the credential is NOT held.
        """
        client = MagicMock()
        def fake_request(req):
            cred = getattr(req, "credential", None)
            if cred is None:
                raise RuntimeError("unexpected request shape in mock")
            key = (cred.subject, cred.issuer)
            held_type = credential_map.get(key, None)
            if held_type is None:
                # No credential exists for this (subject, issuer) pair
                return MagicMock(result={})
            # If caller asked for a specific type, only return success if it matches
            if cred.credential_type is not None and cred.credential_type != held_type:
                return MagicMock(result={})
            return MagicMock(result={"node": {"Flags": 0x00010000, "Issuer": "rIss", "Subject": "rSub", "CredentialType": "4B5943"}})
        client.request.side_effect = fake_request
        return client

    def test_check_satisfied_when_all_required_present(self):
        # "KYC" in hex = 4B5943, "EVAL_PASS" = 4556414C5F50415353
        client = self._mock_client({
            ("r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n", "rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"): "4B5943",
            ("r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n", "rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc"): "4556414C5F50415353",
        })
        with patch("xrpl_agent_id.trust.get_client", return_value=client):
            reg = TrustRegistry(network="testnet")
            reg.require(issuer="rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", credential_type=b"KYC")
            reg.require(issuer="rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", credential_type=b"EVAL_PASS")
            result = reg.check(agent_did="r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n")
        assert result.satisfied is True
        assert result.missing_required == []
        assert "✓" in result.summary()

    def test_check_unsatisfied_when_one_required_missing(self):
        client = self._mock_client({
            ("r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n", "rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"): "4B5943",  # KYC held
            # rIssuer2: not in map → not held
        })
        with patch("xrpl_agent_id.trust.get_client", return_value=client):
            reg = TrustRegistry(network="testnet")
            reg.require(issuer="rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", credential_type=b"KYC")
            reg.require(issuer="rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", credential_type=b"EVAL_PASS")
            result = reg.check(agent_did="r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n")
        assert result.satisfied is False
        assert len(result.missing_required) == 1
        assert result.missing_required[0] == ("rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", b"EVAL_PASS")

    def test_check_passes_did_xrpl_address(self):
        client = self._mock_client({
            ("r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n", "rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf"): "4B5943",  # KYC
        })
        with patch("xrpl_agent_id.trust.get_client", return_value=client):
            reg = TrustRegistry(network="testnet")
            reg.require(issuer="rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", credential_type=b"KYC")
            result = reg.check(agent_did="did:xrpl:1:r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n")
        assert result.satisfied is True


# ---------------------------------------------------------------------------
# Authority.verify_set (mocked ledger)
# ---------------------------------------------------------------------------

class TestAuthorityVerifySet:
    def test_verify_set_returns_verification_result(self):
        client = MagicMock()
        def fake_request(req):
            cred = req.credential
            # Agent holds KYC from rIssuer1 only
            if cred.subject == "r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n" and cred.issuer == "rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf" and cred.credential_type == "4B5943":
                return MagicMock(result={"node": {"Flags": 0x00010000, "Issuer": "rIss", "Subject": "rSub", "CredentialType": "4B5943"}})
            return MagicMock(result={})
        client.request.side_effect = fake_request

        authority = Authority.__new__(Authority)
        authority.network = "testnet"

        with patch("xrpl_agent_id.network.get_client", return_value=client):
            result = authority.verify_set(
                agent_did="r6eQqpRNmwFubHw7fyiP9uL9YcZkVsQ8n",
                required=[
                    ("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"KYC"),         # held
                    ("rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", b"EVAL_PASS"),  # not held
                ],
            )
        assert isinstance(result, VerificationResult)
        assert result.all_satisfied is False
        assert result.missing == [("rEePd7C3J9AbtWcDqh36PD6TvZmrnFZwHc", b"EVAL_PASS")]

    def test_verify_set_did_form_input(self):
        client = MagicMock()
        client.request.return_value = MagicMock(result={"node": {"Flags": 0x00010000, "Issuer": "rIss", "Subject": "rSub", "CredentialType": "4B5943"}})
        authority = Authority.__new__(Authority)
        authority.network = "testnet"

        with patch("xrpl_agent_id.network.get_client", return_value=client):
            result = authority.verify_set(
                agent_did="did:xrpl:1:rAgentAddr",
                required=[("rGxvG8UGLLozZAQfg819HTjsfjmHaoVPSf", b"KYC")],
            )
        assert result.agent_did == "did:xrpl:1:rAgentAddr"
        call_args = client.request.call_args[0][0]
        assert call_args.credential.subject == "rAgentAddr"


# ---------------------------------------------------------------------------
# Public API surface — sanity check
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_imports(self):
        import xrpl_agent_id
        assert hasattr(xrpl_agent_id, "TrustRegistry")
        assert hasattr(xrpl_agent_id, "TrustPolicy")
        assert hasattr(xrpl_agent_id, "TrustCheckResult")
        assert hasattr(xrpl_agent_id, "VerificationResult")
        assert hasattr(xrpl_agent_id, "Authority")
        assert hasattr(xrpl_agent_id, "AgentIdentity")
        assert xrpl_agent_id.__version__.startswith("0.")
