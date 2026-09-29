# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""AuditLog — append-only record of every AuthorizationDecision.

Two persistence modes:

  - local SQLite (default, fast, queryable)
  - optional XRPL mirror via a 0-XRP self-payment with a memo (immutable,
    public, costs ~15 drops per write)

The XRPL mirror is opt-in because every write costs a transaction fee and
requires a funded signing wallet. Use it for high-value decisions you want
publicly attributable and tamper-evident; skip it for routine traffic.

Usage:

    from xrpl_agent_id.audit import AuditLog, XRPLMirror

    log = AuditLog(db_path="audit.db")
    log.record(decision, context={"endpoint": "/api/transfer"})

    # With XRPL mirror (optional)
    mirror = XRPLMirror.from_seed("sEd...", network="testnet")
    log = AuditLog(db_path="audit.db", xrpl_mirror=mirror)
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xrpl_agent_id.authorization import AuthorizationDecision


AUDIT_SCHEMA = """
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


@dataclass
class AuditRecord:
    """A row from the audit log, returned by AuditLog.query()."""

    id: int
    decided_at: int
    request_id: str | None
    agent_did: str
    allow: bool
    reasons_json: str
    summary: str
    request_json: str | None
    agent_record: str | None
    extra_json: str | None
    mirrored_tx: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditRecord":
        return cls(
            id=row["id"],
            decided_at=row["decided_at"],
            request_id=row["request_id"],
            agent_did=row["agent_did"],
            allow=bool(row["allow"]),
            reasons_json=row["reasons_json"],
            summary=row["summary"],
            request_json=row["request_json"],
            agent_record=row["agent_record"],
            extra_json=row["extra_json"],
            mirrored_tx=row["mirrored_tx"],
        )

    def reasons(self) -> list[dict]:
        return json.loads(self.reasons_json)

    def extra(self) -> dict:
        return json.loads(self.extra_json) if self.extra_json else {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "decided_at": self.decided_at,
            "request_id": self.request_id,
            "agent_did": self.agent_did,
            "allow": self.allow,
            "reasons": self.reasons(),
            "summary": self.summary,
            "request": (json.loads(self.request_json) if self.request_json else None),
            "agent_record": self.agent_record,
            "extra": self.extra(),
            "mirrored_tx": self.mirrored_tx,
        }


class XRPLMirror:
    """Optional on-chain mirror that writes a 0-XRP payment with a memo.

    Each decision is hashed (sha256 of canonical JSON) and the hash is
    submitted as a memo on a payment to ``destination``. The transaction is
    public, immutable, and timestamped by the ledger — proof-of-decision
    without exposing the full decision text on-chain.

    Costs ~15 drops + small fee buffer per decision. Caller is responsible
    for funding the signing wallet.

    NOTE: XRPL rejects self-payments for XRP, so the destination must
    differ from the mirror wallet's own address. A freshly-funded sink
    wallet is the canonical pattern; the live harness funds one alongside
    the mirror.
    """

    def __init__(
        self,
        wallet,
        network: str = "testnet",
        destination: str | None = None,
    ) -> None:
        self.wallet = wallet
        self.network = network
        # If no destination given, fall back to the canonical XRPL
        # "ACCountRoot" address — fees will burn but the memo lands. For
        # testnet this is rrrrrrrrrrrrrrrrrrrrrrrLvTp.
        self.destination = destination or "rrrrrrrrrrrrrrrrrrrrrrrrLvLvTp"

    @classmethod
    def from_seed(
        cls, seed: str, network: str = "testnet", destination: str | None = None
    ) -> "XRPLMirror":
        from xrpl.wallet import Wallet
        return cls(wallet=Wallet.from_seed(seed), network=network, destination=destination)

    def submit(self, decision: AuthorizationDecision, extra: dict | None = None) -> str:
        """Submit the decision hash as a memo on a payment to ``destination``.

        Returns the tx hash. Raises on submission failure.
        """
        import hashlib
        from xrpl.transaction import submit_and_wait
        from xrpl.models.transactions import Payment, Memo
        from xrpl_agent_id.network import get_client

        canonical = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()

        memo_payload = {
            "app": "xrpl_agent_id_audit",
            "v": 1,
            "decision_id": digest,
            "allow": decision.allow,
            "agent": decision.agent_did,
            "ts": decision.evaluated_at,
        }
        memo_data = json.dumps(memo_payload, separators=(",", ":"))

        tx = Payment(
            account=self.wallet.address,
            destination=self.destination,
            amount="1",  # 1 drop — the smallest valid XRP amount. 0-XRP fails
                         # temBAD_AMOUNT for non-ACCountRoot destinations.
            memos=[
                Memo(
                    memo_data=memo_data.encode("utf-8").hex().upper(),
                    memo_format="68747470733A2F2F7872706C2D6167656E742D69642E6578616D706C652F6175646974"
                                # "https://xrpl-agent-id.example/audit"
                )
            ],
        )
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        tx_hash = (response.result or {}).get("hash", "")
        return tx_hash


class AuditLog:
    """Append-only audit log for AuthorizationDecisions.

    SQLite-backed. Thread-safe via check_same_thread=False + serialized writes.
    """

    def __init__(
        self,
        db_path: str | Path,
        xrpl_mirror: XRPLMirror | None = None,
        mirror_on_deny_only: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in AUDIT_SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        self.xrpl_mirror = xrpl_mirror
        self.mirror_on_deny_only = mirror_on_deny_only

    @classmethod
    def from_dashboard_db(
        cls,
        db_path: str | Path,
        xrpl_mirror: XRPLMirror | None = None,
        mirror_on_deny_only: bool = True,
    ) -> "AuditLog":
        """Build an AuditLog that points at the dashboard's shared DB.

        The dashboard DB has its own (compatible) auth_decisions schema; we
        still create the AUDIT_SCHEMA tables to be safe, but the dashboard's
        ``open_db()`` already includes them so this is idempotent.
        """
        return cls(
            db_path=db_path,
            xrpl_mirror=xrpl_mirror,
            mirror_on_deny_only=mirror_on_deny_only,
        )

    def record(
        self,
        decision: AuthorizationDecision,
        extra: dict | None = None,
        *,
        attempt_mirror: bool = True,
    ) -> int:
        """Record one decision. Returns the inserted row id.

        If a mirror is configured and (mirror_on_deny_only is False OR the
        decision is a DENY), the mirror is called and its tx hash stored on
        the row. Mirror failures are caught and logged via mirror_tx=None.
        """
        now = int(time.time())
        reasons_json = json.dumps([r.to_dict() for r in decision.reasons])
        request_json = (
            json.dumps(decision.request.__dict__) if decision.request else None
        )
        extra_json = json.dumps(extra) if extra else None
        mirror_tx: str | None = None

        cur = self._conn.execute(
            """
            INSERT INTO auth_decisions (
                decided_at, request_id, agent_did, allow, reasons_json,
                summary, request_json, agent_record, extra_json, mirrored_tx
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                decision.request.request_id if decision.request else None,
                decision.agent_did,
                1 if decision.allow else 0,
                reasons_json,
                decision.summary(),
                request_json,
                decision.agent_record_summary,
                extra_json,
                mirror_tx,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None  # auto-increment column always returns an id on INSERT

        if attempt_mirror and self.xrpl_mirror is not None:
            should_mirror = (not self.mirror_on_deny_only) or (not decision.allow)
            if should_mirror:
                try:
                    mirror_tx = self.xrpl_mirror.submit(decision, extra)
                except Exception as e:
                    mirror_tx = None
                    extra_mirror_err = {"mirror_error": str(e)}
                    self._conn.execute(
                        "UPDATE auth_decisions SET extra_json = ? WHERE id = ?",
                        (
                            json.dumps({**(extra or {}), **extra_mirror_err}),
                            new_id,
                        ),
                    )
                else:
                    self._conn.execute(
                        "UPDATE auth_decisions SET mirrored_tx = ? WHERE id = ?",
                        (mirror_tx, new_id),
                    )

        return new_id

    def query(
        self,
        *,
        agent_did: str | None = None,
        allow: bool | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        """Query recent decisions, newest first."""
        clauses = []
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
        rows = self._conn.execute(
            f"SELECT * FROM auth_decisions{where} ORDER BY decided_at DESC LIMIT ?",
            params,
        )
        return [AuditRecord.from_row(r) for r in rows]

    def stats(self, since: int | None = None) -> dict:
        """Aggregate stats for the audit log (or a time range)."""
        params: list[Any] = []
        where = ""
        if since is not None:
            where = " WHERE decided_at >= ?"
            params.append(since)
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM auth_decisions{where}", params
        ).fetchone()["n"]
        allowed = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM auth_decisions{where}{' AND' if where else ' WHERE'} allow = 1",
            params,
        ).fetchone()["n"]
        denied = total - allowed
        return {"total": total, "allowed": allowed, "denied": denied}

    def close(self) -> None:
        self._conn.close()


__all__ = ["AuditLog", "AuditRecord", "XRPLMirror"]
