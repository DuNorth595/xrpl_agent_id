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
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from xrpl_agent_id.dashboard import db
from xrpl_agent_id.audit import XRPLMirror

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


def api_version() -> dict:
    """Structured version + runtime info for ops and CI smoke tests.

    Stable contract — do not remove fields without bumping the API version.
    """
    return {
        "package": "xrpl_agent_id",
        "package_version": _pkg_version(),
        "server_version": DashboardHandler.server_version.replace("xrpl_agent_id_dashboard/", ""),
        "python_version": sys.version.split()[0],
        "xrpl_py_version": _xrpl_py_version(),
        "now": int(time.time()),
        "ok": True,
    }


def _xrpl_py_version() -> str:
    """Read xrpl-py's version via importlib.metadata (xrpl-py has no __version__ attr)."""
    try:
        from importlib.metadata import version

        return version("xrpl-py")
    except Exception:
        return "unavailable"


def api_liveness(conn: sqlite3.Connection) -> dict:
    """Trivial liveness ping — returns OK if the DB is reachable.

    Replaces the legacy /api/health route, which was actually returning
    monitor events (misleading). The legacy URL still resolves here for
    backward compatibility; new clients should hit /api/liveness.
    """
    row = conn.execute("SELECT 1 AS ok").fetchone()
    return {"status": "ok" if row else "degraded", "now": int(time.time())}


