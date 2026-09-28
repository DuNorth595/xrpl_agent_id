# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Smoke test for the dashboard package — no live XRPL connection needed.

Exercises the db layer, API helpers, and HTTP handler with an in-memory
SQLite DB. Verifies:
- schema applies cleanly
- insert helpers dedupe on tx_hash
- /api/summary, /api/credentials, /api/identities, /api/agent_state respond
- watchlist add/remove works

Run: /usr/bin/python3 tests/test_dashboard.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

# Make the package importable when run from anywhere
HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

from xrpl_agent_id.dashboard import db, server as srv


def _make_conn(path: Path) -> sqlite3.Connection:
    return db.open_db(path)


def _tx(op: str, **kw) -> dict:
    base = {"TransactionType": op, "hash": "DEAD" + op + str(time.time_ns())[:8]}
    base.update(kw)
    return base


def test_schema_and_inserts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _make_conn(Path(tmp) / "t.db")
        now = int(time.time())

        # Identity
        assert db.insert_identity_event(
            conn,
            received_at=now,
            tx_hash="idhash1",
            op_type="DIDSet",
            account="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            did="did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            uri_hex=None,
            data_hex=None,
            fee_drops=15,
            ledger_seq=1,
            close_time=now,
            raw={"TransactionType": "DIDSet"},
        ) is True
        # duplicate
        assert db.insert_identity_event(
            conn,
            received_at=now,
            tx_hash="idhash1",
            op_type="DIDSet",
            account="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            did="did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            uri_hex=None,
            data_hex=None,
            fee_drops=15,
            ledger_seq=1,
            close_time=now,
            raw={"TransactionType": "DIDSet"},
        ) is False

        # Credential Create
        assert db.insert_credential_event(
            conn,
            received_at=now,
            tx_hash="chash1",
            op_type="CredentialCreate",
            issuer="rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU",
            subject="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            credential_type_hex="6167656E745F6964656E746974795F7631",
            credential_type="agent_identity_v1",
            uri_hex="697066733A2F2F666F6F",
            expiration=None,
            fee_drops=15,
            ledger_seq=1,
            close_time=now,
            raw={"TransactionType": "CredentialCreate"},
        ) is True

        # Credential Accept (subject accepts; this is what makes them "hold" it)
        assert db.insert_credential_event(
            conn,
            received_at=now + 1,
            tx_hash="chash2",
            op_type="CredentialAccept",
            issuer="rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU",
            subject="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            credential_type_hex="6167656E745F6964656E746974795F7631",
            credential_type="agent_identity_v1",
            uri_hex="697066733A2F2F666F6F",
            expiration=None,
            fee_drops=15,
            ledger_seq=2,
            close_time=now + 1,
            raw={"TransactionType": "CredentialAccept"},
        ) is True

        # Watchlist
        db.upsert_watch(
            conn, address="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            role="agent", label="Test Subject", added_at=now,
        )
        rows = db.list_watch(conn)
        assert len(rows) == 1
        assert rows[0]["address"] == "rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf"

        # API helpers
        summary = srv.api_summary(conn)
        assert summary["identity_events"] == 1
        assert summary["credential_events"] == 2
        assert summary["watchlist_count"] == 1

        creds = srv.api_credentials(conn)
        assert len(creds) == 2
        assert creds[0]["op_type"] == "CredentialAccept"
        assert creds[1]["op_type"] == "CredentialCreate"
        assert creds[0]["credential_type"] == "agent_identity_v1"

        ids = srv.api_identities(conn)
        assert len(ids) == 1
        assert ids[0]["op_type"] == "DIDSet"

        state = srv.api_agent_state(conn)
        assert len(state["agents"]) == 1
        agent = state["agents"][0]
        assert agent["address"] == "rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf"
        assert agent["role"] == "agent"
        assert agent["did_status"] is not None
        # 1 Create (subject is party) + 1 Accept (subject is party) = 2 events for this agent
        assert len(agent["credentials"]) == 2

        # Remove from watchlist
        assert db.remove_watch(conn, "rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf") == 1
        assert len(db.list_watch(conn)) == 0

        print("OK schema + inserts + watchlist + API helpers")


def test_http_handler() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "http.db"
        # Seed some data first
        conn = _make_conn(db_path)
        now = int(time.time())
        db.upsert_watch(
            conn, address="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            role="agent", label="test", added_at=now,
        )
        db.insert_identity_event(
            conn,
            received_at=now, tx_hash="id1", op_type="DIDSet",
            account="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            did="did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
            uri_hex=None, data_hex=None, fee_drops=15,
            ledger_seq=1, close_time=now, raw={},
        )
        conn.close()

        # Boot server on a random port
        httpd = srv.DashboardServer(("127.0.0.1", 0), srv.DashboardHandler, db_path)
        port = httpd.server_address[1]
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()

        try:
            c = HTTPConnection("127.0.0.1", port, timeout=5)
            for path in ("/", "/api/summary", "/api/credentials", "/api/identities",
                         "/api/watchlist", "/api/agent_state", "/api/health", "/api/files"):
                c.request("GET", path)
                r = c.getresponse()
                body = r.read()
                assert r.status == 200, f"{path}: status {r.status} body {body[:200]!r}"
                ct = r.getheader("Content-Type", "")
                if path == "/":
                    assert "text/html" in ct, f"{path}: bad ct {ct}"
                    assert b"xrpl_agent_id" in body
                else:
                    assert "application/json" in ct, f"{path}: bad ct {ct}"
                    json.loads(body)
            c.close()
            print("OK HTTP handler — all endpoints serve 200")
        finally:
            httpd.shutdown()


def test_files_api_lists_project() -> None:
    """api_files() returns the project root, version, and walks all visible files."""
    result = srv.api_files()
    assert result["root"].endswith("XRPL_AGENT_ID")
    assert result["version"].startswith("0.")  # whatever current version is
    assert len(result["files"]) > 0
    # Every result has a positive size and an absolute path
    for f in result["files"]:
        assert f["abs_path"].startswith("/")
        assert f["rel_path"]
        assert f["size_bytes"] >= 0
        assert f["kind"] == "file"


def test_files_api_excludes_secrets_and_artifacts() -> None:
    """Secrets (seeds.json, .env) and build artifacts (.db, .pyc, .git/) must not leak."""
    result = srv.api_files()
    rel_paths = {f["rel_path"] for f in result["files"]}
    # Build artifacts
    assert not any(p.endswith(".pyc") for p in rel_paths)
    assert not any(p.endswith(".db") for p in rel_paths)
    assert not any(p.endswith(".db-shm") for p in rel_paths)
    assert not any(p.endswith(".db-wal") for p in rel_paths)
    # Secrets
    assert "seeds.json" not in rel_paths
    assert ".env" not in rel_paths
    # VCS noise
    assert not any(p.startswith(".git/") for p in rel_paths)


if __name__ == "__main__":
    test_schema_and_inserts()
    test_http_handler()
    test_files_api_lists_project()
    test_files_api_excludes_secrets_and_artifacts()
    print("\nAll dashboard smoke tests passed.")
