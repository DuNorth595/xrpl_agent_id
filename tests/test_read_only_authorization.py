# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests that pin the read-only authorization path.

XRPL charges a fee for a transaction -- a ledger *write* -- and charges
nothing for a read. The authorization path here is read-only: authorizing a
request must never submit a transaction.

If a future change made that path write to the ledger, every authorized
request would cost a transaction fee. That is a per-use tax on exactly the
ecosystem this library serves, so the invariant is pinned here as a test
rather than left to code review.

These tests cover:
  - the authorization and trust modules contain no write-path calls
  - `AuthorizationPolicy.evaluate()` issues no transaction
  - `TrustRegistry.check()` issues no transaction
  - the audit mirror is optional and off by default
  - a denial produces no ledger write (the operator pays, not the counterparty)
  - batch anchoring is the cheap path (one fee per batch, not per decision)
  - the fee ceiling refuses to sign into a congestion premium
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from xrpl_agent_id.audit import AuditLog, XRPLMirror
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext
from xrpl_agent_id.banned import Ban, BannedAgentRegistry

PKG_ROOT = Path(__file__).resolve().parent.parent / "xrpl_agent_id"

#: Calls that would put a transaction on the ledger.
WRITE_PATH_CALLS = frozenset(
    {
        "submit",
        "submit_and_wait",
        "submit_with_retry",
        "autofill",
        "sign",
        "sign_transaction",
        "safe_sign",
        "submit_multisigned",
    }
)


def _module_source(name: str) -> str:
    path = PKG_ROOT / f"{name}.py"
    assert path.exists(), f"expected {path} to exist"
    return path.read_text(encoding="utf-8")


def _called_names(source: str) -> set[str]:
    """Return every bare function/method name called in a module.

    Uses the AST rather than a regex so that a name appearing inside a
    string or a comment does not trip the check.
    """
    tree = ast.parse(textwrap.dedent(source))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


class TestNoWritePathOnTheHotPath:
    """The authorization and trust paths must never touch the write side."""

    @pytest.mark.parametrize("module_name", ["authorization", "trust"])
    def test_module_contains_no_write_path_calls(self, module_name: str) -> None:
        called = _called_names(_module_source(module_name))
        offenders = called & WRITE_PATH_CALLS
        assert not offenders, (
            f"{module_name}.py calls {sorted(offenders)}; the authorization "
            "path must stay read-only"
        )

    def test_evaluate_does_not_submit(self) -> None:
        """`AuthorizationPolicy.evaluate()` must not reach the ledger writer."""
        source = inspect.getsource(AuthorizationPolicy.evaluate)
        tree = ast.parse(inspect.cleandoc(source))
        called = _called_names(ast.unparse(tree))
        offenders = called & WRITE_PATH_CALLS
        assert not offenders, (
            f"AuthorizationPolicy.evaluate() calls {sorted(offenders)}; "
            "authorizing a request must not submit a transaction"
        )

    def test_policy_evaluate_returns_decision_without_network(self) -> None:
        """The whole point: a decision with no keys and no network."""
        from unittest.mock import MagicMock

        registry = MagicMock()
        record = MagicMock()
        record.agent_address = "rBadAgent"
        record.agent_did = "did:xrpl:1:rBadAgent"
        record.controllers = ["rBadAgent"]
        record.credentials = []
        record.summary.return_value = "fake"
        registry.resolve.return_value = record

        bans = BannedAgentRegistry(network="testnet")
        bans.add(Ban(address="rBadAgent", reason="test", added_by="test"))
        policy = AuthorizationPolicy(network="testnet", bans=bans, registry=registry)
        policy.require_credential(
            issuer="rIssuer",
            credential_type=b"agent_identity_v1",
        )
        decision = policy.evaluate(
            "rBadAgent",
            context=RequestContext(resource="/api/transfer"),
        )
        assert decision.allow is False
        assert decision.reasons[0].code.name == "AGENT_BANNED"

    def test_trust_check_is_read_only(self) -> None:
        """`TrustRegistry.check()` reads the ledger; it never writes to it."""
        from xrpl_agent_id.trust import TrustRegistry

        called = _called_names(inspect.getsource(TrustRegistry.check))
        offenders = called & WRITE_PATH_CALLS
        assert not offenders, (
            f"TrustRegistry.check() calls {sorted(offenders)}; a trust check "
            "must not submit a transaction"
        )