def api_health(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """DEPRECATED — returned monitor events under a confusing name.

    Kept for backward compatibility. New code should use ``api_liveness``
    for actual liveness and ``api_monitor`` for monitor events.
    """
    rows = conn.execute(
        "SELECT * FROM monitor_events ORDER BY fired_at DESC LIMIT ?", (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def api_monitor(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Recent monitor events, newest first. Successor to the misnamed /api/health."""
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


def api_authz_events(conn: sqlite3.Connection, limit: int = 50, agent_did: str | None = None, allow: bool | None = None) -> list[dict]:
    """Recent authorization decisions, newest first."""
    rows = db.list_auth_decisions(
        conn,
        agent_did=agent_did,
        allow=allow,
        limit=limit,
    )
    out = []
    for r in rows:
        d = _row_to_dict(r)
        # Parse reasons_json into a Python list for the frontend
        try:
            d["reasons"] = json.loads(d.pop("reasons_json") or "[]")
        except Exception:
            d["reasons"] = []
        try:
            d["extra"] = json.loads(d.pop("extra_json") or "{}")
        except Exception:
            d["extra"] = {}
        try:
            d["request"] = json.loads(d.pop("request_json") or "null")
        except Exception:
            d["request"] = None
        out.append(d)
    return out


def api_authz_stats(conn: sqlite3.Connection, since_seconds: int = 24 * 3600) -> dict:
    """Aggregate stats for the authorization layer.

    Args:
        since_seconds: window size in seconds; stats cover decisions in this window.
    """
    since = int(time.time()) - since_seconds
    return {
        "window_seconds": since_seconds,
        **db.auth_decision_stats(conn, since=since),
    }


# -----------------------------------------------------------------------
# /api/verify — cross-link on-chain memos to local audit rows
# -----------------------------------------------------------------------

# XRPL public RPC endpoints. testnet is the default; mainnet is also
# supported because the dashboard may eventually verify mainnet memos.
_VERIFY_NETWORK_URLS = {
    "testnet": "https://s.altnet.rippletest.net:51234",
    "mainnet": "https://xrplcluster.com",
}


def _decode_memo_hex(memo_hex: str) -> dict:
    """Decode a memo hex string into the JSON dict it carries.

    Raises:
        ValueError on non-hex or non-utf8 input.
        json.JSONDecodeError if the decoded bytes aren't valid JSON.
    """
    cleaned = memo_hex.strip().strip('"')
    # Strip an optional 0x prefix and uppercase.
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    raw = bytes.fromhex(cleaned)
    return json.loads(raw.decode("utf-8"))


def _format_decision_row(row: sqlite3.Row) -> dict:
    """Render an auth_decisions row the way /api/verify wants to expose it."""
    out = _row_to_dict(row)
    try:
        out["reasons"] = json.loads(out.pop("reasons_json") or "[]")
    except Exception:
        out["reasons"] = []
    try:
        out["extra"] = json.loads(out.pop("extra_json") or "{}")
    except Exception:
        out["extra"] = {}
    try:
        out["request"] = json.loads(out.pop("request_json") or "null")
    except Exception:
        out["request"] = None
    # Pretty ISO timestamp alongside the unix-seconds one.
    try:
        from datetime import datetime, timezone
        out["decided_at_iso"] = (
            datetime.fromtimestamp(int(out["decided_at"]), tz=timezone.utc).isoformat()
        )
    except Exception:
        out["decided_at_iso"] = None
    return out


def _fetch_memo_from_tx(tx_hash: str, network: str) -> str | None:
    """Fetch a tx from XRPL and return the MemoData hex of its first memo.

    Returns None if the tx doesn't exist, has no memos, or the network is
    unknown. Raises xrpl XRPLException subclasses on transient RPC errors
    so the caller can surface them as 502.
    """
    from xrpl.clients import JsonRpcClient
    from xrpl.models.requests import Tx

    url = _VERIFY_NETWORK_URLS.get(network)
    if url is None:
        raise ValueError(f"unknown network: {network!r}")
    client = JsonRpcClient(url)
    resp = client.request(Tx(transaction=tx_hash, binary=False))
    tx = resp.result
    if not tx:
        return None
    memos = (tx.get("tx_json") or {}).get("Memos") or []
    if not memos:
        return None
    return (memos[0].get("Memo") or {}).get("MemoData")


def api_verify(
    conn: sqlite3.Connection,
    *,
    memo_hex: str | None,
    tx_hash: str | None,
    network: str = "testnet",
) -> tuple[dict, int]:
    """Verify an on-chain audit memo against the local SQLite audit log.

    Two entry points:
        memo_hex=...  — paste the hex blob directly (e.g. from a block
                        explorer); we decode it ourselves and look up by
                        ``decision_id``.
        tx_hash=...   — paste an XRPL tx hash; we fetch the memo from the
                        public RPC, then look up by ``decision_id`` with a
                        fallback to ``mirrored_tx``.

    Returns (payload, http_status). The payload shape is the same for both
    entry points, with one extra field (``on_chain_proof``) populated only
    in the tx_hash path.
    """
    if not memo_hex and not tx_hash:
        return (
            {"error": "either memo_hex or tx_hash is required"},
            400,
        )
    if memo_hex and tx_hash:
        return (
            {"error": "pass exactly one of memo_hex or tx_hash, not both"},
            400,
        )

    # ---- 1. Resolve to (memo_dict, on_chain_proof) --------------------
    memo_dict: dict | None = None
    on_chain_proof: dict | None = None

    if memo_hex:
        try:
            memo_dict = _decode_memo_hex(memo_hex)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            # Both are subclasses of ValueError but have specific useful messages.
            return {"error": f"memo decoded bytes are not valid UTF-8/JSON: {e}"}, 400
        except ValueError as e:
            return {"error": f"memo_hex is not valid hex: {e}"}, 400
    else:
        assert tx_hash is not None
        try:
            memo_data_hex = _fetch_memo_from_tx(tx_hash, network)
        except ValueError as e:
            return {"error": str(e)}, 400
        # xrpl raises ResponseException for txnNotFound; surface as 404.
        except Exception as e:  # noqa: BLE001 — translate any xrpl exception
            type_name = type(e).__name__
            if "NotFound" in type_name or "not found" in str(e).lower():
                return {"error": f"tx not found on {network}: {tx_hash}"}, 404
            return {"error": f"XRPL RPC error: {type_name}: {e}"}, 502
        if memo_data_hex is None:
            return {"error": f"tx has no memo (or not found): {tx_hash}"}, 404
        try:
            memo_dict = _decode_memo_hex(memo_data_hex)
        except Exception as e:  # noqa: BLE001
            return {"error": f"on-chain memo is malformed: {e}"}, 502
        on_chain_proof = {
            "tx_hash": tx_hash,
            "network": network,
            "explorer_url": (
                f"https://testnet.xrpl.org/transactions/{tx_hash}"
                if network == "testnet"
                else f"https://xrpl.org/transactions/{tx_hash}"
            ),
        }

    # ---- 2. Validate memo shape ---------------------------------------
    if memo_dict.get("app") != XRPLMirror.APP_TAG:
        return (
            {
                "error": (
                    f"memo is not from {XRPLMirror.APP_TAG} "
                    f"(got app={memo_dict.get('app')!r})"
                ),
                "decoded_memo": memo_dict,
            },
            400,
        )

    decision_id = memo_dict.get("decision_id")
    if not decision_id:
        return {"error": "memo has no decision_id", "decoded_memo": memo_dict}, 400

    # ---- 3. Look up the local SQLite row ------------------------------
    row = db.find_auth_decision_by_decision_id(conn, decision_id)
    lookup_via = "decision_id"
    if row is None and tx_hash:
        # Pre-v0.3.2 rows don't have decision_id populated. Fall back to tx.
        row = db.find_auth_decision_by_mirrored_tx(conn, tx_hash)
        lookup_via = "mirrored_tx (fallback)"
    if row is None:
        return (
            {
                "verified": False,
                "decision_id": decision_id,
                "memo": memo_dict,
                "on_chain_proof": on_chain_proof,
                "lookup_via": lookup_via,
                "error": (
                    "memo is well-formed but no matching row in the local "
                    "audit log. The decision may have been made by a "
                    "different operator."
                ),
            },
            404,
        )

    return (
        {
            "verified": True,
            "lookup_via": lookup_via,
            "decision_id": decision_id,
            "memo": memo_dict,
            "decision": _format_decision_row(row),
            "on_chain_proof": on_chain_proof,
        },
        200,
    )


# -----------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "xrpl_agent_id_dashboard/0.2.0"

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
            if path == "/api/version":
                return self._send_json(api_version())
            if path == "/api/liveness":
                return self._send_json(api_liveness(conn))
            if path == "/api/monitor":
                limit = int(qs.get("limit", ["25"])[0])
                return self._send_json({"events": api_monitor(conn, limit=limit)})
            if path == "/api/health":
                # DEPRECATED: was returning monitor events. Keep working for
                # backward compat but route to the real liveness check.
                limit = int(qs.get("limit", ["25"])[0])
                return self._send_json({"liveness": api_liveness(conn), "legacy_events": api_health(conn, limit=limit)})
            if path == "/api/authz/events":
                limit = int(qs.get("limit", ["50"])[0])
                agent = qs.get("agent_did", [None])[0]
                allow_q = qs.get("allow", [None])[0]
                allow = None
                if allow_q is not None:
                    allow = allow_q.lower() in ("1", "true", "yes")
                return self._send_json({"events": api_authz_events(conn, limit=limit, agent_did=agent, allow=allow)})
            if path == "/api/authz/stats":
                since = int(qs.get("since", [str(24 * 3600)])[0])
                return self._send_json(api_authz_stats(conn, since_seconds=since))
            if path == "/api/verify":
                memo_hex = qs.get("memo_hex", [None])[0]
                tx_hash = qs.get("tx_hash", [None])[0]
                network = qs.get("network", ["testnet"])[0]
                payload, status = api_verify(
                    conn,
                    memo_hex=memo_hex,
                    tx_hash=tx_hash,
                    network=network,
                )
                return self._send_json(payload, status=status)
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
