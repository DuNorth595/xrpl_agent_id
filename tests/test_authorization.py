# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the AuthorizationPolicy layer.

All tests are offline — AgentRegistry is mocked at the module boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xrpl_agent_id.authorization import (
    AuthorizationPolicy,
    ReasonCode,
    RequestContext,
)
from xrpl_agent_id.banned import Ban, BannedAgentRegistry
from xrpl_agent_id.credential import Credential


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _cred(
    issuer: str,
    subject: str,
    ctype: bytes,
    accepted: bool = True,
    expiration: int | None = None,
) -> Credential:
    return Credential(
        issuer=f"did:xrpl:2:{issuer}",
        subject=f"did:xrpl:2:{subject}",
        credential_type=ctype,
        accepted=accepted,
        expiration=expiration,
    )


def _record(addr: str = "rSUBJ", controllers=None, creds=None):
    rec = MagicMock()
    rec.agent_address = addr
    rec.agent_did = f"did:xrpl:2:{addr}"
    rec.controllers = controllers or [addr]
    rec.credentials = creds or []
    rec.summary.return_value = f"fake {addr}"
    return rec


@pytest.fixture
def tmp_bans(tmp_path):
    path = tmp_path / "banned.json"
    bans = BannedAgentRegistry(network="testnet", path=path)
    bans.load()
    return bans


@pytest.fixture
def policy_with(tmp_bans):
    """Factory: create a policy with mocked registry returning the given record."""
    def _make(record, *, bans=None):
        with patch("xrpl_agent_id.authorization.AgentRegistry") as MockReg:
            instance = MagicMock()
            instance.resolve.return_value = record
            MockReg.return_value = instance
            pol = AuthorizationPolicy(network="testnet", bans=bans or tmp_bans, registry=instance)
            return pol
    return _make


# ---------------------------------------------------------------------------
# Allow when policy is empty
# ---------------------------------------------------------------------------

class TestEmptyPolicy:
    def test_no_rules_no_bans_allows(self, policy_with, tmp_bans):
        rec = _record()
        pol = policy_with(rec, bans=tmp_bans)
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True
        assert decision.reasons[0].code == ReasonCode.OK


# ---------------------------------------------------------------------------
# Banned-list denial
# ---------------------------------------------------------------------------

class TestBannedDenial:
    def test_banned_address_denies(self, policy_with, tmp_bans):
        tmp_bans.add(Ban(address="rSUBJ", reason="spam", added_by="sec"))
        rec = _record()
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.AGENT_BANNED in codes

    def test_banned_controller_denies(self, policy_with, tmp_bans):
        tmp_bans.add(Ban(address="rSignerX", reason="bad actor", added_by="sec"))
        rec = _record(controllers=["rSUBJ", "rSignerX"])
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CONTROLLER_BANNED in codes

    def test_nonbanned_controller_allows(self, policy_with, tmp_bans):
        rec = _record(controllers=["rSUBJ", "rSignerY"])
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True

    def test_expired_ban_does_not_deny(self, policy_with, tmp_bans):
        tmp_bans.add(Ban(
            address="rSUBJ",
            reason="old issue",
            added_by="sec",
            expires_at="2020-01-01T00:00:00+00:00",
        ))
        rec = _record()
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True


# ---------------------------------------------------------------------------
# Required-credential denial
# ---------------------------------------------------------------------------

