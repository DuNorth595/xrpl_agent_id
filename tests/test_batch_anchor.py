# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for batch anchoring -- the cheap on-chain audit path.

One transaction per *batch* instead of one per decision. That only
works if:
  - the batch is anchored in a single transaction;
  - every decision in it is provably included;
  - a second call does not re-anchor the same decisions (which would
    spend a second fee on them);
  - the audit database created before the batch-anchor columns existed
    still works.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from xrpl_agent_id.audit import AuditLog, XRPLMirror
from xrpl_agent_id.authorization import (
    AuthorizationDecision,
    AuthorizationReason,
    ReasonCode,
)
from xrpl_agent_id.merkle import merkle_root, verify_inclusion


def _decision(allow: bool = True, agent: str = "did:xrpl:2:rTest") -> AuthorizationDecision:
    reasons = [
        AuthorizationReason(
            code=ReasonCode.OK if allow else ReasonCode.CREDENTIAL_MISSING,
            detail="ok" if allow else "no credential",
        )
    ]
    return AuthorizationDecision(
        agent_did=agent, allow=allow, reasons=reasons
    )


class _FakeProvider:
    classic_address = "rMirrorAddress"
    public_key = "ED" + "00" * 32


class _RecordingMirror(XRPLMirror):
    """A mirror that records batches instead of hitting the ledger."""

    def __init__(self) -> None:
        super().__init__(provider=_FakeProvider(), network="testnet")
        self.batches: list[list[str]] = []
        self.counter = 0

    def submit_batch(self, decision_ids: list[str], *, extra: Any = None) -> str:
        self.batches.append(list(decision_ids))
        self.counter += 1
        return f"BATCH_TX_{self.counter}"

    def submit(self, decision: Any, extra: Any = None) -> str:
        raise AssertionError("per-decision mirroring should not happen in these tests")


# -----------------------------------------------------------------------
# anchor_batch
# -----------------------------------------------------------------------


def test_anchor_batch_anchors_pending_decisions(tmp_path) -> None:
    """One call, one transaction, all pending decisions."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)

    for i in range(5):
        log.record(_decision(allow=True, agent=f"did:xrpl:2:rAgent{i}"))

    out = log.anchor_batch()
    assert out["tx_hash"] == "BATCH_TX_1"
    assert out["count"] == 5
    assert len(mirror.batches) == 1, "must be ONE transaction for the batch"
    assert len(mirror.batches[0]) == 5


def test_anchor_batch_is_idempotent(tmp_path) -> None:
    """A second call must not re-anchor the same decisions.

    Re-anchoring would spend a second fee on decisions that are
    already anchored -- exactly the fee-stacking this design exists to
    avoid.
    """
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    for i in range(3):
        log.record(_decision(agent=f"did:xrpl:2:rAgent{i}"))

    log.anchor_batch()
    out2 = log.anchor_batch()
    assert out2["tx_hash"] is None
    assert out2["count"] == 0
    assert len(mirror.batches) == 1


def test_anchor_batch_with_nothing_pending(tmp_path) -> None:
    """No pending decisions -> no transaction."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    out = log.anchor_batch()
    assert out["tx_hash"] is None
    assert out["count"] == 0
    assert mirror.batches == []


def test_anchor_batch_without_a_mirror(tmp_path) -> None:
    """No mirror configured -> no transaction, and says why."""
    log = AuditLog(db_path=tmp_path / "a.db")
    log.record(_decision())
    out = log.anchor_batch()
    assert out["tx_hash"] is None
    assert "no mirror" in (out.get("skipped") or "")


def test_anchor_batch_respects_limit(tmp_path) -> None:
    """The limit caps the batch size."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    for i in range(10):
        log.record(_decision(agent=f"did:xrpl:2:rAgent{i}"))

    out = log.anchor_batch(limit=4)
    assert out["count"] == 4
    assert len(mirror.batches[0]) == 4

    # The remaining six are still pending and can be anchored next.
    out2 = log.anchor_batch()
    assert out2["count"] == 6


# -----------------------------------------------------------------------
# Inclusion proofs -- the reason batching is legitimate
# -----------------------------------------------------------------------


def test_every_decision_in_a_batch_has_a_verifiable_proof(tmp_path) -> None:
    """Every anchored decision proves inclusion against the root."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    decisions = [_decision(agent=f"did:xrpl:2:rAgent{i}") for i in range(7)]
    for d in decisions:
        log.record(d)

    out = log.anchor_batch()
    root = out["root"]
    assert root == merkle_root(out["decision_ids"])

    for did in out["decision_ids"]:
        proof_info = log.batch_proof(did)
        assert proof_info is not None, f"no proof for {did}"
        assert proof_info["root"] == root
        assert proof_info["tx_hash"] == "BATCH_TX_1"
        assert verify_inclusion(did, proof_info["proof"], root), (
            "inclusion proof must verify"
        )


