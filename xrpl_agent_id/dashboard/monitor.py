#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.dashboard.monitor — XRPL testnet websocket subscriber.

Subscribes to the XRPL testnet transactions stream, filters for
agent-identity transactions (CredentialCreate/Accept/Delete, DIDSet/DIDDelete),
inserts into the dashboard DB.

Run:
    python -m xrpl_agent_id.dashboard.monitor
    python -m xrpl_agent_id.dashboard.monitor --watch rPNGAy...,rHDfNd...
    python -m xrpl_agent_id.dashboard.monitor --network mainnet   # not for testing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xrpl.asyncio.clients import AsyncWebsocketClient
from xrpl.models import Subscribe

from xrpl_agent_id.dashboard import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NETWORKS: dict[str, str] = {
    "testnet": "wss://s.altnet.rippletest.net:51233",
    "mainnet": "wss://s1.ripple.com:51233",
    "devnet":  "wss://s.devnet.rippletest.net:51233",
}

IDENTITY_TX_TYPES = {"DIDSet", "DIDDelete"}
CREDENTIAL_TX_TYPES = {"CredentialCreate", "CredentialAccept", "CredentialDelete"}
ALL_TX_TYPES = IDENTITY_TX_TYPES | CREDENTIAL_TX_TYPES

LOG = logging.getLogger("agent_id_monitor")
DEFAULT_DB = Path.home() / "Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/xrpl_agent_id_dashboard.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())


def _decode_hex(s: str | None) -> str | None:
    """Best-effort hex → str. Returns None if input is None or not valid hex."""
    if s is None:
        return None
    try:
        b = bytes.fromhex(s)
    except ValueError:
        return None
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return s  # fall back to hex string


def _extract_account(tx: dict[str, Any]) -> str | None:
    return tx.get("Account")


def _extract_fee_drops(tx: dict[str, Any]) -> int | None:
    f = tx.get("Fee")
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def _extract_credential_type_hex(tx: dict[str, Any]) -> str | None:
    return tx.get("CredentialType")


def _process_tx(
    conn: sqlite3.Connection,
    tx: dict[str, Any],
    *,
    received_at: int,
    ledger_seq: int | None,
    close_time: int | None,
) -> str | None:
    """Route one transaction into the right table. Returns the table touched or None."""
    tx_type = tx.get("TransactionType")
    tx_hash = tx.get("hash")
    if not tx_type or not tx_hash:
        return None

    fee = _extract_fee_drops(tx)

    if tx_type in IDENTITY_TX_TYPES:
        account = _extract_account(tx)
        if not account:
            return None
        from xrpl_agent_id.did import did_from_account
        try:
            did = did_from_account(account, network="testnet")
        except Exception:
            did = None
        ok = db.insert_identity_event(
            conn,
            received_at=received_at,
            tx_hash=tx_hash,
            op_type=tx_type,
            account=account,
            did=did,
            uri_hex=tx.get("URI"),
            data_hex=tx.get("Data"),
            fee_drops=fee,
            ledger_seq=ledger_seq,
            close_time=close_time,
            raw=tx,
        )
        return "identity_events" if ok else None

    if tx_type in CREDENTIAL_TX_TYPES:
        ctype_hex = _extract_credential_type_hex(tx)
        ok = db.insert_credential_event(
            conn,
            received_at=received_at,
            tx_hash=tx_hash,
            op_type=tx_type,
            issuer=tx.get("Issuer"),
            subject=tx.get("Subject"),
            credential_type_hex=ctype_hex,
            credential_type=_decode_hex(ctype_hex),
            uri_hex=tx.get("URI"),
            expiration=tx.get("Expiration"),
            fee_drops=fee,
            ledger_seq=ledger_seq,
            close_time=close_time,
            raw=tx,
        )
        return "credential_events" if ok else None

    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_monitor(
    network: str,
    db_path: Path,
    watch_addresses: set[str] | None = None,
    log_level: int = logging.INFO,
) -> None:
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if network not in NETWORKS:
        raise ValueError(f"unknown network {network!r}; choose from {list(NETWORKS)}")

    url = NETWORKS[network]
    conn = db.open_db(db_path)
    LOG.info("opened db at %s", db_path)
    LOG.info("connecting to %s (%s)", network, url)

    backoff = 1.0
    while True:
        try:
            db.insert_monitor_event(
                conn, event_type="connect", fired_at=_now(), detail=url
            )
            async with AsyncWebsocketClient(url) as client:
                await client.send(Subscribe(streams=["transactions"]))
                db.insert_monitor_event(
                    conn, event_type="subscribe_ack", fired_at=_now(),
                    detail='{"streams":["transactions"]}',
                )
                LOG.info("subscribed to transactions stream on %s", network)
                backoff = 1.0

                async for msg in client:
                    if msg.get("type") != "transaction":
                        continue
                    tx = msg.get("transaction") or {}
                    tx_hash = tx.get("hash")
                    if not tx_hash:
                        continue

                    # If watchlist set, only ingest matching tx
                    if watch_addresses is not None:
                        parties = {
                            tx.get("Account"),
                            tx.get("Issuer"),
                            tx.get("Subject"),
                            tx.get("Destination"),
                        }
                        parties.discard(None)
                        if not (parties & watch_addresses):
                            continue

                    touched = _process_tx(
                        conn,
                        tx,
                        received_at=_now(),
                        ledger_seq=msg.get("ledger_index"),
                        close_time=msg.get("close_time_iso") or None,
                    )
                    if touched:
                        LOG.info(
                            "%s %s on %s",
                            touched,
                            tx.get("TransactionType"),
                            tx_hash[:10] + "...",
                        )
        except Exception as e:  # noqa: BLE001
            db.insert_monitor_event(
                conn, event_type="error", fired_at=_now(), detail=repr(e)
            )
            LOG.exception("monitor error: %s", e)
            LOG.info("reconnecting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_watch(raw: str) -> set[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    for p in parts:
        if not (p.startswith("r") and len(p) >= 25):
            raise ValueError(f"not a classic XRPL address: {p!r}")
    return set(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="xrpl_agent_id dashboard monitor")
    parser.add_argument("--network", default="testnet", choices=list(NETWORKS))
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"path to dashboard SQLite db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--watch",
        default=None,
        help="comma-separated rXXX addresses to watch (default: ingest all agent-ID tx)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    watch = _parse_watch(args.watch) if args.watch else None
    if watch:
        LOG.info("watching addresses: %s", ",".join(sorted(watch)))

    asyncio.run(
        run_monitor(
            network=args.network,
            db_path=Path(args.db),
            watch_addresses=watch,
            log_level=logging.DEBUG if args.verbose else logging.INFO,
        )
    )


if __name__ == "__main__":
    main()
