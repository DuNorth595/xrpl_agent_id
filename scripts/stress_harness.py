# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Stress harness for xrpl_agent_id authorization.

Two modes:

  --mode=sim   (default; offline)
      Spin up N synthetic "phantom" agents with controlled states
      (valid / banned / missing-creds / wrong-issuer / controller-banned).
      Each phantom calls AuthorizationPolicy.evaluate().
      Exercises the decision logic without hitting the ledger.

  --mode=live  (requires RUN_LIVE=1)
      Fund N real wallets from the testnet faucet.
      Issue credentials to a subset of them.
      Pre-ban a subset by their addresses.
      Run each through the policy against the live ledger.
      All decisions logged to the audit DB.

Both modes write the same audit DB and same summary JSON.

Usage:

    # Offline simulator
    /usr/bin/python3 scripts/stress_harness.py --mode=sim --n=20

    # Live against testnet (uses existing issuer wallet from results/)
    RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness.py --mode=live --n=8
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# Allow running from scripts/ without install
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from xrpl_agent_id.authorization import (
    AuthorizationPolicy,
    AuthorizationReason,
    ReasonCode,
    RequestContext,
)
from xrpl_agent_id.audit import AuditLog
from xrpl_agent_id.banned import Ban, BannedAgentRegistry
from xrpl_agent_id.credential import Credential
from xrpl_agent_id.registry import AgentRecord, AgentRegistry


# ----------------------------------------------------------------------------
# Phantom agent definition (used by sim mode)
# ----------------------------------------------------------------------------

@dataclass
class PhantomAgent:
    """A synthetic agent with a controlled state, used by sim mode."""

    address: str
    did: str
    role: str  # 'valid' | 'banned' | 'controller_banned' | 'no_creds' | 'wrong_issuer' | 'pending'
    credentials: list = field(default_factory=list)  # list of Credential objects
    controllers: list = field(default_factory=list)  # list of controller addresses

    def __post_init__(self):
        if not self.controllers:
            self.controllers = [self.address]


def _make_credential(issuer: str, subject: str, ctype: bytes, accepted: bool = True) -> Credential:
    return Credential(
        issuer=f"did:xrpl:2:{issuer}",
        subject=f"did:xrpl:2:{subject}",
        credential_type=ctype,
        accepted=accepted,
    )


def _phantom_valid(idx: int) -> "PhantomAgent":
    addr = f"rVALID{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="valid",
        credentials=[_make_credential("rISSUER0000000000000000000000VALID", addr, b"KYC", accepted=True)],
    )


def _phantom_banned(idx: int) -> "PhantomAgent":
    addr = f"rBANNED{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="banned",
    )


def _phantom_controller_banned(idx: int) -> "PhantomAgent":
    addr = f"rMULTISIG{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="controller_banned",
        credentials=[_make_credential("rISSUER0000000000000000000000VALID", addr, b"KYC", accepted=True)],
        controllers=[addr, f"rBADC0NTRL{idx:04d}XRPLAGENTID"],
    )


def _phantom_no_creds(idx: int) -> "PhantomAgent":
    addr = f"rEMPTY{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="no_creds",
    )


def _phantom_wrong_issuer(idx: int) -> "PhantomAgent":
    addr = f"rWRONGISS{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="wrong_issuer",
        credentials=[_make_credential("rWRONGISSUERXRPLAGENTIDVALID", addr, b"KYC", accepted=True)],
    )


def _phantom_pending(idx: int) -> "PhantomAgent":
    addr = f"rPENDING{idx:04d}AGENTXRPLAGENTID"
    return PhantomAgent(
        address=addr,
        did=f"did:xrpl:2:{addr}",
        role="pending",
        credentials=[_make_credential("rISSUER0000000000000000000000VALID", addr, b"KYC", accepted=False)],
    )


PHANTOM_FACTORIES = {
    "valid": _phantom_valid,
    "banned": _phantom_banned,
    "controller_banned": _phantom_controller_banned,
    "no_creds": _phantom_no_creds,
    "wrong_issuer": _phantom_wrong_issuer,
    "pending": _phantom_pending,
}


def _phantom_to_record(phantom: "PhantomAgent") -> MagicMock:
    """Convert a PhantomAgent to a MagicMock that quacks like an AgentRecord."""
    rec = MagicMock(spec=AgentRecord)
    rec.agent_address = phantom.address
    rec.agent_did = phantom.did
    rec.controllers = phantom.controllers
    rec.credentials = phantom.credentials
    rec.summary.return_value = (
        f"Agent {phantom.address} | "
        f"{len([c for c in phantom.credentials if c.accepted])}/{len(phantom.credentials)} creds accepted | "
        f"{len(phantom.controllers)} controller(s)"
    )
    return rec


# ----------------------------------------------------------------------------
# Sim mode
# ----------------------------------------------------------------------------

