# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""stress_harness_throughput.py — pure mirror-throughput benchmark.

The 50-agent scale harness (stress_harness_scale.py) is dominated by
per-agent setup: faucet funding + balance poll + CredentialCreate +
CredentialAccept + (sometimes) SignerListSet. That mixes three
throughput limits into one metric and confuses the picture.

This harness isolates the **mirror-only** throughput: it funds one
signing wallet, one sink, and then blasts N mirror txs (1-drop payments
with a synthetic decision_id memo) as fast as the testnet will accept
them. The synthetic decision_id is the SHA-256 of the tx index + run
nonce — it's *not* tied to a real `AuthorizationDecision` because the
goal here is the pure ledger-side rate.

Each tx is submitted via `submit_and_wait` (waits for validated ledger)
sequentially because xrpl-py's `autofill_and_sign` is not thread-safe
on a single wallet (sequence numbers race). With multiple wallets in
parallel we'd get higher throughput, but that's a separate harness.

Usage:
    RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_throughput.py --n 1000
    RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_throughput.py --n 1000 --wallets 5

Outputs:
    results/throughput_run_<utc>.json  — full per-tx latency + aggregate stats
    results/throughput_run_<utc>.csv   — per-tx CSV (one row per tx)

Stats:
    * p50 / p95 / p99 / max submit latency
    * txs/min, txs/hour
    * failure breakdown by XRPL error code
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.stress_harness_live import fund_wallet_via_faucet  # noqa: E402
from xrpl_agent_id.network import get_client  # noqa: E402
from xrpl.models.transactions import Payment, Memo  # noqa: E402
from xrpl.transaction import submit_and_wait  # noqa: E402


def p(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(q * (len(s) - 1))))
    return s[idx]


