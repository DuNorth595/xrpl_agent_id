# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the dashboard /api/verify endpoint.

Verifies the cross-link between on-chain XRPL audit memos and the local
SQLite audit log. Tests are hermetic — no live XRPL RPC calls. The
``tx_hash`` path is mocked at the ``_fetch_memo_from_tx`` boundary.

Run: /usr/bin/python3 tests/test_dashboard_verify.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

from xrpl_agent_id.dashboard import db, server as srv
from xrpl_agent_id.audit import XRPLMirror, compute_decision_id
from xrpl_agent_id.authorization import (
    AuthorizationDecision,
    AuthorizationReason,
    ReasonCode,
    RequestContext,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

def _make_decision(agent_did: str, allow: bool, reason_code: str | None = None) -> AuthorizationDecision:
    """Construct a deterministic AuthorizationDecision for tests."""
    if allow:
        return AuthorizationDecision(
            agent_did=agent_did,
            allow=True,
            reasons=[],
            evaluated_at="2026-09-29T01:53:27.000000+00:00",
            request=RequestContext(resource="/api/protected-live"),
        )
    return AuthorizationDecision(
        agent_did=agent_did,
        allow=False,
        reasons=[
            AuthorizationReason(
                code=ReasonCode(reason_code),
                detail=f"test {reason_code}",
                issuer=None,
                credential_type=None,
            )
        ],
        evaluated_at="2026-09-29T01:53:27.000000+00:00",
        request=RequestContext(resource="/api/protected-live"),
    )


def _seed_decision(conn, decision: AuthorizationDecision, mirrored_tx: str) -> str:
    """Insert a decision row + return the decision_id we stored."""
    decided_at = int(
        datetime.fromisoformat(decision.evaluated_at).timestamp()
    )
    did = compute_decision_id(decision)
    db.insert_auth_decision(
        conn,
        decided_at=decided_at,
        request_id=None,
        agent_did=decision.agent_did,
        allow=decision.allow,
        reasons_json=json.dumps([r.to_dict() for r in decision.reasons]),
        summary=str(decision),
        request_json=json.dumps(decision.request.__dict__) if decision.request else None,
        agent_record=None,
        extra_json=json.dumps({"role": "test", "mode": "sim"}),
        mirrored_tx=mirrored_tx,
        decision_id=did,
    )
    return did


def _memo_hex_for(decision: AuthorizationDecision) -> str:
    """Build the on-chain memo hex for a decision (mirrors XRPLMirror.submit)."""
    did = compute_decision_id(decision)
    payload = {
        "app": XRPLMirror.APP_TAG,
        "v": XRPLMirror.SCHEMA_VERSION,
        "decision_id": did,
        "allow": decision.allow,
        "agent": decision.agent_did,
        "ts": decision.evaluated_at,
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8").hex().upper()


# -----------------------------------------------------------------------
# Unit tests for the helpers
# -----------------------------------------------------------------------

def test_decode_memo_hex_happy_path() -> None:
    decision = _make_decision(
        "did:xrpl:2:rABC", False, "CONTROLLER_BANNED"
    )
    hex_blob = _memo_hex_for(decision)
    out = srv._decode_memo_hex(hex_blob)
    assert out["app"] == XRPLMirror.APP_TAG
    assert out["decision_id"] == compute_decision_id(decision)
    assert out["allow"] is False
    assert out["agent"] == "did:xrpl:2:rABC"


def test_decode_memo_hex_strips_0x_and_quotes() -> None:
    decision = _make_decision("did:xrpl:2:rZ", True)
    hex_blob = _memo_hex_for(decision)
    # wrap like a URL-encoded form value
    wrapped = '"' + "0x" + hex_blob.lower() + '"'
    out = srv._decode_memo_hex(wrapped)
    assert out["decision_id"] == compute_decision_id(decision)


def test_decode_memo_hex_bad_hex() -> None:
    try:
        srv._decode_memo_hex("not-hex-at-all!!")
    except ValueError:
        return
    raise AssertionError("expected ValueError on bad hex")


def test_decode_memo_hex_non_utf8() -> None:
    # 0xFF is invalid UTF-8 start byte
    try:
        srv._decode_memo_hex("FF")
    except (ValueError, UnicodeDecodeError):
        return
    raise AssertionError("expected decode error on non-UTF-8 bytes")


# -----------------------------------------------------------------------
# api_verify — direct function tests (no HTTP layer)
# -----------------------------------------------------------------------

def _api_verify_with_db(decision: AuthorizationDecision | None = None, mirrored_tx: str | None = None):
    """Spin up a temp DB, optionally seed a decision, return (tmp, conn, decision, decision_id, memo_hex)."""
    tmp = tempfile.TemporaryDirectory()
    conn = db.open_db(Path(tmp.name) / "v.db")
    if decision is not None and mirrored_tx is not None:
        did = _seed_decision(conn, decision, mirrored_tx)
        return tmp, conn, decision, did, _memo_hex_for(decision)
    # Empty DB — caller will build their own memo_hex for the not-found test.
    return tmp, conn, None, None, ""


def test_verify_memo_hex_match() -> None:
    decision = _make_decision(
        "did:xrpl:2:rControllerBanned", False, "CONTROLLER_BANNED"
    )
    tmp, conn, _, did, memo_hex = _api_verify_with_db(decision, "TX_MIRROR_1")
    try:
        payload, status = srv.api_verify(conn, memo_hex=memo_hex, tx_hash=None)
        assert status == 200, f"expected 200, got {status}: {payload}"
        assert payload["verified"] is True
        assert payload["lookup_via"] == "decision_id"
        assert payload["decision_id"] == did
        assert payload["memo"]["agent"] == "did:xrpl:2:rControllerBanned"
        assert payload["decision"]["agent_did"] == "did:xrpl:2:rControllerBanned"
        assert payload["decision"]["mirrored_tx"] == "TX_MIRROR_1"
        assert payload["decision"]["reasons"][0]["code"] == "CONTROLLER_BANNED"
        assert payload["on_chain_proof"] is None  # memo_hex path has no proof
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_memo_hex_not_in_db() -> None:
    # Memo is well-formed, but no row in our DB
    decision = _make_decision("did:xrpl:2:rGhostAgent", True)
    tmp, conn, _, _, _ = _api_verify_with_db()  # empty DB
    try:
        # Memo hex is valid but no row exists with that decision_id
        memo_hex = _memo_hex_for(decision)
        payload, status = srv.api_verify(conn, memo_hex=memo_hex, tx_hash=None)
        assert status == 404
        assert payload["verified"] is False
        assert payload["decision_id"] == compute_decision_id(decision)
        assert "no matching row" in payload["error"].lower()
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_memo_hex_wrong_app() -> None:
    # Build a memo with the wrong app tag
    bogus = json.dumps(
        {"app": "someone_else", "v": 1, "decision_id": "DEAD", "allow": False,
         "agent": "did:xrpl:2:rX", "ts": "2026-01-01T00:00:00+00:00"},
        separators=(",", ":"),
    )
    blob = bogus.encode("utf-8").hex().upper()
    tmp, conn, _, _, _ = _api_verify_with_db()
    try:
        payload, status = srv.api_verify(conn, memo_hex=blob, tx_hash=None)
        assert status == 400
        assert "not from" in payload["error"].lower()
        assert payload["decoded_memo"]["app"] == "someone_else"
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_memo_hex_bad_hex() -> None:
    tmp, conn, _, _, _ = _api_verify_with_db()
    try:
        payload, status = srv.api_verify(conn, memo_hex="ZZZZ", tx_hash=None)
        assert status == 400
        assert "hex" in payload["error"].lower()
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_requires_one_of_memo_or_tx() -> None:
    tmp, conn, _, _, _ = _api_verify_with_db()
    try:
        payload, status = srv.api_verify(conn, memo_hex=None, tx_hash=None)
        assert status == 400
        assert "required" in payload["error"].lower()
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_rejects_both_memo_and_tx() -> None:
    tmp, conn, _, _, _ = _api_verify_with_db()
    try:
        payload, status = srv.api_verify(
            conn, memo_hex="DEAD", tx_hash="BEEF",
        )
        assert status == 400
        assert "exactly one" in payload["error"].lower()
    finally:
        conn.close()
        tmp.cleanup()


def test_verify_tx_hash_match_with_mirrored_tx_fallback() -> None:
    """If a row was inserted before the decision_id column existed (or its
    decision_id wasn't computed at insert time), /api/verify should still
    find it via the mirrored_tx fallback when given a tx_hash."""
    decision = _make_decision(
        "did:xrpl:2:rLegacy", False, "AGENT_BANNED"
    )
    did = compute_decision_id(decision)
    tmp = tempfile.TemporaryDirectory()
    conn = db.open_db(Path(tmp.name) / "v.db")
    # Insert WITHOUT decision_id (simulate a pre-v0.3.2 row)
    decided_at = int(
        datetime.fromisoformat(decision.evaluated_at).timestamp()
    )
    db.insert_auth_decision(
        conn,
        decided_at=decided_at,
        request_id=None,
        agent_did=decision.agent_did,
        allow=decision.allow,
        reasons_json=json.dumps([r.to_dict() for r in decision.reasons]),
        summary=str(decision),
        request_json=None,
        agent_record=None,
        extra_json=json.dumps({"role": "banned", "mode": "sim"}),
        mirrored_tx="LEGACY_TX",
        decision_id=None,  # <-- the legacy state
    )
    # Memo that the (hypothetical) on-chain tx carries
    memo_hex = _memo_hex_for(decision)
    # Mock the network fetch so we don't actually hit XRPL
    from unittest.mock import patch
    with patch.object(srv, "_fetch_memo_from_tx", return_value=memo_hex):
        try:
            payload, status = srv.api_verify(
                conn, memo_hex=None, tx_hash="LEGACY_TX", network="testnet",
            )
            assert status == 200, f"got {status}: {payload}"
            assert payload["verified"] is True
            assert payload["lookup_via"] == "mirrored_tx (fallback)"
            assert payload["decision_id"] == did
            assert payload["on_chain_proof"]["tx_hash"] == "LEGACY_TX"
            assert payload["on_chain_proof"]["network"] == "testnet"
        finally:
            conn.close()
            tmp.cleanup()


# -----------------------------------------------------------------------
# HTTP-layer test (covers routing + 400/200 responses)
# -----------------------------------------------------------------------

def test_verify_endpoint_over_http() -> None:
    decision = _make_decision(
        "did:xrpl:2:rHttpAgent", False, "NO_CREDENTIALS"
    )
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "v.db"
    conn = db.open_db(db_path)
    memo_hex = _memo_hex_for(decision)
    _seed_decision(conn, decision, "HTTP_TX")
    conn.close()

    # Spin up a real server pointing at our temp DB
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.DashboardHandler)
    httpd.db_path = db_path  # type: ignore[attr-defined]
    import threading
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    try:
        c = HTTPConnection("127.0.0.1", port, timeout=5)

        # 1. Happy path with memo_hex
        from urllib.parse import urlencode
        qs = urlencode({"memo_hex": memo_hex})
        c.request("GET", f"/api/verify?{qs}")
        r = c.getresponse()
        body = json.loads(r.read())
        assert r.status == 200, f"happy path: {r.status} {body}"
        assert body["verified"] is True
        assert body["decision"]["agent_did"] == "did:xrpl:2:rHttpAgent"

        # 2. Bad hex → 400
        c.request("GET", "/api/verify?memo_hex=ZZZZ")
        r = c.getresponse()
        body = json.loads(r.read())
        assert r.status == 400
        assert "hex" in body["error"].lower()

        # 3. Missing both → 400
        c.request("GET", "/api/verify")
        r = c.getresponse()
        body = json.loads(r.read())
        assert r.status == 400

        # 4. Both → 400
        c.request("GET", "/api/verify?memo_hex=AA&tx_hash=BB")
        r = c.getresponse()
        body = json.loads(r.read())
        assert r.status == 400
        c.close()
    finally:
        httpd.shutdown()


# -----------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------

if __name__ == "__main__":
    test_decode_memo_hex_happy_path()
    test_decode_memo_hex_strips_0x_and_quotes()
    test_decode_memo_hex_bad_hex()
    test_decode_memo_hex_non_utf8()
    test_verify_memo_hex_match()
    test_verify_memo_hex_not_in_db()
    test_verify_memo_hex_wrong_app()
    test_verify_memo_hex_bad_hex()
    test_verify_requires_one_of_memo_or_tx()
    test_verify_rejects_both_memo_and_tx()
    test_verify_tx_hash_match_with_mirrored_tx_fallback()
    test_verify_endpoint_over_http()
    print("\nAll /api/verify tests passed.")