def run_sim(n: int, seed: int = 0, dashboard_db: Path | None = None) -> dict:
    """Run N phantom agents through the policy. Returns summary dict.

    If dashboard_db is provided, decisions are ALSO written into the dashboard
    DB so they show up in the live dashboard. If not provided, decisions are
    written to a fresh results/audit_sim_*.db file as before.
    """
    rng = random.Random(seed)
    phantoms: list[PhantomAgent] = []
    roles = list(PHANTOM_FACTORIES.keys())
    # Even distribution across roles
    for i in range(n):
        role = roles[i % len(roles)]
        phantoms.append(PHANTOM_FACTORIES[role](i))

    # Build a policy with mocked registry
    bans = BannedAgentRegistry(network="testnet", path=PROJECT_ROOT / "results" / "stress_bans.json")
    bans.load()
    for p in phantoms:
        if p.role == "banned":
            bans.add(Ban(address=p.address, reason="sim-test ban", added_by="harness"))
        elif p.role == "controller_banned":
            for ctrl in p.controllers[1:]:
                bans.add(Ban(address=ctrl, reason="sim-test controller ban", added_by="harness"))

    if dashboard_db is not None:
        audit_path = Path(dashboard_db)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        audit_path = PROJECT_ROOT / "results" / f"audit_sim_{int(time.time())}.db"
    audit = AuditLog(db_path=audit_path)

    with patch("xrpl_agent_id.authorization.AgentRegistry") as MockReg:
        def resolve_side_effect(agent_id, use_cache=True):
            # Find the matching phantom by address
            addr = agent_id if not agent_id.startswith("did:xrpl:") else agent_id.split(":")[-1]
            for p in phantoms:
                if p.address == addr:
                    return _phantom_to_record(p)
            raise ValueError(f"unknown phantom: {agent_id}")
        reg_instance = MagicMock()
        reg_instance.resolve.side_effect = resolve_side_effect
        MockReg.return_value = reg_instance

        policy = AuthorizationPolicy(bans=bans, registry=reg_instance)
        policy.require_credential(issuer="rISSUER0000000000000000000000VALID", credential_type=b"KYC")

        decisions_by_role: dict[str, list] = {r: [] for r in roles}
        for p in phantoms:
            ctx = RequestContext(resource="/api/protected")
            decision = policy.evaluate(p.address, context=ctx)
            audit.record(decision, extra={"role": p.role, "mode": "sim"})
            decisions_by_role[p.role].append(decision)

    audit.close()

    # Summarize
    summary = {
        "mode": "sim",
        "n_phantoms": n,
        "by_role": {},
        "audit_db": str(audit_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    for role, decisions in decisions_by_role.items():
        n_role = len(decisions)
        n_allow = sum(1 for d in decisions if d.allow)
        n_deny = n_role - n_allow
        reason_counts: dict[str, int] = {}
        for d in decisions:
            for r in d.reasons:
                reason_counts[r.code.value] = reason_counts.get(r.code.value, 0) + 1
        summary["by_role"][role] = {
            "n": n_role,
            "allowed": n_allow,
            "denied": n_deny,
            "reason_counts": reason_counts,
        }
    return summary


# ----------------------------------------------------------------------------
# Live mode (placeholder; run via scripts/stress_harness_live.py — to be written)
# ----------------------------------------------------------------------------

def run_live(n: int) -> dict:
    """Run N real testnet wallets through the policy. Returns summary dict.

    NOTE: live mode is implemented in a separate module to avoid forcing
    faucet/RUN_LIVE requirements on users of sim mode. See
    scripts/stress_harness_live.py.
    """
    raise NotImplementedError(
        "live mode is implemented in scripts/stress_harness_live.py — run that directly"
    )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Stress harness for xrpl_agent_id authorization")
    parser.add_argument("--mode", choices=("sim", "live"), default="sim")
    parser.add_argument("--n", type=int, default=12, help="number of agents to spawn")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None, help="summary JSON output path")
    parser.add_argument(
        "--dashboard-db",
        type=Path,
        default=None,
        help="Optional: write decisions to the dashboard's SQLite DB so they "
             "appear live in the dashboard at port 8768. "
             "Default: a fresh results/audit_sim_*.db file.",
    )
    args = parser.parse_args()

    if args.mode == "sim":
        summary = run_sim(args.n, args.seed, dashboard_db=args.dashboard_db)
    else:
        summary = run_live(args.n)

    out = args.out or (PROJECT_ROOT / "results" / f"stress_summary_{summary['mode']}_{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"Wrote summary to {out}")

    # Pretty print
    print()
    print(f"Mode: {summary['mode']}, N={summary.get('n_phantoms', args.n)}")
    print()
    print(f"{'role':<22} {'n':>4} {'allow':>6} {'deny':>6} reasons")
    print("-" * 70)
    for role, stats in summary.get("by_role", {}).items():
        rc = ", ".join(f"{k}={v}" for k, v in stats.get("reason_counts", {}).items())
        print(f"{role:<22} {stats['n']:>4} {stats['allowed']:>6} {stats['denied']:>6} {rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
