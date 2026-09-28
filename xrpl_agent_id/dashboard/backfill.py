#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Backfill the dashboard DB from saved XRPL testnet results JSON files.

Reads the results/ directory and seeds identity_events + credential_events
+ watchlist from the existing scenario + issuance artifacts. This lets you
boot the dashboard and see real historical data immediately, without
waiting for new live transactions.

Run:
    python -m xrpl_agent_id.dashboard.backfill [--db PATH] [--results PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from xrpl_agent_id.dashboard import db

DEFAULT_RESULTS = Path(__file__).resolve().parents[2] / "results"
DEFAULT_DB = Path.home() / "Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/xrpl_agent_id_dashboard.db"


def _now() -> int:
    return int(time.time())


def _hex_to_str(h: str | None) -> str | None:
    if not h:
        return None
    try:
        return bytes.fromhex(h).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return h


def backfill_scenario(conn, scenario_path: Path) -> dict:
    """One scenario JSON file from results/scenario_*.json."""
    if not scenario_path.exists():
        return {"file": str(scenario_path), "skipped": "missing"}
    raw = json.loads(scenario_path.read_text())
    wallets = raw.get("wallets", {})
    subject_addr = wallets.get("subject", {}).get("address")
    issuer_addr = wallets.get("issuer", {}).get("address")
    evaluator_addr = wallets.get("evaluator", {}).get("address")

    inserted = {"identities": 0, "credentials": 0, "watchlist": 0}
    now = _now()

    # Seed watchlist with all known parties from this scenario
    for addr, role, label in [
        (subject_addr, "agent", "Subject"),
        (issuer_addr, "issuer", "Issuer"),
        (evaluator_addr, "issuer", "Evaluator"),
    ]:
        if addr:
            db.upsert_watch(conn, address=addr, role=role, label=label, added_at=now)
            inserted["watchlist"] += 1

    steps = raw.get("steps", {})
    started = raw.get("started_at", "")
    # Parse ISO timestamp into unix seconds
    try:
        from datetime import datetime
        started_ts = int(datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp())
    except Exception:
        started_ts = now

    # Issue/Create/Accept steps we know about
    cred_steps = [
        ("issue_kyc", "CredentialCreate", issuer_addr, subject_addr,
         "6167656E745F6964656E746974795F7631", "agent_identity_v1"),
        ("issue_eval", "CredentialCreate", evaluator_addr, subject_addr,
         "6167656E745F6576616C5F7061737365645F7631", "eval_passed_v1"),
        ("accept_kyc", "CredentialAccept", issuer_addr, subject_addr,
         "6167656E745F6964656E746974795F7631", "agent_identity_v1"),
        ("accept_eval", "CredentialAccept", evaluator_addr, subject_addr,
         "6167656E745F6576616C5F7061737365645F7631", "eval_passed_v1"),
    ]
    for i, (key, op, iss, sub, ctype_hex, ctype) in enumerate(cred_steps):
        step = steps.get(key)
        if not step:
            continue
        uri = step.get("uri", "")
        uri_hex = uri.encode("utf-8").hex() if uri else None
        ok = db.insert_credential_event(
            conn,
            received_at=started_ts + i,  # synthetic ordering
            tx_hash=f"SCN-{scenario_path.stem}-{key}",
            op_type=op,
            issuer=iss,
            subject=sub,
            credential_type_hex=ctype_hex,
            credential_type=ctype,
            uri_hex=uri_hex,
            expiration=None,
            fee_drops=15,
            ledger_seq=None,
            close_time=started_ts + i,
            raw=step,
        )
        if ok:
            inserted["credentials"] += 1

    # DIDSet
    did_step = steps.get("did_set")
    if did_step and subject_addr:
        uri = did_step.get("uri", "")
        uri_hex = uri.encode("utf-8").hex() if uri else None
        ok = db.insert_identity_event(
            conn,
            received_at=started_ts + 10,
            tx_hash=f"SCN-{scenario_path.stem}-did_set",
            op_type="DIDSet",
            account=subject_addr,
            did=f"did:xrpl:2:{subject_addr}",
            uri_hex=uri_hex,
            data_hex=None,
            fee_drops=15,
            ledger_seq=None,
            close_time=started_ts + 10,
            raw=did_step,
        )
        if ok:
            inserted["identities"] += 1

    # Revoke step
    revoke = steps.get("revoke_kyc")
    if revoke and revoke.get("tx_hash"):
        ok = db.insert_credential_event(
            conn,
            received_at=started_ts + 20,
            tx_hash=revoke["tx_hash"],
            op_type="CredentialDelete",
            issuer=issuer_addr,
            subject=subject_addr,
            credential_type_hex="6167656E745F6964656E746974795F7631",
            credential_type="agent_identity_v1",
            uri_hex=None,
            expiration=None,
            fee_drops=15,
            ledger_seq=None,
            close_time=started_ts + 20,
            raw=revoke,
        )
        if ok:
            inserted["credentials"] += 1

    return {"file": scenario_path.name, **inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description="backfill dashboard DB from results/")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    args = parser.parse_args()

    db_path = Path(args.db)
    results_dir = Path(args.results)
    conn = db.open_db(db_path)

    scenarios = sorted(results_dir.glob("scenario_*.json"))
    if not scenarios:
        print(f"no scenario_*.json files in {results_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"backfilling from {len(scenarios)} scenario file(s) into {db_path}")
    for s in scenarios:
        result = backfill_scenario(conn, s)
        print(f"  {result['file']}: identities={result.get('identities', 0)} "
              f"credentials={result.get('credentials', 0)} "
              f"watchlist={result.get('watchlist', 0)}")


if __name__ == "__main__":
    main()
