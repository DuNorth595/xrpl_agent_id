# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.dashboard.db — SQLite schema + insert helpers.

Three tables cover the agent-ID surface:

    identity_events    — every DIDSet / DIDDelete seen on the watched network
    credential_events  — every CredentialCreate / Accept / Delete
    watchlist          — addresses the monitor is tracking (agent + issuer)
    monitor_events     — connect / disconnect / errors for the dashboard's
                          health panel

All times are unix seconds. WAL mode + check_same_thread=False so the
HTTP server can read while the monitor writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at   INTEGER NOT NULL,
    ledger_seq    INTEGER,
    close_time    INTEGER,
    tx_hash       TEXT NOT NULL UNIQUE,
    op_type       TEXT NOT NULL,         -- 'DIDSet' | 'DIDDelete'
    account       TEXT NOT NULL,         -- rXXX that owns the DID
    did           TEXT,                  -- did:xrpl:<network-id>:<account>
    uri_hex       TEXT,                  -- on-ledger URI hex (DIDSet)
    data_hex      TEXT,                  -- on-ledger Data hex (DIDSet)
    fee_drops     INTEGER,
    raw           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_id_account  ON identity_events(account);
CREATE INDEX IF NOT EXISTS idx_id_received ON identity_events(received_at);

CREATE TABLE IF NOT EXISTS credential_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at   INTEGER NOT NULL,
    ledger_seq    INTEGER,
    close_time    INTEGER,
    tx_hash       TEXT NOT NULL UNIQUE,
    op_type       TEXT NOT NULL,         -- 'CredentialCreate' | 'CredentialAccept' | 'CredentialDelete'
    issuer        TEXT,                  -- rXXX for Create/Delete
    subject       TEXT,                  -- rXXX for Create/Accept
    credential_type_hex TEXT,            -- 64-byte hex string per XLS-70
    credential_type     TEXT,            -- human-readable (hex-decoded, utf-8 if printable)
    uri_hex       TEXT,                  -- on-ledger URI hex
    expiration    INTEGER,
    fee_drops     INTEGER,
    raw           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cred_issuer   ON credential_events(issuer);
CREATE INDEX IF NOT EXISTS idx_cred_subject  ON credential_events(subject);
CREATE INDEX IF NOT EXISTS idx_cred_received ON credential_events(received_at);
CREATE INDEX IF NOT EXISTS idx_cred_type     ON credential_events(credential_type_hex);

CREATE TABLE IF NOT EXISTS watchlist (
    address       TEXT PRIMARY KEY,       -- rXXX
    role          TEXT NOT NULL,          -- 'agent' | 'issuer' | 'observer'
    label         TEXT,                   -- human-friendly name
    added_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,          -- 'connect' | 'disconnect' | 'error' | 'subscribe_ack' | 'reconnect'
    fired_at      INTEGER NOT NULL,
    detail        TEXT                    -- free-form JSON / message
);

CREATE INDEX IF NOT EXISTS idx_mevent_fired ON monitor_events(fired_at);

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
    mirrored_tx   TEXT
);

