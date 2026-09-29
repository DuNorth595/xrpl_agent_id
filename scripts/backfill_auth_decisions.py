#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""backfill_auth_decisions.py — Copy auth_decisions from one DB to another.

Used to bring the dashboard DB up to date with rows that a live harness
run wrote to its own audit DB (``results/audit_live_*.db``).

Pre-v0.3.2 rows have no ``decision_id`` (the column was added in v0.3.2)
— for those, /api/verify will only be reachable via the ``tx_hash`` path
(falls back to ``mirrored_tx`` lookup). For v0.3.2+ rows, both paths work.

Run:
    /usr/bin/python3 scripts/backfill_auth_decisions.py \\
        --src results/audit_live_1790646795.db \\
        --dst ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/xrpl_agent_id_dashboard.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True, help="Source SQLite (e.g. results/audit_live_*.db)")
    p.add_argument("--dst", required=True, help="Destination SQLite (the dashboard DB)")
    p.add_argument("--dry-run", action="store_true", help="Count matches without writing")
    args = p.parse_args()

    src = sqlite3.connect(args.src)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(args.dst)
    dst.row_factory = sqlite3.Row

    src_cols = [r["name"] for r in src.execute("PRAGMA table_info(auth_decisions)")]
    dst_cols = [r["name"] for r in dst.execute("PRAGMA table_info(auth_decisions)")]
    # Skip 'id' — destination must assign its own autoincrement.
    common = [c for c in src_cols if c in dst_cols and c != "id"]
    print(f"src cols: {src_cols}")
    print(f"dst cols: {dst_cols}")
    print(f"common (excluding id):   {common}")

    # Find rows in src not yet in dst (keyed by mirrored_tx — the only
    # on-chain-unique column that the harness sets).
    src_rows = list(src.execute(
        "SELECT * FROM auth_decisions WHERE mirrored_tx IS NOT NULL ORDER BY id"
    ))
    existing_tx = {
        r["mirrored_tx"]
        for r in dst.execute("SELECT mirrored_tx FROM auth_decisions WHERE mirrored_tx IS NOT NULL")
    }
    to_copy = [r for r in src_rows if r["mirrored_tx"] not in existing_tx]
    print(f"src rows with mirror: {len(src_rows)}, already in dst: {len(src_rows) - len(to_copy)}, to copy: {len(to_copy)}")

    if args.dry_run or not to_copy:
        return 0

    placeholders = ",".join("?" * len(common))
    col_list = ",".join(common)
    copied = 0
    for r in to_copy:
        vals = [r[c] for c in common]
        try:
            dst.execute(
                f"INSERT INTO auth_decisions ({col_list}) VALUES ({placeholders})",
                vals,
            )
            copied += 1
        except sqlite3.IntegrityError as e:
            print(f"  skip id={r['id']} mirrored_tx={r['mirrored_tx'][:16]}... ({e})")
    dst.commit()
    print(f"Copied {copied} rows into {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
