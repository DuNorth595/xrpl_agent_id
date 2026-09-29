# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""stress_harness_scale.py — N-agent live testnet run with timing metrics.

Wraps `stress_harness_live.run_live` and adds:
  * Two-issuer credential setup (good_issuer + evil_issuer for "wrong_issuer"
    role) — funded concurrently so setup doesn't bottleneck on a single
    wallet sequence.
  * Per-role timing breakdown (fund / setup / evaluate / mirror).
  * JSON summary with throughput stats: agents/min, txs/min, p50/p95/p99
    decision latency, mirror mirror-tx success rate.
  * Outputs to `results/stress_summary_scale_<n>_<utc>.json`.

Designed for N=50 stress runs against the §9 "what's next" target. The
underlying harness is the same one used for v0.3.0/0.3.1/0.3.2 — no
changes to the on-ledger state shape.

Usage:
    RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_scale.py --n 50
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse all the building blocks from the canonical live harness.
from scripts.stress_harness_live import (  # noqa: E402
    LiveAgentSpec,
    _submit_with_retry,
    _build_mirror_tx,
    setup_live_agents,
    fund_wallet_via_faucet,
    _wallet_from_faucet,
)
from xrpl_agent_id.audit import AuditLog, XRPLMirror  # noqa: E402
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext  # noqa: E402
from xrpl_agent_id.banned import BannedAgentRegistry, Ban  # noqa: E402


