# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the fee ceiling on the signing/submission path.

XRPL's open-ledger cost rises *exponentially* once the open ledger passes
its soft limit. Without a ceiling, a transaction signed during congestion
can cost many times the median fee -- and for the audit mirror, whose
denial path is attacker-reachable, that is a griefing vector.

These tests cover:
  - the ceiling refuses to sign above the limit
  - the ceiling is enforced against the *autofilled* fee
  - a refusal is never retried (it is a decision, not a transient error)
  - a refusal is *recorded* on the audit row, never silently dropped
  - the mirror accepts and passes through a ceiling
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from xrpl_agent_id.audit import AuditLog, XRPLMirror
from xrpl_agent_id.authorization import (
    AuthorizationDecision,
    AuthorizationReason,
    ReasonCode,
)
from xrpl_agent_id.tx_submit import (
    FeeCeilingExceeded,
    _check_fee_ceiling,
    max_fee_drops_for_usd,
    submit_with_retry,
)


def _decision(allow: bool = False) -> AuthorizationDecision:
    """Build a decision the way the rest of the suite does."""
    reasons = [
        AuthorizationReason(
            code=ReasonCode.OK if allow else ReasonCode.CREDENTIAL_MISSING,
            detail="ok" if allow else "no credential",
        )
    ]
    return AuthorizationDecision(
        agent_did="did:xrpl:2:rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty",
        allow=allow,
        reasons=reasons,
    )


# -----------------------------------------------------------------------
# _check_fee_ceiling
# -----------------------------------------------------------------------


class _FakeTx:
    def __init__(self, fee: Any = None):
        self.fee = fee


def test_ceiling_none_disables_check() -> None:
    """No ceiling configured -> nothing raises."""
    _check_fee_ceiling(_FakeTx(fee=10_000_000), None)


def test_ceiling_allows_fee_below_limit() -> None:
    """A fee under the ceiling passes."""
    _check_fee_ceiling(_FakeTx(fee=5000), 10_000)


def test_ceiling_allows_fee_exactly_at_limit() -> None:
    """The ceiling is inclusive -- a fee exactly at the limit passes."""
    _check_fee_ceiling(_FakeTx(fee=10_000), 10_000)


def test_ceiling_raises_above_limit() -> None:
    """A fee above the ceiling raises."""
    with pytest.raises(FeeCeilingExceeded) as exc:
        _check_fee_ceiling(_FakeTx(fee=10_001), 10_000)
    assert "10001" in str(exc.value)
    assert "10000" in str(exc.value)


def test_ceiling_ignores_unset_fee() -> None:
    """A fee of None means 'not yet autofilled' -- autofill decides later."""
    _check_fee_ceiling(_FakeTx(fee=None), 10_000)


def test_ceiling_ignores_zero_fee() -> None:
    """A fee of 0 is 'unset' in XRPL terms, not a real zero-fee tx."""
    _check_fee_ceiling(_FakeTx(fee=0), 10_000)


# -----------------------------------------------------------------------
# submit_with_retry
# -----------------------------------------------------------------------


class _FakeProvider:
    """Minimal provider: has the three members the protocol requires."""

    def __init__(self) -> None:
        self.classic_address = "rProviderAddress"
        self.public_key = "ED" + "00" * 32
        self.signed: list[Any] = []

    def sign(self, tx: Any) -> Any:
        self.signed.append(tx)
        return tx


def test_submit_with_retry_raises_ceiling_before_signing(monkeypatch) -> None:
    """The ceiling is checked BEFORE the provider signs anything.

    This is the whole point: refusing to sign means the transaction never
    reaches the network, so no premium is ever paid.
    """
    from unittest.mock import patch

    provider = _FakeProvider()
    tx = _FakeTx(fee=50_000)

    class _FakeClient:
        pass

    # In provider mode _prepare_attempt calls xrpl.transaction.autofill.
    # Stub it to return the same tx so the fee check sees 50_000.
    with patch("xrpl.transaction.autofill", return_value=tx):
        with patch("xrpl.transaction.submit_and_wait") as submit:
            with pytest.raises(FeeCeilingExceeded):
                submit_with_retry(
                    tx,
                    _FakeClient(),
                    provider,
                    max_fee_drops=10_000,
                )
            submit.assert_not_called()

    assert provider.signed == [], "must not sign above the ceiling"


