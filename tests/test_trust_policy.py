"""Trust policy ergonomics and correctness.

Covers the fixes made in v0.5.2:

  - a require rule with a set of issuers is reported once, not once per
    issuer (an agent holding a credential from ANY issuer in the set
    satisfies the rule)
  - an issuer-agnostic require rule is answerable from the ledger
  - credential_type accepts str as well as bytes
  - a ban rule denies an agent regardless of what it holds
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from xrpl_agent_id.trust import TrustPolicy
from xrpl_agent_id.trust import TrustRegistry


# ------------------------------------------------------- credential_type


def test_credential_type_accepts_str():
    policy = TrustPolicy(issuers={"rAAA"}, credential_type="https://x/auth")
    assert policy.credential_type == b"https://x/auth"
    assert policy.credential_type_bytes == b"https://x/auth"


def test_credential_type_accepts_bytes_unchanged():
    policy = TrustPolicy(issuers={"rAAA"}, credential_type=b"https://x/auth")
    assert policy.credential_type == b"https://x/auth"


def test_credential_type_none_stays_none():
    policy = TrustPolicy(issuers={"rAAA"}, credential_type=None)
    assert policy.credential_type is None
    assert policy.credential_type_bytes is None


def test_credential_type_rejects_other_types():
    with pytest.raises(TypeError):
        TrustPolicy(issuers={"rAAA"}, credential_type=123)


def test_require_accepts_str_credential_type():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer="rAAA", credential_type="https://x/auth")
    assert registry._rules[0].credential_type == b"https://x/auth"


def test_deny_accepts_str_credential_type():
    registry = TrustRegistry(network="testnet")
    registry.deny(issuer="rAAA", credential_type="https://x/auth")
    assert registry._rules[0].credential_type == b"https://x/auth"


# ------------------------------------------------------- issuer grouping


def test_require_with_issuer_set_creates_one_rule():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer={"rAAA", "rBBB", "rCCC"}, credential_type="https://x/auth")
    assert len(registry._rules) == 1
    assert registry._rules[0].issuers == {"rAAA", "rBBB", "rCCC"}


def test_missing_required_reports_one_entry_per_rule_not_per_issuer():
    """An unsatisfied rule with three issuers is one missing requirement.

    Reporting three entries would read as though the agent needed a
    credential from every issuer, when one from any of them satisfies it.
    """
    registry = TrustRegistry(network="testnet")
    registry.require(issuer={"rAAA", "rBBB", "rCCC"}, credential_type="https://x/auth")

    with patch.object(
        TrustRegistry, "_rule_satisfied", return_value=False
    ), patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        result = registry.check("did:xrpl:2:rSubject")

    assert len(result.missing_required) == 1
    issuer, ctype = result.missing_required[0]
    assert ctype == b"https://x/auth"
    assert issuer in {"rAAA", "rBBB", "rCCC"}


def test_missing_required_has_one_entry_per_distinct_rule():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer={"rAAA", "rBBB"}, credential_type="https://x/one")
    registry.require(issuer={"rCCC"}, credential_type="https://x/two")

    with patch.object(
        TrustRegistry, "_rule_satisfied", return_value=False
    ), patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        result = registry.check("did:xrpl:2:rSubject")

    assert len(result.missing_required) == 2


# ------------------------------------------- issuer-agnostic require rule


def test_issuer_agnostic_require_is_satisfied_by_a_held_credential():
    """A require rule with no issuer set is answered from the ledger.

    Before this fix the issuer-agnostic branch returned False
    unconditionally, so the rule could never pass.
    """
    registry = TrustRegistry(network="testnet")
    registry.require(issuer=None, credential_type="https://x/auth")

    with patch.object(
        TrustRegistry,
        "_get_agent_credentials",
        return_value=[("rSomeIssuer", b"https://x/auth")],
    ):
        assert registry._rule_satisfied(None, "rSubject", registry._rules[0]) is True


def test_issuer_agnostic_require_fails_when_type_not_held():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer=None, credential_type="https://x/auth")

    with patch.object(
        TrustRegistry,
        "_get_agent_credentials",
        return_value=[("rSomeIssuer", b"https://x/other")],
    ):
        assert registry._rule_satisfied(None, "rSubject", registry._rules[0]) is False


def test_require_any_credential_is_satisfied_when_any_is_held():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer=None, credential_type=None)

    with patch.object(
        TrustRegistry, "_get_agent_credentials", return_value=[("rI", b"anything")]
    ):
        assert registry._rule_satisfied(None, "rSubject", registry._rules[0]) is True


# ------------------------------------------------------------ ban rules


def test_ban_creates_a_ban_rule():
    registry = TrustRegistry(network="testnet")
    registry.ban("rBadActor", description="credential stuffing")
    rule = registry._rules[0]
    assert rule.action == "ban"
    assert rule.issuers == {"rBadActor"}
    assert rule.description == "credential stuffing"


def test_ban_matches_on_the_subject_regardless_of_credentials():
    policy = TrustPolicy(issuers={"rBAD"}, credential_type=None, action="ban")
    assert policy.matches("rBAD", b"anything") is True
    assert policy.matches("rGOOD", b"anything") is False


def test_banned_agent_is_denied_even_when_it_holds_credentials():
    registry = TrustRegistry(network="testnet")
    registry.ban("rSubject", description="banned by policy")

    with patch.object(
        TrustRegistry,
        "_get_agent_credentials",
        return_value=[("rIssuer", b"https://x/auth")],
    ):
        result = registry.check("did:xrpl:2:rSubject")

    assert result.satisfied is False
    assert result.banned is True
    assert result.ban_reason == "banned by policy"


def test_ban_is_evaluated_before_require_rules():
    registry = TrustRegistry(network="testnet")
    registry.require(issuer="rIssuer", credential_type="https://x/auth")
    registry.ban("rSubject")

    with patch.object(
        TrustRegistry, "_rule_satisfied", return_value=False
    ), patch.object(
        TrustRegistry, "_get_agent_credentials", return_value=[]
    ):
        result = registry.check("did:xrpl:2:rSubject")

    assert result.banned is True
    assert result.satisfied is False


def test_ban_of_a_different_agent_does_not_apply():
    registry = TrustRegistry(network="testnet")
    registry.ban("rSomeoneElse")

    with patch.object(
        TrustRegistry, "_rule_satisfied", return_value=True
    ), patch.object(
        TrustRegistry, "_get_agent_credentials", return_value=[("rI", b"t")]
    ):
        result = registry.check("did:xrpl:2:rSubject")

    assert result.banned is False


def test_summary_mentions_a_ban():
    registry = TrustRegistry(network="testnet")
    registry.ban("rSubject", description="fraud")

    with patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        result = registry.check("did:xrpl:2:rSubject")

    assert "BANNED" in result.summary()
    assert "fraud" in result.summary()

def test_ban_accepts_a_did_and_still_matches():
    """A ban written as a DID must actually deny the agent.

    The check compares against the subject's address. A DID-form ban
    that was not reduced to an address would silently never match, and
    the agent would be allowed through.
    """
    address = "rSubjectAddressXXXXXXXXXXXXXXXXXXXX"
    registry = TrustRegistry(network="testnet")
    registry.ban("did:xrpl:2:" + address, description="banned by DID")

    # The rule stores the reduced address, so it compares like-for-like.
    assert registry._rules[0].issuers == {address}

    with patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        result = registry.check("did:xrpl:2:" + address)

    assert result.banned is True
    assert result.ban_reason == "banned by DID"


def test_ban_accepts_a_bare_address():
    address = "rSubjectAddressXXXXXXXXXXXXXXXXXXXX"
    registry = TrustRegistry(network="testnet")
    registry.ban(address, description="banned by address")

    assert registry._rules[0].issuers == {address}

    with patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        result = registry.check("did:xrpl:2:" + address)

    assert result.banned is True


def test_ban_accepts_a_mixed_set_of_forms():
    a = "rAddressXXXXXXXXXXXXXXXXXXXXXXXXXX"
    b = "rOtherAddressXXXXXXXXXXXXXXXXXXXXXXXX"
    registry = TrustRegistry(network="testnet")
    registry.ban([a, "did:xrpl:2:" + b], description="mixed")

    assert registry._rules[0].issuers == {a, b}

    with patch.object(TrustRegistry, "_get_agent_credentials", return_value=[]):
        assert registry.check("did:xrpl:2:" + b).banned is True
        assert registry.check("did:xrpl:2:" + a).banned is True