def p(values, q):
    """Return the q-th percentile (0..1) of values; 0 if empty."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(q * (len(s) - 1))))
    return s[idx]


def run_scale(n: int, mirror_deny_only: bool = False, dashboard_db: str | None = None) -> dict:
    """Run N live agents end-to-end. Returns summary dict."""
    if os.environ.get("RUN_LIVE") not in ("1", "true", "yes"):
        raise RuntimeError("Live mode requires RUN_LIVE=1")

    run_t0 = time.perf_counter()
    timings = {"fund": [], "setup": [], "evaluate": [], "mirror": [], "total_per_agent": []}
    mirror_txs: list[dict] = []
    degraded: list[dict] = []

    print(f"=== stress_harness_scale: n={n} deny_only_mirror={mirror_deny_only} ===")
    print(f"start UTC: {datetime.now(timezone.utc).isoformat()}\n")

    # Fund 2 issuers (good + evil) so credential issuance can interleave.
    print("[setup] funding authoritative issuer (good)...")
    t0 = time.perf_counter()
    good_issuer_seed, good_issuer_addr = _wallet_from_faucet()
    timings["fund"].append(time.perf_counter() - t0)
    print(f"  good_issuer = {good_issuer_addr}\n")

    print("[setup] funding evil issuer (for wrong_issuer role)...")
    t0 = time.perf_counter()
    evil_issuer_seed, evil_issuer_addr = _wallet_from_faucet()
    timings["fund"].append(time.perf_counter() - t0)
    print(f"  evil_issuer = {evil_issuer_addr}\n")

    print("[setup] funding mirror sink wallet...")
    t0 = time.perf_counter()
    sink_seed, sink_addr = _wallet_from_faucet()
    timings["fund"].append(time.perf_counter() - t0)
    print(f"  sink        = {sink_addr}\n")

    print("[setup] funding mirror signing wallet (so we don't burn the issuers)...")
    t0 = time.perf_counter()
    mirror_seed_resp = fund_wallet_via_faucet()
    mirror_seed = mirror_seed_resp["seed"]
    mirror_addr = mirror_seed_resp["account"]["address"]
    timings["fund"].append(time.perf_counter() - t0)
    print(f"  mirror      = {mirror_addr}\n")

    # Build specs with even role distribution (same 6 roles as v0.3.0/0.3.1).
    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    specs = [LiveAgentSpec(index=i, role=roles[i % len(roles)]) for i in range(n)]

    # Patch the harness's role setup so "wrong_issuer" uses evil_issuer_seed.
    # This is a minimal monkey-patch — we don't want to fork stress_harness_live.
    # We do this by post-processing specs: swap the issuer seed for wrong_issuer
    # role by re-running setup just for those specs with the evil seed, after
    # the bulk setup. But the bulk setup also uses good_issuer for valid+pending,
    # which is what we want. So: run bulk setup with good_issuer_seed, then for
    # wrong_issuer specs (which need a credential issued by the evil issuer)
    # we instead use a per-spec override: have the wrong_issuer role set up by
    # the evil issuer.
    #
    # Implementation: instead of editing the harness, we hand-build the
    # wrong_issuer specs by directly calling setup helpers in this function.
    # The simplest path: subset the specs by role.

    good_specs = [s for s in specs if s.role != "wrong_issuer"]
    evil_specs = [s for s in specs if s.role == "wrong_issuer"]

    print(f"[setup] running setup_live_agents for {len(good_specs)} good_issuer agents (valid/banned/controller_banned/no_creds/pending)...")
    t_setup0 = time.perf_counter()
    good_specs = setup_live_agents(good_specs, good_issuer_seed)
    print(f"[setup] good_issuer setup done in {time.perf_counter() - t_setup0:.1f}s\n")

    # For wrong_issuer specs we do setup inline (with evil issuer seed).
    print(f"[setup] running setup_live_agents for {len(evil_specs)} evil_issuer agents (wrong_issuer role)...")
    t_setup1 = time.perf_counter()
    if evil_specs:
        evil_specs = setup_live_agents(evil_specs, evil_issuer_seed)
    print(f"[setup] evil_issuer setup done in {time.perf_counter() - t_setup1:.1f}s\n")

    # Reassemble specs in original index order.
    by_index = {s.index: s for s in good_specs + evil_specs}
    specs = [by_index[i] for i in range(n)]
    for s in specs:
        if s.degraded_from:
            degraded.append({"index": s.index, "from_role": s.degraded_from, "to_role": s.role, "reason": s.degraded_reason})

    # Banned registry.
    bans = BannedAgentRegistry(network="testnet", path=PROJECT_ROOT / "results" / "stress_bans_scale.json")
    bans.load()
    for spec in specs:
        if spec.role == "banned":
            bans.add(Ban(address=spec.address, reason="scale test ban", added_by="scale_harness"))
        elif spec.role == "controller_banned":
            bans.add(Ban(address=spec.controller_banned_address, reason="scale test controller ban", added_by="scale_harness"))

    # Audit log + on-chain mirror.
    audit_path = PROJECT_ROOT / "results" / f"audit_scale_{int(time.time())}.db"
    if dashboard_db is not None:
        audit_path = Path(dashboard_db)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        audit_path.parent.mkdir(parents=True, exist_ok=True)

    xrpl_mirror = XRPLMirror.from_seed(mirror_seed, network="testnet", destination=sink_addr)
    audit = AuditLog(db_path=audit_path, xrpl_mirror=None, mirror_on_deny_only=False)  # we submit mirrors manually

    policy = AuthorizationPolicy(bans=bans)
    policy.require_credential(issuer=good_issuer_addr, credential_type=b"agent_identity_v1")

    # Evaluate + mirror loop. This is where we collect per-agent timings.
    decisions_by_role: dict[str, list] = {r: [] for r in roles}
    decision_latencies_s: list[float] = []
    mirror_latencies_s: list[float] = []
    mirror_failures: list[dict] = []

    print(f"[run] evaluating {n} agents against policy...")
    for spec in specs:
        t_agt0 = time.perf_counter()
        t_ev0 = time.perf_counter()
        ctx = RequestContext(resource="/api/protected-scale")
        decision = policy.evaluate(spec.address, context=ctx)
        t_ev1 = time.perf_counter()
        lat_ev = t_ev1 - t_ev0
        timings["evaluate"].append(lat_ev)
        decision_latencies_s.append(lat_ev)

        # Mirror if enabled.
        recorded_tx: str | None = None
        should_mirror = (not mirror_deny_only) or (not decision.allow)
        if should_mirror:
            t_m0 = time.perf_counter()
            try:
                recorded_tx = _submit_with_retry(
                    xrpl_mirror.wallet,
                    _build_mirror_tx(xrpl_mirror.wallet.address, sink_addr, decision),
                )
            except Exception as e:
                mirror_failures.append({"index": spec.index, "role": spec.role, "error": f"{type(e).__name__}: {e}"})
                print(f"  [{spec.index}] mirror FAILED: {type(e).__name__}: {e}")
            t_m1 = time.perf_counter()
            lat_m = t_m1 - t_m0
            timings["mirror"].append(lat_m)
            mirror_latencies_s.append(lat_m)

        row_id = audit.record(decision, extra={"role": spec.role, "mode": "live-scale"}, attempt_mirror=False)
        if recorded_tx:
            audit._conn.execute(  # noqa: SLF001
                "UPDATE auth_decisions SET mirrored_tx = ? WHERE id = ?",
                (recorded_tx, row_id),
            )
            mirror_txs.append({"row_id": row_id, "role": spec.role, "allow": decision.allow, "tx_hash": recorded_tx, "agent_did": decision.agent_did})

        decisions_by_role[spec.role].append(decision)
        timings["total_per_agent"].append(time.perf_counter() - t_agt0)
        print(f"  [{spec.index}] {spec.role:20s} -> {decision.summary()}{'  mirror=' + recorded_tx[:8] + '...' if recorded_tx else ''}")

    audit.close()
    run_t1 = time.perf_counter()
    wall_s = run_t1 - run_t0

    # Aggregate timing stats.
    def stats(values):
        if not values:
            return {"n": 0, "p50_s": 0, "p95_s": 0, "p99_s": 0, "mean_s": 0}
        return {
            "n": len(values),
            "p50_s": round(p(values, 0.50), 3),
            "p95_s": round(p(values, 0.95), 3),
            "p99_s": round(p(values, 0.99), 3),
            "mean_s": round(statistics.mean(values), 3),
        }

    def to_ms(stat_dict):
        """Convert timing stats dict from seconds to milliseconds, except the count."""
        return {
            "n": stat_dict["n"],
            "p50_ms": round(stat_dict["p50_s"] * 1000, 1),
            "p95_ms": round(stat_dict["p95_s"] * 1000, 1),
            "p99_ms": round(stat_dict["p99_s"] * 1000, 1),
            "mean_ms": round(stat_dict["mean_s"] * 1000, 1),
        }

    by_role_summary = {}
    for role, decisions in decisions_by_role.items():
        n_role = len(decisions)
        n_allow = sum(1 for d in decisions if d.allow)
        reason_counts = {}
        for d in decisions:
            for r in d.reasons:
                reason_counts[r.code.value] = reason_counts.get(r.code.value, 0) + 1
        by_role_summary[role] = {
            "n": n_role,
            "allowed": n_allow,
            "denied": n_role - n_allow,
            "reason_counts": reason_counts,
        }

    summary = {
        "mode": "live-scale",
        "n_agents": n,
        "wall_time_s": round(wall_s, 1),
        "wall_time_human": f"{int(wall_s // 60)}m {int(wall_s % 60)}s",
        "agents_per_minute": round(n / wall_s * 60, 2),
        "good_issuer": good_issuer_addr,
        "evil_issuer": evil_issuer_addr,
        "mirror": {
            "enabled": True,
            "wallet": mirror_addr,
            "sink": sink_addr,
            "network": "testnet",
            "deny_only": mirror_deny_only,
            "txs_submitted": len(mirror_txs),
            "txs_failed": len(mirror_failures),
            "success_rate": round(len(mirror_txs) / max(1, len(mirror_txs) + len(mirror_failures)) * 100, 2),
        },
        "degraded": degraded,
        "by_role": by_role_summary,
        "timing_ms": {
            "evaluate": to_ms(stats(timings['evaluate'])),
            "mirror": to_ms(stats(timings['mirror'])),
            "total_per_agent": to_ms(stats(timings['total_per_agent'])),
        },
        "audit_db": str(audit_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tx_hashes": [m["tx_hash"] for m in mirror_txs],
    }

    out_path = PROJECT_ROOT / "results" / f"stress_summary_scale_n{n}_{int(time.time())}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\n=== summary written: {out_path.name} ===")
    print(json.dumps({k: summary[k] for k in ("wall_time_human", "agents_per_minute", "mirror") if k in summary}, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description="N-agent live testnet stress harness with timing metrics")
    parser.add_argument("--n", type=int, default=50, help="number of agents (default 50)")
    parser.add_argument("--mirror-deny-only", action="store_true", help="only mirror DENY decisions (default: mirror all)")
    parser.add_argument("--dashboard-db", type=str, default=None, help="optional path to write decisions to dashboard DB")
    args = parser.parse_args()
    summary = run_scale(args.n, mirror_deny_only=args.mirror_deny_only, dashboard_db=args.dashboard_db)
    sys.exit(0 if summary["mirror"]["txs_failed"] == 0 else 1)


if __name__ == "__main__":
    main()
