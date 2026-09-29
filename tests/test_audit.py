# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the AuditLog layer."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from xrpl_agent_id.audit import AuditLog, XRPLMirror
from xrpl_agent_id.authorization import (
    AuthorizationDecision,
    AuthorizationReason,
    ReasonCode,
    RequestContext,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _decision(allow=True, agent="did:xrpl:2:rSUBJ", reasons=None) -> AuthorizationDecision:
    if reasons is None:
        reasons = [AuthorizationReason(code=ReasonCode.OK, detail="ok")]
    return AuthorizationDecision(
        agent_did=agent,
        allow=allow,
        reasons=reasons,
        request=RequestContext(request_id="req-1", resource="/api/test"),
        agent_record_summary=f"fake {agent}",
    )


@pytest.fixture
def log(tmp_path):
    db = tmp_path / "audit.db"
    l = AuditLog(db_path=db)
    yield l
    l.close()


# ---------------------------------------------------------------------------
# Insert / query
# ---------------------------------------------------------------------------

class TestAuditLogRecord:
    def test_record_returns_row_id(self, log):
        d = _decision()
        rid = log.record(d)
        assert isinstance(rid, int)
        assert rid > 0

    def test_recorded_decision_queryable(self, log):
        d = _decision(allow=False)
        log.record(d, extra={"source": "test"})
        recs = log.query()
        assert len(recs) == 1
        assert recs[0].allow is False
        assert recs[0].agent_did == "did:xrpl:2:rSUBJ"
        assert recs[0].extra() == {"source": "test"}

    def test_reasons_roundtrip(self, log):
        reasons = [
            AuthorizationReason(code=ReasonCode.AGENT_BANNED, detail="bad actor"),
            AuthorizationReason(code=ReasonCode.CREDENTIAL_MISSING, detail="no creds", issuer="rAUDIT", credential_type=b"KYC"),
        ]
        d = _decision(allow=False, reasons=reasons)
        log.record(d)
        recs = log.query()
        loaded = recs[0].reasons()
        assert len(loaded) == 2
        assert loaded[0]["code"] == "AGENT_BANNED"
        assert loaded[1]["code"] == "CREDENTIAL_MISSING"
        assert loaded[1]["credential_type"] == b"KYC".hex().upper()

    def test_request_persisted(self, log):
        d = _decision()
        log.record(d)
        recs = log.query()
        req = recs[0].to_dict()["request"]
        assert req["request_id"] == "req-1"
        assert req["resource"] == "/api/test"


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------

class TestAuditLogQuery:
    def test_filter_by_agent(self, log):
        log.record(_decision(agent="did:xrpl:2:rA"))
        log.record(_decision(agent="did:xrpl:2:rB"))
        log.record(_decision(agent="did:xrpl:2:rA"))
        recs = log.query(agent_did="did:xrpl:2:rA")
        assert len(recs) == 2
        assert all(r.agent_did == "did:xrpl:2:rA" for r in recs)

    def test_filter_by_allow(self, log):
        log.record(_decision(allow=True))
        log.record(_decision(allow=False))
        log.record(_decision(allow=False))
        assert len(log.query(allow=True)) == 1
        assert len(log.query(allow=False)) == 2

    def test_filter_by_since(self, log):
        # Use unix seconds in the past to ensure no false matches
        long_ago = int(time.time()) - 10_000_000
        recent = int(time.time()) - 10
        log.record(_decision())
        recs = log.query(since=recent)
        assert len(recs) == 1
        recs = log.query(since=long_ago)
        assert len(recs) == 1  # one decision exists

    def test_limit(self, log):
        for i in range(5):
            log.record(_decision())
        recs = log.query(limit=3)
        assert len(recs) == 3

    def test_newest_first(self, log):
        ids = []
        for i in range(3):
            ids.append(log.record(_decision()))
        recs = log.query()
        assert [r.id for r in recs] == list(reversed(ids))


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestAuditLogStats:
    def test_stats_counts(self, log):
        log.record(_decision(allow=True))
        log.record(_decision(allow=True))
        log.record(_decision(allow=False))
        s = log.stats()
        assert s["total"] == 3
        assert s["allowed"] == 2
        assert s["denied"] == 1

    def test_empty_stats(self, log):
        s = log.stats()
        assert s == {"total": 0, "allowed": 0, "denied": 0}


# ---------------------------------------------------------------------------
# XRPL mirror (mocked)
# ---------------------------------------------------------------------------

class TestXRPLMirror:
    def test_mirror_called_on_deny(self, tmp_path):
        mirror = MagicMock(spec=XRPLMirror)
        mirror.submit.return_value = "DEADBEEF" * 8
        log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror, mirror_on_deny_only=True)
        try:
            log.record(_decision(allow=False))
            log.record(_decision(allow=True))
            # only deny triggered
            assert mirror.submit.call_count == 1
            # tx hash stored
            recs = log.query()
            denied = [r for r in recs if not r.allow]
            assert denied[0].mirrored_tx == "DEADBEEF" * 8
        finally:
            log.close()

    def test_mirror_called_on_all_when_flag_false(self, tmp_path):
        mirror = MagicMock(spec=XRPLMirror)
        mirror.submit.return_value = "AB" * 32
        log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror, mirror_on_deny_only=False)
        try:
            log.record(_decision(allow=True))
            log.record(_decision(allow=False))
            assert mirror.submit.call_count == 2
        finally:
            log.close()

    def test_mirror_failure_doesnt_lose_decision(self, tmp_path):
        mirror = MagicMock(spec=XRPLMirror)
        mirror.submit.side_effect = RuntimeError("ledger unavailable")
        log = AuditLog(db_path=tmp_path / "a.db", xrpl_mirror=mirror, mirror_on_deny_only=False)
        try:
            rid = log.record(_decision(allow=True))
            recs = log.query()
            assert len(recs) == 1  # decision still recorded
            assert recs[0].mirrored_tx is None  # mirror_tx was None on failure
            # Error surfaced in extra_json
            assert "mirror_error" in recs[0].extra()
        finally:
            log.close()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestAuditPersistence:
    def test_records_survive_reopen(self, tmp_path):
        path = tmp_path / "a.db"
        l1 = AuditLog(db_path=path)
        l1.record(_decision(allow=True))
        l1.record(_decision(allow=False))
        l1.close()
        # Reopen
        l2 = AuditLog(db_path=path)
        try:
            assert l2.stats()["total"] == 2
        finally:
            l2.close()