def build_memo_payload(tx_index: int, run_nonce: str) -> dict:
    """Synthetic decision_id for throughput runs (not tied to a real decision)."""
    digest = hashlib.sha256(f"throughput:{run_nonce}:{tx_index}".encode()).hexdigest().upper()
    return {
        "app": "xrpl_agent_id_audit",
        "v": 1,
        "decision_id": digest,
        "allow": (tx_index % 7 == 0),  # ~14% allow, ~86% deny — roughly matches prod mix
        "agent": f"did:xrpl:2:rTHROUGHPUT{tx_index:08d}",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def submit_one(wallet_seed: str, sink_addr: str, tx_index: int, run_nonce: str, network: str = "testnet") -> dict:
    """Submit one 1-drop mirror payment with a synthetic memo. Returns result dict."""
    from xrpl.wallet import Wallet

    wallet = Wallet.from_seed(wallet_seed)
    payload = build_memo_payload(tx_index, run_nonce)
    memo_hex = json.dumps(payload, separators=(",", ":")).encode("utf-8").hex().upper()

    tx = Payment(
        account=wallet.address,
        destination=sink_addr,
        amount="1",
        memos=[
            Memo(
                memo_data=memo_hex,
                memo_format="68747470733A2F2F7872706C2D6167656E742D69642E6578616D706C652F6175646974",
            )
        ],
    )

    t0 = time.perf_counter()
    try:
        client = get_client(network)
        response = submit_and_wait(tx, client, wallet)
        t1 = time.perf_counter()
        tx_hash = (response.result or {}).get("hash", "")
        result = response.result or {}
        engine_result = result.get("meta", {}).get("TransactionResult") if isinstance(result.get("meta"), dict) else None
        # Successful = tesSUCCESS, tefMAX_LEDGER retries, etc. all surface in result
        return {
            "index": tx_index,
            "ok": True,
            "tx_hash": tx_hash,
            "engine_result": engine_result,
            "latency_s": round(t1 - t0, 4),
        }
    except Exception as e:
        t1 = time.perf_counter()
        msg = str(e)
        # Classify common XRPL errors
        code = "unknown"
        for c in ("tefPAST_SEQ", "tefMAX_LEDGER", "tecUNFUNDED_PAYMENT", "temBAD_AMOUNT", "temMALFORMED", "ConnectionError", "TimeoutError"):
            if c in msg:
                code = c
                break
        return {
            "index": tx_index,
            "ok": False,
            "error_class": code,
            "error": msg[:200],
            "latency_s": round(t1 - t0, 4),
        }


def run_throughput(n: int, n_wallets: int = 1, network: str = "testnet") -> dict:
    if os.environ.get("RUN_LIVE") not in ("1", "true", "yes"):
        raise RuntimeError("Live mode requires RUN_LIVE=1")

    print(f"=== stress_harness_throughput: n={n} wallets={n_wallets} network={network} ===")
    print(f"start UTC: {datetime.now(timezone.utc).isoformat()}\n")

    # Fund sink + signing wallets.
    print("[setup] funding sink wallet...")
    sink_resp = fund_wallet_via_faucet()
    sink_addr = sink_resp["account"]["address"]
    print(f"  sink = {sink_addr}\n")

    print(f"[setup] funding {n_wallets} signing wallet(s)...")
    wallet_seeds: list[str] = []
    wallet_addrs: list[str] = []
    for i in range(n_wallets):
        r = fund_wallet_via_faucet()
        wallet_seeds.append(r["seed"])
        wallet_addrs.append(r["account"]["address"])
        print(f"  wallet {i} = {wallet_addrs[-1]}")

    # Give testnet ~5s to fully fund the wallets.
    print("[setup] sleeping 5s for ledger to recognize funding txs...")
    time.sleep(5)

    run_nonce = str(int(time.time()))
    print(f"\n[run] run_nonce={run_nonce} -- blasting {n} mirror txs...")
    t_run0 = time.perf_counter()

    results: list[dict] = []
    if n_wallets == 1:
        # Single wallet — sequential (xrpl-py sequence numbers are not thread-safe per-wallet).
        for i in range(n):
            r = submit_one(wallet_seeds[0], sink_addr, i, run_nonce, network)
            results.append(r)
            if i % 25 == 0 or not r["ok"]:
                marker = "OK " if r["ok"] else f"FAIL({r['error_class']})"
                print(f"  [{i}] {marker}  {r['latency_s']}s")
    else:
        # Round-robin across wallets. Each wallet handles its share sequentially,
        # but wallets run in parallel — sequence numbers don't collide because
        # each wallet has its own sequence.
        per_wallet = n // n_wallets
        remainder = n - per_wallet * n_wallets

        def worker(wallet_idx: int, count: int, start_index: int) -> list[dict]:
            out = []
            seed = wallet_seeds[wallet_idx]
            for j in range(count):
                out.append(submit_one(seed, sink_addr, start_index + j, run_nonce, network))
            return out

        with ThreadPoolExecutor(max_workers=n_wallets) as ex:
            futures = []
            cursor = 0
            for w in range(n_wallets):
                cnt = per_wallet + (1 if w < remainder else 0)
                futures.append(ex.submit(worker, w, cnt, cursor))
                cursor += cnt
            for fut in as_completed(futures):
                results.extend(fut.result())

    t_run1 = time.perf_counter()
    wall_s = t_run1 - t_run0

    # Stats
    successes = [r for r in results if r["ok"]]
    failures = [r for r in results if not r["ok"]]
    s_lats = [r["latency_s"] for r in successes]
    f_lats = [r["latency_s"] for r in failures]

    failure_codes: dict[str, int] = {}
    for r in failures:
        failure_codes[r["error_class"]] = failure_codes.get(r["error_class"], 0) + 1

    def stats(values):
        if not values:
            return {"n": 0, "p50_s": 0, "p95_s": 0, "p99_s": 0, "max_s": 0, "mean_s": 0, "min_s": 0}
        return {
            "n": len(values),
            "p50_s": round(p(values, 0.50), 4),
            "p95_s": round(p(values, 0.95), 4),
            "p99_s": round(p(values, 0.99), 4),
            "max_s": round(max(values), 4),
            "mean_s": round(statistics.mean(values), 4),
            "min_s": round(min(values), 4),
        }

    summary = {
        "mode": "throughput",
        "n_requested": n,
        "n_succeeded": len(successes),
        "n_failed": len(failures),
        "success_rate_pct": round(len(successes) / max(1, n) * 100, 2),
        "wall_time_s": round(wall_s, 1),
        "wall_time_human": f"{int(wall_s // 60)}m {int(wall_s % 60)}s",
        "txs_per_minute": round(n / wall_s * 60, 2),
        "txs_per_hour": round(n / wall_s * 3600, 1),
        "network": network,
        "n_wallets": n_wallets,
        "sink": sink_addr,
        "wallets": wallet_addrs,
        "run_nonce": run_nonce,
        "failure_codes": failure_codes,
        "success_latency": stats(s_lats),
        "failure_latency": stats(f_lats),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tx_hashes": [r["tx_hash"] for r in successes if r["tx_hash"]],
        "results": results,
    }

    out_path = PROJECT_ROOT / "results" / f"throughput_run_{int(time.time())}.json"
    out_path.write_text(json.dumps(summary, indent=2))

    csv_path = PROJECT_ROOT / "results" / f"throughput_run_{int(time.time())}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "ok", "tx_hash", "engine_result", "error_class", "latency_s"])
        for r in results:
            w.writerow([r["index"], int(r["ok"]), r.get("tx_hash", ""), r.get("engine_result", ""), r.get("error_class", ""), r["latency_s"]])

    print(f"\n=== summary ===")
    print(f"  wall        : {summary['wall_time_human']}")
    print(f"  succeeded   : {summary['n_succeeded']}/{n} ({summary['success_rate_pct']}%)")
    print(f"  txs/min     : {summary['txs_per_minute']}")
    print(f"  p50 / p95 / p99 (success latency, s): {summary['success_latency']['p50_s']} / {summary['success_latency']['p95_s']} / {summary['success_latency']['p99_s']}")
    print(f"  failure codes: {failure_codes}")
    print(f"  JSON: {out_path}")
    print(f"  CSV : {csv_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Pure mirror-throughput benchmark")
    parser.add_argument("--n", type=int, default=1000, help="number of mirror txs to submit (default 1000)")
    parser.add_argument("--wallets", type=int, default=1, help="number of signing wallets to round-robin across (default 1)")
    parser.add_argument("--network", type=str, default="testnet", choices=["testnet", "mainnet"], help="XRPL network")
    args = parser.parse_args()
    summary = run_throughput(args.n, args.wallets, args.network)
    sys.exit(0 if summary["n_failed"] == 0 else 1)


if __name__ == "__main__":
    main()