CREATE INDEX IF NOT EXISTS idx_ad_agent    ON auth_decisions(agent_did);
CREATE INDEX IF NOT EXISTS idx_ad_decided  ON auth_decisions(decided_at);
CREATE INDEX IF NOT EXISTS idx_ad_allow    ON auth_decisions(allow);
CREATE INDEX IF NOT EXISTS idx_ad_request  ON auth_decisions(request_id);
"""


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the dashboard DB and ensure schema is up to date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    for stmt in SCHEMA.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    return conn


# -----------------------------------------------------------------------
# Insert helpers — each transaction-type maps to one table.
# -----------------------------------------------------------------------

def insert_identity_event(
    conn: sqlite3.Connection,
    *,
    received_at: int,
    tx_hash: str,
    op_type: str,
    account: str,
    did: str | None,
    uri_hex: str | None,
    data_hex: str | None,
    fee_drops: int | None,
    ledger_seq: int | None,
    close_time: int | None,
    raw: dict[str, Any],
) -> bool:
    """Returns True if inserted, False if duplicate (already seen)."""
    try:
        conn.execute(
            """
            INSERT INTO identity_events (
                received_at, tx_hash, op_type, account, did, uri_hex, data_hex,
                fee_drops, ledger_seq, close_time, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                received_at, tx_hash, op_type, account, did, uri_hex, data_hex,
                fee_drops, ledger_seq, close_time, json.dumps(raw, separators=(",", ":")),
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def insert_credential_event(
    conn: sqlite3.Connection,
    *,
    received_at: int,
    tx_hash: str,
    op_type: str,
    issuer: str | None,
    subject: str | None,
    credential_type_hex: str | None,
    credential_type: str | None,
    uri_hex: str | None,
    expiration: int | None,
    fee_drops: int | None,
    ledger_seq: int | None,
    close_time: int | None,
    raw: dict[str, Any],
) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO credential_events (
                received_at, tx_hash, op_type, issuer, subject,
                credential_type_hex, credential_type, uri_hex, expiration,
                fee_drops, ledger_seq, close_time, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                received_at, tx_hash, op_type, issuer, subject,
                credential_type_hex, credential_type, uri_hex, expiration,
                fee_drops, ledger_seq, close_time, json.dumps(raw, separators=(",", ":")),
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def insert_monitor_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    fired_at: int,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO monitor_events (event_type, fired_at, detail) VALUES (?, ?, ?)",
        (event_type, fired_at, detail),
    )


def upsert_watch(
    conn: sqlite3.Connection,
    *,
    address: str,
    role: str,
    label: str | None = None,
    added_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO watchlist (address, role, label, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            role = excluded.role,
            label = excluded.label
        """,
        (address, role, label, added_at),
    )


def list_watch(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM watchlist ORDER BY role, label, address"))


def remove_watch(conn: sqlite3.Connection, address: str) -> int:
    cur = conn.execute("DELETE FROM watchlist WHERE address = ?", (address,))
    return cur.rowcount


def insert_auth_decision(
    conn: sqlite3.Connection,
    *,
    decided_at: int,
    request_id: str | None,
    agent_did: str,
    allow: bool,
    reasons_json: str,
    summary: str,
    request_json: str | None,
    agent_record: str | None,
    extra_json: str | None,
    mirrored_tx: str | None,
) -> int:
    """Insert one authorization decision row. Returns new row id."""
    cur = conn.execute(
        """
        INSERT INTO auth_decisions (
            decided_at, request_id, agent_did, allow, reasons_json,
            summary, request_json, agent_record, extra_json, mirrored_tx
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decided_at, request_id, agent_did, 1 if allow else 0, reasons_json,
            summary, request_json, agent_record, extra_json, mirrored_tx,
        ),
    )
    return cur.lastrowid or 0


def update_auth_mirror_tx(conn: sqlite3.Connection, row_id: int, mirrored_tx: str) -> None:
    conn.execute(
        "UPDATE auth_decisions SET mirrored_tx = ? WHERE id = ?",
        (mirrored_tx, row_id),
    )


def update_auth_extra(conn: sqlite3.Connection, row_id: int, extra_json: str) -> None:
    conn.execute(
        "UPDATE auth_decisions SET extra_json = ? WHERE id = ?",
        (extra_json, row_id),
    )


def list_auth_decisions(
    conn: sqlite3.Connection,
    *,
    agent_did: str | None = None,
    allow: bool | None = None,
    since: int | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if agent_did is not None:
        clauses.append("agent_did = ?")
        params.append(agent_did)
    if allow is not None:
        clauses.append("allow = ?")
        params.append(1 if allow else 0)
    if since is not None:
        clauses.append("decided_at >= ?")
        params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    return list(conn.execute(
        f"SELECT * FROM auth_decisions{where} ORDER BY decided_at DESC LIMIT ?",
        params,
    ))


def auth_decision_stats(conn: sqlite3.Connection, since: int | None = None) -> dict:
    params: list[Any] = []
    where = ""
    if since is not None:
        where = " WHERE decided_at >= ?"
        params.append(since)
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM auth_decisions{where}", params
    ).fetchone()["c"]
    allowed = conn.execute(
        f"SELECT COUNT(*) AS c FROM auth_decisions{where}{' AND' if where else ' WHERE'} allow = 1",
        params,
    ).fetchone()["c"]
    return {"total": total, "allowed": allowed, "denied": total - allowed}