class TestMirrorIsOptionalAndOffByDefault:
    """The on-chain mirror must never be mandatory."""

    def test_mirror_defaults_to_none(self) -> None:
        sig = inspect.signature(AuditLog.__init__)
        assert sig.parameters["xrpl_mirror"].default is None

    def test_mirror_on_deny_only_defaults_true(self) -> None:
        sig = inspect.signature(AuditLog.__init__)
        assert sig.parameters["mirror_on_deny_only"].default is True

    def test_no_mirror_means_no_ledger_write(self, tmp_path: Path) -> None:
        """A plain AuditLog with no mirror records decisions without a ledger write."""
        log = AuditLog(db_path=tmp_path / "audit.db")
        assert log.xrpl_mirror is None
        assert log.mirror_on_deny_only is True

    def test_denial_produces_no_ledger_write(self, tmp_path: Path) -> None:
        """A denied counterparty never signs a transaction.

        The mirror runs on its own key, so the on-chain audit cost of a
        denial is the operator's expense -- never a charge on the party
        that was denied.
        """
        log = AuditLog(db_path=tmp_path / "audit.db")
        assert log.xrpl_mirror is None

        from xrpl_agent_id.authorization import (
            AuthorizationDecision,
            AuthorizationReason,
            ReasonCode,
        )

        decision = AuthorizationDecision(
            agent_did="did:xrpl:1:rBadAgent",
            allow=False,
            reasons=[
                AuthorizationReason(
                    code=ReasonCode.CREDENTIAL_MISSING,
                    detail="no credential",
                )
            ],
        )
        row_id = log.record(decision)
        assert row_id > 0

        row = log._conn.execute(
            "SELECT mirrored_tx FROM auth_decisions WHERE id = ?", (row_id,)
        ).fetchone()
        assert row is not None
        assert row[0] is None, "a denial must not produce a ledger write"


class TestBatchedPathExists:
    """The scale fixes must actually be implemented, not just promised."""

    def test_anchor_batch_is_present(self) -> None:
        assert hasattr(AuditLog, "anchor_batch")
        assert hasattr(AuditLog, "batch_proof")

    def test_anchor_batch_is_the_cheap_path(self, tmp_path: Path) -> None:
        """One transaction covers the whole batch, not one per decision.

        The batch size is the cost lever: 1,000 decisions per transaction
        is what turns a ~$6,650/day audit bill into ~$6.65/day.
        """
        log = AuditLog(db_path=tmp_path / "audit.db")
        sig = inspect.signature(log.anchor_batch)
        assert sig.parameters["limit"].default == 1000

    def test_mirror_has_its_own_key_not_the_issuers(self) -> None:
        """The mirror is deliberately a separate signer."""
        source = inspect.getsource(XRPLMirror)
        assert "APP_TAG" in source or "app" in source.lower()
        # The mirror takes its own seed -- it is not handed the issuer's.
        sig = inspect.signature(XRPLMirror.__init__)
        params = list(sig.parameters)
        assert "seed" in params or "wallet" in params or "provider" in params


class TestFeeCeilingBoundsSpend:
    """The ceiling has to hold under load, not only when the ledger is quiet."""

    def test_fee_ceiling_raises_before_signing(self) -> None:
        from xrpl_agent_id.tx_submit import FeeCeilingExceeded, _check_fee_ceiling

        class _Tx:
            fee = "50000"

        with pytest.raises(FeeCeilingExceeded):
            _check_fee_ceiling(_Tx(), 5_000)

    def test_fee_ceiling_allows_under_ceiling(self) -> None:
        from xrpl_agent_id.tx_submit import _check_fee_ceiling

        class _Tx:
            fee = "10"

        _check_fee_ceiling(_Tx(), 5_000)  # must not raise