def test_submit_with_retry_does_not_retry_ceiling(monkeypatch) -> None:
    """A ceiling refusal is a decision, not a transient error.

    It must not be retried -- retrying would re-autofill and re-check,
    and if the network recovered, the tx would be signed at whatever fee
    was current, which is exactly the unbounded behaviour the ceiling
    exists to prevent.
    """
    from unittest.mock import patch

    provider = _FakeProvider()
    tx = _FakeTx(fee=50_000)
    calls = {"n": 0}

    def _fake_submit(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        raise AssertionError("submit_and_wait should not be reached")

    class _FakeClient:
        pass

    with patch("xrpl.transaction.autofill", return_value=tx):
        with patch("xrpl.transaction.submit_and_wait", _fake_submit):
            with pytest.raises(FeeCeilingExceeded):
                submit_with_retry(
                    tx,
                    _FakeClient(),
                    provider,
                    max_attempts=3,
                    max_fee_drops=10_000,
                )

    assert calls["n"] == 0, "must not attempt submission at all"


def test_submit_with_retry_without_ceiling_signs_normally(monkeypatch) -> None:
    """The default (no ceiling) path is unchanged."""
    from unittest.mock import patch

    provider = _FakeProvider()
    tx = _FakeTx(fee=50_000)

    class _FakeResp:
        result = {"hash": "ABC123"}

    class _FakeClient:
        pass

    with patch("xrpl.transaction.autofill", return_value=tx):
        with patch("xrpl.transaction.submit_and_wait", return_value=_FakeResp()):
            out = submit_with_retry(tx, _FakeClient(), provider)

    assert out == {"hash": "ABC123"}
    assert len(provider.signed) == 1


# -----------------------------------------------------------------------
# The mirror
# -----------------------------------------------------------------------


def test_mirror_stores_max_fee_drops() -> None:
    """The mirror holds a fee ceiling and exposes it."""
    mirror = XRPLMirror(
        provider=_FakeProvider(), network="testnet", max_fee_drops=25_000
    )
    assert mirror.max_fee_drops == 25_000


def test_mirror_default_has_no_ceiling() -> None:
    """Default is None -- the check is opt-in."""
    mirror = XRPLMirror(provider=_FakeProvider(), network="testnet")
    assert mirror.max_fee_drops is None


def test_mirror_passes_ceiling_to_submit(monkeypatch) -> None:
    """The mirror forwards its ceiling into submit_with_retry."""
    from unittest.mock import patch

    mirror = XRPLMirror(
        provider=_FakeProvider(), network="testnet", max_fee_drops=25_000
    )
    seen: dict[str, Any] = {}

    def _fake_submit_with_retry(tx, client, wallet, **kwargs):
        seen.update(kwargs)
        return {"hash": "HASH123"}

    with patch("xrpl_agent_id.tx_submit.submit_with_retry", _fake_submit_with_retry):
        # audit.py imports submit_with_retry *inside* submit(), so patching
        # the module attribute is the right seam.
        h = mirror.submit(_decision(allow=False))

    assert h == "HASH123"
    assert seen.get("max_fee_drops") == 25_000


# -----------------------------------------------------------------------
# The recorded gap -- a refusal must be visible, not silent
# -----------------------------------------------------------------------


class _CeilingMirror(XRPLMirror):
    """A real XRPLMirror whose submit always refuses on the fee ceiling.

    Subclasses XRPLMirror so it satisfies the type the AuditLog expects
    and so the fee-ceiling plumbing is exercised on the real path.
    """

    def __init__(self) -> None:
        super().__init__(
            provider=_FakeProvider(), network="testnet", max_fee_drops=10_000
        )
        self.calls = 0

    def submit(self, decision: Any, extra: Any = None) -> str:
        self.calls += 1
        raise FeeCeilingExceeded(
            "transaction fee 50000 drops exceeds the ceiling of 10000 drops"
        )


def test_fee_ceiling_refusal_is_recorded_not_silent(tmp_path) -> None:
    """A refusal is recorded on the audit row as an explicit gap.

    An auditor must be able to see that the decision exists AND that it
    could not be anchored. A silently-dropped mirror is worse than no
    mirror, because it looks like everything was anchored.
    """
    log = AuditLog(
        db_path=tmp_path / "audit.db",
        xrpl_mirror=_CeilingMirror(),
        mirror_on_deny_only=True,
    )
    decision = _decision(allow=False)
    row_id = log.record(decision)

    row = log._conn.execute(
        "SELECT mirrored_tx, extra_json FROM auth_decisions WHERE id = ?", (row_id,)
    ).fetchone()
    assert row is not None

    assert row["mirrored_tx"] is None, "must not claim a mirror tx"
    extra = json.loads(row["extra_json"] or "{}")
    assert extra.get("mirror_dropped") == "fee_ceiling_exceeded"
    assert "fee ceiling" in (extra.get("mirror_dropped_reason") or "")

    # The decision itself is still recorded.
    assert log.query()[0].allow is False


def test_ceiling_refusal_does_not_lose_the_decision(tmp_path) -> None:
    """A refusal must not lose the decision -- only the anchoring."""
    mirror = _CeilingMirror()
    log = AuditLog(
        db_path=tmp_path / "audit.db",
        xrpl_mirror=mirror,
        mirror_on_deny_only=True,
    )
    decision = _decision(allow=False)
    log.record(decision)

    assert mirror.calls == 1, "the mirror was attempted"
    rows = log.query()
    assert len(rows) == 1, "the decision is still in the log"
    assert rows[0].allow is False


# -----------------------------------------------------------------------
# max_fee_drops_for_usd -- the price-aware ceiling
# -----------------------------------------------------------------------


def test_usd_ceiling_at_current_price() -> None:
    """At $1.33/XRP, a $0.25 ceiling is ~188,000 drops."""
    drops = max_fee_drops_for_usd(0.25, 1.33)
    assert 180_000 < drops < 190_000
    # And it round-trips: the drops cost about the limit.
    assert drops * 1e-6 * 1.33 == pytest.approx(0.25, rel=0.01)


def test_usd_ceiling_shrinks_as_price_rises() -> None:
    """The same dollar ceiling is fewer drops when XRP is dearer.

    This is the whole point: a fixed drop ceiling is NOT a fixed
    dollar ceiling. At $1,000/XRP the same $0.25 limit is 250 drops,
    not 188,000.
    """
    cheap = max_fee_drops_for_usd(0.25, 1.33)
    dear = max_fee_drops_for_usd(0.25, 1000.0)
    assert dear < cheap
    assert dear == 250  # 0.25 / 1000 * 1e6


def test_usd_ceiling_holds_its_dollar_value_across_prices() -> None:
    """Whatever the price, the ceiling costs the same dollars."""
    for price in (0.50, 1.33, 20.0, 50.0, 500.0, 1000.0):
        drops = max_fee_drops_for_usd(1.00, price)
        cost_usd = drops * 1e-6 * price
        assert cost_usd == pytest.approx(1.00, rel=0.001), f"price {price}"


def test_usd_ceiling_rejects_bad_input() -> None:
    """Non-positive inputs are programming errors and say so."""
    with pytest.raises(ValueError):
        max_fee_drops_for_usd(0, 1.33)
    with pytest.raises(ValueError):
        max_fee_drops_for_usd(-1, 1.33)
    with pytest.raises(ValueError):
        max_fee_drops_for_usd(1.0, 0)
    with pytest.raises(ValueError):
        max_fee_drops_for_usd(1.0, -1.33)


def test_usd_ceiling_never_returns_zero() -> None:
    """A ceiling must be at least one drop, or it would block everything."""
    assert max_fee_drops_for_usd(0.0000001, 1000.0) >= 1