class TestRequiredCredential:
    def test_missing_required_credential_denies(self, policy_with):
        rec = _record(creds=[])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.NO_CREDENTIALS in codes

    def test_having_required_credential_allows(self, policy_with):
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True

    def test_unaccepted_credential_denies(self, policy_with):
        # credential exists but subject hasn't accepted it
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=False)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_REVOKED in codes

    def test_wrong_issuer_denies(self, policy_with):
        # Cred is from rWRONG, but we require from rAUDIT
        rec = _record(creds=[_cred("rWRONG", "rSUBJ", b"KYC", accepted=True)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_MISSING in codes

    def test_any_issuer_matches_correct_type(self, policy_with):
        rec = _record(creds=[_cred("rANYONE", "rSUBJ", b"KYC", accepted=True)])
        pol = policy_with(rec)
        pol.require_credential(issuer=None, credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True

    def test_multiple_required_all_must_pass(self, policy_with):
        # Agent has only one of two required creds
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        pol.require_credential(issuer="rEVAL", credential_type=b"EVAL_PASS")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_MISSING in codes


# ---------------------------------------------------------------------------
# Denied-credential-type
# ---------------------------------------------------------------------------

class TestDeniedCredentialType:
    def test_holding_denied_type_denies(self, policy_with):
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"BANNED_TYPE", accepted=True)])
        pol = policy_with(rec)
        pol.deny_credential_type(b"BANNED_TYPE")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_TYPE_DENIED in codes

    def test_not_holding_denied_type_allows(self, policy_with):
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"OTHER_TYPE", accepted=True)])
        pol = policy_with(rec)
        pol.deny_credential_type(b"BANNED_TYPE")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True


# ---------------------------------------------------------------------------
# Decision output shape
# ---------------------------------------------------------------------------

class TestDecisionShape:
    def test_deny_reasons_property_filters_out_ok(self, policy_with, tmp_bans):
        tmp_bans.add(Ban(address="rSUBJ", reason="bad", added_by="sec"))
        rec = _record()
        pol = policy_with(rec, bans=tmp_bans)
        decision = pol.evaluate("rSUBJ")
        # Should have AGENT_BANNED deny reason, no OK mixed in.
        for r in decision.deny_reasons:
            assert r.code != ReasonCode.OK

    def test_summary_allow_form(self, policy_with):
        rec = _record()
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.summary().startswith("✓ ALLOW")

    def test_summary_deny_form_includes_codes(self, policy_with):
        rec = _record(creds=[])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.summary().startswith("✗ DENY")
        assert "NO_CREDENTIALS" in decision.summary()

    def test_to_json_serializable(self, policy_with):
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ", context=RequestContext(resource="/api/x"))
        import json
        j = decision.to_json()
        parsed = json.loads(j)
        assert parsed["allow"] is True
        assert parsed["request"]["resource"] == "/api/x"

    def test_request_context_auto_filled(self, policy_with):
        rec = _record()
        pol = policy_with(rec)
        decision = pol.evaluate("rSUBJ")
        assert decision.request is not None
        assert decision.request.request_id.startswith("req-")
        assert decision.request.requested_at.endswith("+00:00") or "T" in decision.request.requested_at


# ---------------------------------------------------------------------------
# Fluent rule construction
# ---------------------------------------------------------------------------

class TestFluentRules:
    def test_require_returns_self_for_chaining(self, policy_with):
        rec = _record()
        pol = policy_with(rec)
        result = pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        assert result is pol

    def test_deny_returns_self_for_chaining(self, policy_with):
        rec = _record()
        pol = policy_with(rec)
        result = pol.deny_credential_type(b"BAD")
        assert result is pol


# ---------------------------------------------------------------------------
# Banned-agent persistence (BannedAgentRegistry)
# ---------------------------------------------------------------------------

class TestBannedPersistence:
    def test_save_and_reload_roundtrip(self, tmp_path):
        path = tmp_path / "b.json"
        b1 = BannedAgentRegistry(path=path)
        b1.load()
        b1.add(Ban(address="rA", reason="one", added_by="op"))
        b1.add(Ban(address="rB", reason="two", added_by="op", reference="INC-1"))
        # New instance pointing at same path
        b2 = BannedAgentRegistry(path=path)
        b2.load()
        assert b2.is_banned("rA")
        assert b2.is_banned("rB")
        assert b2.get("rB").reference == "INC-1"

    def test_add_replaces_existing(self, tmp_path):
        path = tmp_path / "b.json"
        b = BannedAgentRegistry(path=path)
        b.load()
        b.add(Ban(address="rA", reason="first", added_by="op"))
        b.add(Ban(address="rA", reason="second", added_by="op"))
        assert b.get("rA").reason == "second"
        assert len(b) == 1

    def test_remove_returns_true_when_existed(self, tmp_path):
        path = tmp_path / "b.json"
        b = BannedAgentRegistry(path=path)
        b.load()
        b.add(Ban(address="rA", reason="x", added_by="op"))
        assert b.remove("rA") is True
        assert b.remove("rA") is False

    def test_ban_requires_reason(self):
        with pytest.raises(ValueError, match="reason is required"):
            Ban(address="rA", reason="")

    def test_ban_requires_target(self):
        with pytest.raises(ValueError, match="must specify address or controller"):
            Ban(reason="x")

    def test_ban_rejects_both_targets(self):
        with pytest.raises(ValueError, match="exactly one"):
            Ban(address="rA", controller_address="rB", reason="x")

    def test_ban_max_reason_length(self):
        with pytest.raises(ValueError, match="512 chars"):
            Ban(address="rA", reason="x" * 513)