def test_proof_fails_for_an_unanchored_decision(tmp_path) -> None:
    """A decision that was never anchored has no proof."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    log.record(_decision(agent="did:xrpl:2:rAnchored"))
    log.anchor_batch()

    log.record(_decision(agent="did:xrpl:2:rNotAnchored"))
    row = log._conn.execute(
        "SELECT decision_id FROM auth_decisions WHERE agent_did = ?",
        ("did:xrpl:2:rNotAnchored",),
    ).fetchone()
    assert log.batch_proof(row["decision_id"]) is None


def test_batch_proof_for_unknown_decision(tmp_path) -> None:
    """An unknown decision id returns None rather than raising."""
    log = AuditLog(db_path=tmp_path / "a.db")
    assert log.batch_proof("DEADBEEF") is None


def test_anchor_root_is_stored_on_the_row(tmp_path) -> None:
    """The root and index are recorded so a proof can be rebuilt."""
    mirror = _RecordingMirror()
    log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror)
    for i in range(3):
        log.record(_decision(agent=f"did:xrpl:2:rAgent{i}"))
    out = log.anchor_batch()

    row = log._conn.execute(
        "SELECT anchor_root, anchor_index, mirrored_tx FROM auth_decisions "
        "WHERE id = 1"
    ).fetchone()
    assert row["anchor_root"] == out["root"]
    assert row["anchor_index"] == 0
    assert row["mirrored_tx"] == "BATCH_TX_1"


# -----------------------------------------------------------------------
# The batch memo payload
# -----------------------------------------------------------------------


def test_batch_memo_payload_shape() -> None:
    """The memo carries the root, the count, and the app tag.

    It must fit in an XRPL memo -- 256 bytes of payload -- so a large
    batch still costs exactly one transaction of one fee.
    """
    from unittest.mock import patch

    captured: dict[str, Any] = {}

    class _FakeResp:
        result = {"hash": "BATCHHASH"}

    def _fake_submit_with_retry(tx, client, wallet, **kwargs):
        captured["tx"] = tx
        captured["kwargs"] = kwargs
        return {"hash": "BATCHHASH"}

    mirror = XRPLMirror(provider=_FakeProvider(), network="testnet")
    ids = [f"{i:064X}" for i in range(1000)]

    with patch("xrpl_agent_id.tx_submit.submit_with_retry", _fake_submit_with_retry):
        h = mirror.submit_batch(ids)

    assert h == "BATCHHASH"
    memo = captured["tx"].memos[0]
    payload = json.loads(bytes.fromhex(memo.memo_data).decode("utf-8"))
    assert payload["kind"] == "batch"
    assert payload["root"] == merkle_root(ids)
    assert payload["count"] == 1000
    assert payload["first"] == ids[0]
    assert payload["last"] == ids[-1]
    # The whole payload must fit the memo limit. rippled's STTx.cpp
    # checks the *serialized total* of all memos in a transaction
    # against 1024 bytes ("The memo exceeds the maximum allowed size.").
    assert len(memo.memo_data) // 2 <= 1024, "memo payload exceeds the limit"


def test_batch_memo_forwards_the_fee_ceiling() -> None:
    """The batch path honours the mirror's fee ceiling too."""
    from unittest.mock import patch

    captured: dict[str, Any] = {}

    def _fake_submit_with_retry(tx, client, wallet, **kwargs):
        captured.update(kwargs)
        return {"hash": "H"}

    mirror = XRPLMirror(
        provider=_FakeProvider(), network="testnet", max_fee_drops=25_000
    )
    with patch("xrpl_agent_id.tx_submit.submit_with_retry", _fake_submit_with_retry):
        mirror.submit_batch([f"{i:064X}" for i in range(3)])

    assert captured.get("max_fee_drops") == 25_000


def test_submit_batch_rejects_an_empty_batch() -> None:
    """An empty batch is a bug, not a no-op."""
    mirror = XRPLMirror(provider=_FakeProvider(), network="testnet")
    with pytest.raises(ValueError):
        mirror.submit_batch([])


# -----------------------------------------------------------------------
# Migration -- an audit database created before the columns existed
# -----------------------------------------------------------------------


def test_existing_database_gets_the_new_columns(tmp_path) -> None:
    """An audit DB created before the batch-anchor columns still works.

    `CREATE TABLE IF NOT EXISTS` means an old database keeps its old
    schema. Opening it must add the new columns, or the first batch
    anchor would fail with "no such column".
    """
    db = tmp_path / "old.db"

    # Recreate the pre-batch-anchor schema exactly.
    legacy = sqlite3.connect(str(db))
    legacy.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_decisions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            decided_at    INTEGER NOT NULL,
            request_id    TEXT,
            agent_did     TEXT NOT NULL,
            allow         INTEGER NOT NULL,
            reasons_json  TEXT NOT NULL,
            summary       TEXT NOT NULL,
            request_json  TEXT,
            agent_record  TEXT,
            extra_json    TEXT,
            mirrored_tx   TEXT,
            decision_id   TEXT
        )
        """
    )
    legacy.commit()
    legacy.close()

    # Opening it must migrate it.
    mirror = _RecordingMirror()
    log = AuditLog(db_path=db, xrpl_mirror=mirror)
    log.record(_decision())
    out = log.anchor_batch()
    assert out["tx_hash"] == "BATCH_TX_1"
    assert out["count"] == 1

    # And the columns are there.
    cols = {
        r[1] for r in log._conn.execute("PRAGMA table_info(auth_decisions)")
    }
    assert "anchor_root" in cols
    assert "anchor_index" in cols


def test_migration_is_idempotent(tmp_path) -> None:
    """Opening the same database twice must not fail."""
    db = tmp_path / "twice.db"
    AuditLog(db_path=db)
    AuditLog(db_path=db)
    AuditLog(db_path=db)
    log = AuditLog(db_path=db)
    cols = [r[1] for r in log._conn.execute("PRAGMA table_info(auth_decisions)")]
    assert cols.count("anchor_root") == 1
