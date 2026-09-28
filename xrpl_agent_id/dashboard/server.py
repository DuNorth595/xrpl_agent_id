#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.dashboard.server — stdlib HTTP dashboard.

Single-page HTML+CSS+JS dashboard that polls /api/* endpoints every few
seconds. No external JS deps; vanilla fetch + DOM.

Run:
    python -m xrpl_agent_id.dashboard.server
    python -m xrpl_agent_id.dashboard.server --host 0.0.0.0 --port 8768
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from xrpl_agent_id.dashboard import db

DEFAULT_DB = Path.home() / "Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/xrpl_agent_id_dashboard.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8768
HTML_PATH = Path(__file__).parent / "templates" / "index.html"
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../XRPL_AGENT_ID/


# -----------------------------------------------------------------------
# API handlers
# -----------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def api_summary(conn: sqlite3.Connection) -> dict:
    id_count = conn.execute("SELECT COUNT(*) AS c FROM identity_events").fetchone()["c"]
    cred_count = conn.execute("SELECT COUNT(*) AS c FROM credential_events").fetchone()["c"]
    last_cred = conn.execute(
        "SELECT op_type, tx_hash, issuer, subject, credential_type, received_at "
        "FROM credential_events ORDER BY received_at DESC LIMIT 1"
    ).fetchone()
    last_id = conn.execute(
        "SELECT op_type, tx_hash, account, received_at "
        "FROM identity_events ORDER BY received_at DESC LIMIT 1"
    ).fetchone()
    last_event = conn.execute(
        "SELECT event_type, fired_at FROM monitor_events ORDER BY fired_at DESC LIMIT 1"
    ).fetchone()
    watch_count = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
    return {
        "now": int(time.time()),
        "identity_events": id_count,
        "credential_events": cred_count,
        "watchlist_count": watch_count,
        "last_credential": _row_to_dict(last_cred) if last_cred else None,
        "last_identity": _row_to_dict(last_id) if last_id else None,
        "last_monitor_event": _row_to_dict(last_event) if last_event else None,
    }


def api_credentials(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM credential_events ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def api_identities(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM identity_events ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def api_watchlist(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM watchlist ORDER BY role, label, address").fetchall()
    return [_row_to_dict(r) for r in rows]


def api_health(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM monitor_events ORDER BY fired_at DESC LIMIT ?", (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def api_agent_state(conn: sqlite3.Connection) -> dict:
    """For each watched address, summarize all credential + DID events involving it.

    A 'held' credential is one where the agent is the subject of a
    CredentialAccept AND there's no later CredentialDelete for the same
    (issuer, subject, credential_type) triple.
    """
    watched = [dict(r) for r in conn.execute("SELECT * FROM watchlist").fetchall()]
    out = []
    for w in watched:
        addr = w["address"]
        # Latest DIDSet/DIDDelete for this account
        did_row = conn.execute(
            "SELECT op_type, did, received_at FROM identity_events "
            "WHERE account = ? ORDER BY received_at DESC LIMIT 1",
            (addr,),
        ).fetchone()
        # All credential events involving this address (any party)
        cred_rows = conn.execute(
            """
            SELECT op_type, issuer, subject, credential_type, credential_type_hex,
                   uri_hex, expiration, tx_hash, received_at
            FROM credential_events
            WHERE subject = ? OR issuer = ?
            ORDER BY received_at DESC
            """,
            (addr, addr),
        ).fetchall()
        out.append({
            "address": addr,
            "role": w["role"],
            "label": w["label"],
            "did_status": _row_to_dict(did_row) if did_row else None,
            "credentials": [_row_to_dict(r) for r in cred_rows],
        })
    return {"agents": out, "now": int(time.time())}


# -----------------------------------------------------------------------
# Project file listing
# -----------------------------------------------------------------------

# Files / dirs we never surface (build artifacts, caches, secrets, db)
_EXCLUDE_DIR_NAMES = {
    "__pycache__", ".git", ".pytest_cache", "node_modules", "build", "dist",
    ".venv", "venv", ".mypy_cache", ".ruff_cache", ".DS_Store",
}
_EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo", ".db", ".db-journal"}
_EXCLUDE_FILE_SUFFIX_PARTS = {".db-shm", ".db-wal"}  # SQLite WAL-mode sidecars
_EXCLUDE_FILE_NAMES = {"seeds.json", ".env", ".env.local"}


def _walk_project(root: Path) -> list[dict]:
    """Walk PROJECT_ROOT, return [{rel_path, abs_path, size_bytes, kind}]."""
    rows: list[dict] = []
    if not root.exists():
        return rows
    for p in sorted(root.rglob("*")):
        if any(part in _EXCLUDE_DIR_NAMES for part in p.relative_to(root).parts):
            continue
        if p.is_dir():
            continue
        if p.suffix in _EXCLUDE_FILE_SUFFIXES:
            continue
        if any(p.name.endswith(s) for s in _EXCLUDE_FILE_SUFFIX_PARTS):
            continue
        if p.name in _EXCLUDE_FILE_NAMES:
            continue
        try:
            rel = p.relative_to(root).as_posix()
            size = p.stat().st_size
        except OSError:
            continue
        rows.append({
            "rel_path": rel,
            "abs_path": str(p),
            "size_bytes": size,
            "kind": "file",
        })
    return rows


def _pkg_version() -> str:
    """Read xrpl_agent_id.__version__ without importing the whole package.

    The package's __init__ pulls in xrpl-py which can be slow; read it raw.
    """
    try:
        init = (PROJECT_ROOT / "xrpl_agent_id" / "__init__.py").read_text()
        for line in init.splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


def api_files() -> dict:
    """List every file in the project tree (for the dashboard 'Project Files' panel).

    Surfacing paths lets users jump straight from the dashboard to the source
    without remembering the project layout.
    """
    return {
        "root": str(PROJECT_ROOT),
        "version": _pkg_version(),
        "files": _walk_project(PROJECT_ROOT),
        "now": int(time.time()),
    }


# -----------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "xrpl_agent_id_dashboard/0.1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet default access log; uncomment to debug.
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str, status: int = 200) -> None:
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _send_static(self, path: Path) -> None:
        if not path.exists():
            self.send_error(404, "not found")
            return
        body = path.read_bytes()
        ct = "text/plain"
        if path.suffix == ".html":
            ct = "text/html; charset=utf-8"
        elif path.suffix == ".css":
            ct = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            ct = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)

        # Open per-request connection (cheap with WAL + ThreadingHTTPServer)
        server_db = self.server.db_path  # type: ignore[attr-defined]
        conn = db.open_db(server_db)

        try:
            if path == "/" or path == "/index.html":
                return self._send_html(HTML_PATH.read_text())
            if path == "/api/summary":
                return self._send_json(api_summary(conn))
            if path == "/api/credentials":
                limit = int(qs.get("limit", ["50"])[0])
                return self._send_json({"events": api_credentials(conn, limit=limit)})
            if path == "/api/identities":
                limit = int(qs.get("limit", ["50"])[0])
                return self._send_json({"events": api_identities(conn, limit=limit)})
            if path == "/api/watchlist":
                return self._send_json({"watchlist": api_watchlist(conn)})
            if path == "/api/agent_state":
                return self._send_json(api_agent_state(conn))
            if path == "/api/files":
                return self._send_json(api_files())
            if path == "/api/health":
                limit = int(qs.get("limit", ["25"])[0])
                return self._send_json({"events": api_health(conn, limit=limit)})
            if path == "/static/style.css" or path == "/static/dashboard.js":
                name = path.split("/")[-1]
                return self._send_static(HTML_PATH.parent / name)
            return self.send_error(404, "not found")
        finally:
            conn.close()


# -----------------------------------------------------------------------
# Server factory
# -----------------------------------------------------------------------

class DashboardServer(ThreadingHTTPServer):
    def __init__(self, addr: tuple[str, int], handler: type, db_path: Path) -> None:
        super().__init__(addr, handler)
        self.db_path = db_path


def main() -> None:
    parser = argparse.ArgumentParser(description="xrpl_agent_id dashboard server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure schema exists before first request
    db.open_db(db_path)

    server = DashboardServer((args.host, args.port), DashboardHandler, db_path)
    print(f"xrpl_agent_id dashboard: http://{args.host}:{args.port}")
    print(f"  db: {db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