# ---------------------------------------------------------------------------
# Credential expiry (XLS-70 expiration enforcement — v0.4.0 fix)
# ---------------------------------------------------------------------------
# These tests pin the behavior described in docs/MAINNET_DECISION.md §3.
# Before this fix, CREDENTIAL_EXPIRED was a defined ReasonCode that nothing
# ever emitted — expired credentials silently passed trust checks.

class TestCredentialExpiry:
    """Enforcement of XLS-70 `expiration` field at decision time.

    XLS-70 stores expiration as Ripple epoch seconds (XRPL_EPOCH = 946684800).
    The library translates "now" to Ripple epoch at decision time and emits
    CREDENTIAL_EXPIRED when cred.expiration < now.
    """

    def test_expired_credential_denies(self, policy_with):
        # expiration in the past (Ripple epoch = 1 second after XRPL_EPOCH)
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True, expiration=1)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_EXPIRED in codes
        # And it should NOT be silently treated as OK or as missing/revoked
        assert ReasonCode.OK not in codes
        assert ReasonCode.CREDENTIAL_MISSING not in codes

    def test_not_yet_expired_credential_allows(self, policy_with):
        # expiration far in the future (Ripple epoch ~ year 2050)
        future_expiry = 2_500_000_000  # well past current Ripple epoch
        rec = _record(
            creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True, expiration=future_expiry)]
        )
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True

    def test_no_expiration_credential_allows(self, policy_with):
        # No expiration set — same as the original happy path
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True, expiration=None)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True

    def test_expired_takes_precedence_over_unaccepted(self, policy_with):
        # Both flags true — should fire EXPIRED, not REVOKED.
        # (Expiry is the more specific signal: "credential is dead".)
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=False, expiration=1)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is False
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_EXPIRED in codes
        assert ReasonCode.CREDENTIAL_REVOKED not in codes

    def test_expired_credential_without_require_rule_does_not_fire(self, policy_with):
        # If the policy doesn't require this credential, expiry is irrelevant
        # to the decision (we don't pre-emptively deny on expired creds unless
        # they're in a require rule).
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True, expiration=1)])
        pol = policy_with(rec)
        # No require_credential — empty policy
        decision = pol.evaluate("rSUBJ")
        assert decision.allow is True
        codes = [r.code for r in decision.reasons]
        assert ReasonCode.CREDENTIAL_EXPIRED not in codes

    def test_expired_credential_reason_includes_timestamps(self, policy_with):
        rec = _record(creds=[_cred("rAUDIT", "rSUBJ", b"KYC", accepted=True, expiration=42)])
        pol = policy_with(rec)
        pol.require_credential(issuer="rAUDIT", credential_type=b"KYC")
        decision = pol.evaluate("rSUBJ")
        expired_reasons = [r for r in decision.reasons if r.code == ReasonCode.CREDENTIAL_EXPIRED]
        assert len(expired_reasons) == 1
        r = expired_reasons[0]
        assert r.credential_type == b"KYC"
        assert r.issuer == "rAUDIT"
        assert "42" in r.detail  # expiration value present in detail
        assert "Ripple epoch" in r.detail
