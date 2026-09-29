# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Live testnet stress harness for xrpl_agent_id authorization.

Spins up N real testnet wallets, funds them from the faucet, runs each
through AuthorizationPolicy against the live ledger. Mixed states:
  - Some agents get a valid KYC credential
  - Some are pre-banned
  - Some are controller-banned (multi-sig accounts where one signer is banned)
  - Some get issued but don't accept the credential

Cost: ~6 transactions per funded agent (3 for the issuer's cred + 3 for
the subject's accept + DID). At default n=4, ~12-16 transactions, ~300 drops
total. Run with caution on testnet — keep n small.

Usage:
    RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_live.py --n=4

Note: this script is separate from stress_harness.py so that sim mode
can run without forcing the testnet faucet / live creds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from xrpl.wallet import Wallet
from xrpl.transaction import submit_and_wait  # noqa: F401  (used in _submit_with_retry)
from xrpl.models.transactions import (
    Payment,
    Memo,
    SignerListSet,
    SignerEntry,
)  # noqa: F401

from xrpl_agent_id import AgentIdentity, Authority, CredentialType
from xrpl_agent_id.audit import AuditLog
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext
from xrpl_agent_id.banned import Ban, BannedAgentRegistry
from xrpl_agent_id.credential import Credential
from xrpl_agent_id.network import get_client, get_network


# ----------------------------------------------------------------------------
# Faucet + funding
# ----------------------------------------------------------------------------

def fund_wallet_via_faucet() -> dict:
    """Hit the testnet faucet for a fresh wallet. Returns {seed, address, ...}."""
    req = urllib.request.Request(
        "https://faucet.altnet.rippletest.net/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wait_for_balance(address: str, min_drops: int = 50_000_000, timeout_s: int = 60) -> int:
    from xrpl.models.requests import AccountInfo

    client = get_client("testnet")
    deadline = time.time() + timeout_s
    last = 0
    while time.time() < deadline:
        try:
            resp = client.request(AccountInfo(account=address))
            last = int(resp.result["account_data"]["Balance"])
            if last >= min_drops:
                return last
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Account {address} never reached {min_drops} drops (last: {last})")


# ----------------------------------------------------------------------------
# Live role orchestration
# ----------------------------------------------------------------------------

@dataclass
class LiveAgentSpec:
    """Plan for one agent in the live harness."""

    index: int
    role: str  # 'valid' | 'banned' | 'controller_banned' | 'no_creds' | 'wrong_issuer' | 'pending'
    address: str = ""
    did: str = ""
    seed: str = ""
    controller_banned_address: str = ""
    controller_banned_seed: str = ""
    # Only set when shape='quarantined': a clean co-signer whose presence
    # raises quorum to 2 (operationally freezes the account).
    controller_banned_sentinel_address: str = ""
    controller_banned_sentinel_seed: str = ""
    # 'compromised' (default) — banned addr is the sole signer (realistic
    #                       compromise, full authority over the account).
    # 'quarantined'         — banned + sentinel at 1:1 weights, quorum 2
    #                       (account is operationally dead but detection
    #                       still fires).
    # Only meaningful when role == 'controller_banned'.
    controller_banned_shape: str = "compromised"
    controller_banned_signerlist_tx: str = ""  # hash of the SignerListSet
    # Set when setup fails after retries; the agent is rolled to no_creds
    # and these fields record what role it was supposed to be.
    degraded_from: str = ""
    degraded_reason: str = ""


def _wallet_from_faucet() -> tuple[str, str]:
    """Fund a fresh wallet, return (seed, address)."""
    data = fund_wallet_via_faucet()
    seed = data["seed"]
    address = data["account"]["address"]
    wait_for_balance(address)
    return seed, address


def _submit_with_retry(wallet, tx, *, max_attempts: int = 3) -> str:
    """Submit a transaction with retry-on-tefPAST_SEQ / tefMAX_LEDGER.

    Returns the tx hash string. Raises on non-transient failure or after
    max_attempts is exhausted.
    """
    from xrpl.transaction import submit_and_wait
    from xrpl_agent_id.network import get_client

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            client = get_client("testnet")
            response = submit_and_wait(tx, client, wallet)
            return (response.result or {}).get("hash", "") or ""
        except Exception as e:  # xrpl raises XRPLReliableSubmissionException
            last_exc = e
            msg = str(e)
            if "tefPAST_SEQ" in msg or "tefMAX_LEDGER" in msg or "LastLedgerSequence" in msg:
                import time as _t
                _t.sleep(2 + attempt)
                continue
            raise
    raise last_exc  # type: ignore[misc]


def _build_mirror_tx(wallet_address: str, destination: str, decision):
    """Build a 0-XRP payment with a memo describing the decision.

    Mirrors XRPLMirror.submit() but returns the tx object so the caller can
    pass it through _submit_with_retry(). The destination MUST differ from
    the source — XRPL rejects self-payments for XRP (tesSUCCESS but never
    confirms) so we send to a fresh sink wallet funded alongside the mirror.
    """
    import hashlib
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
    return Payment(
        account=wallet_address,
        destination=destination,
        amount="1",  # 1 drop — the smallest valid XRP amount. 0-XRP fails
                     # temBAD_AMOUNT for non-ACCountRoot destinations.
        memos=[
            Memo(
                memo_data=memo_data.encode("utf-8").hex().upper(),
                memo_format="68747470733A2F2F7872706C2D6167656E742D69642E6578616D706C652F6175646974",
            )
        ],
    )


def _issue_and_accept_credential(
    issuer_seed: str, subject_seed: str, credential_type: bytes = b"agent_identity_v1"
) -> tuple[Credential, str]:
    """Issue + accept a credential between two funded wallets.

    Returns (issued_credential, accept_tx_hash).
    """
    issuer = Authority.from_seed(issuer_seed, network="testnet")
    subject = AgentIdentity.from_seed(subject_seed, network="testnet")

    # Issue (returns Credential, submits CredentialCreate)
    issued = issuer.issue_credential(
        subject=subject.address,
        credential_type=credential_type,
    )
    # Accept (returns tx hash, submits CredentialAccept)
    accept_tx = subject.accept_credential(
        issuer=issuer.address,
        credential_type=credential_type,
    )
    return issued, accept_tx


def _setup_controller_banned_onchain(
    agent_seed: str,
    agent_address: str,
    banned_address: str,
    sentinel_address: str | None,
    shape: str = "compromised",
) -> str:
    """Publish a SignerListSet on the agent account that includes a banned
    controller. Returns the SignerListSet tx hash.

    IMPORTANT: XRPL forbids the master account from appearing in its own
    SignerList — only OTHER accounts can be co-signers. So the master key
    is always implicitly the lone signer until a SignerListSet is published,
    at which point the SignerList replaces it. That means:

      shape='compromised' (default): master removed; banned address is the
                  SOLE signer at weight 1, quorum 1. Realistic compromise
                  scenario — banned addr has full authority over the account.
                  AgentRegistry resolves controllers = [banned_addr];
                  AuthorizationPolicy fires CONTROLLER_BANNED.

      shape='quarantined':  master removed; banned + a clean sentinel, each
                  at weight 1, quorum 2. Both must sign → operationally
                  dead, but detection still works because AgentRegistry sees
                  the banned co-signer as a controller. sentinel_address
                  MUST be provided when shape='quarantined'.

    The sentinel seed is never used to sign anything in this harness
    (quorum 2 is unreachable since the master is gone). It's recorded on
    the spec purely for audit reproducibility.

    Args:
        agent_seed:    seed of the agent's master key (signs the SignerListSet).
        agent_address: agent account r-address.
        banned_address:r-address of the banned co-signer.
        sentinel_address: required for 'quarantined'; ignored for 'compromised'.
        shape:         'compromised' (default) or 'quarantined'.

    Raises:
        ValueError: if shape is unrecognized or sentinel missing for quarantined.
        RuntimeError: if submit_and_wait returns a non-success result, or
                      if the seed doesn't match the agent_address.
    """
    if shape not in ("compromised", "quarantined"):
        raise ValueError(f"unknown controller_banned_shape: {shape!r}")
    if shape == "quarantined" and not sentinel_address:
        raise ValueError("shape='quarantined' requires a sentinel_address")

    master = Wallet.from_seed(agent_seed)
    if master.address != agent_address:
        # Sanity: the agent_seed we were given should match the agent_address
        # we already funded. If they diverge, refuse rather than silently
        # publish a signerlist on the wrong account.
        raise RuntimeError(
            f"seed/address mismatch: seed yields {master.address}, "
            f"expected {agent_address}"
        )

    if shape == "compromised":
        # Master removed; banned address is the only signer.
        signer_entries = [
            SignerEntry(account=banned_address, signer_weight=1),
        ]
        signer_quorum = 1
    else:  # 'quarantined'
        # Master removed; banned + sentinel, both weight 1, quorum 2.
        signer_entries = [
            SignerEntry(account=banned_address, signer_weight=1),
            SignerEntry(account=sentinel_address, signer_weight=1),  # type: ignore[arg-type]
        ]
        signer_quorum = 2

    tx = SignerListSet(
        account=master.address,
        signer_quorum=signer_quorum,
        signer_entries=signer_entries,
    )

    client = get_client("testnet")
    response = submit_and_wait(tx, client, master)
    result = response.result or {}
    tx_hash = result.get("hash", "") or ""
    engine_result = (result.get("meta") or {}).get("TransactionResult")
    if engine_result != "tesSUCCESS":
        raise RuntimeError(
            f"SignerListSet did not tesSUCCESS: result={engine_result}, "
            f"hash={tx_hash}, full={result}"
        )
    return tx_hash


def setup_live_agents(specs: list[LiveAgentSpec], issuer_seed: str) -> list[LiveAgentSpec]:
    """Fund each agent, set up the role-specific state on the ledger.

    If a per-role setup hits a transient ledger error (tefPAST_SEQ, tefMAX_LEDGER)
    we retry that role up to 3 times. After all retries are exhausted we
    degrade gracefully: the agent's role is reset to "no_creds" so the policy
    evaluation can still run, and a flag is set so the summary reflects the
    degraded state.
    """
    print(f"\n--- Setting up {len(specs)} live agents ---")
    for spec in specs:
        original_role = spec.role
        print(f"  [{spec.index}] role={spec.role} ... ", end="", flush=True)
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                seed, addr = _wallet_from_faucet()
                spec.seed = seed
                spec.address = addr
                spec.did = f"did:xrpl:2:{addr}"

                if spec.role == "valid":
                    _issue_and_accept_credential(issuer_seed, seed)
                elif spec.role == "pending":
                    issuer = Authority.from_seed(issuer_seed, network="testnet")
                    issuer.issue_credential(subject=addr, credential_type=b"agent_identity_v1")
                elif spec.role == "controller_banned":
                    ctrl_seed, ctrl_addr = _wallet_from_faucet()
                    spec.controller_banned_seed = ctrl_seed
                    spec.controller_banned_address = ctrl_addr
                    _issue_and_accept_credential(issuer_seed, seed)
                    # Publish a real on-chain SignerListSet that includes the
                    # banned co-signer. AgentRegistry resolves controllers from
                    # this list, so AuthorizationPolicy will hit
                    # CONTROLLER_BANNED against this address.
                    sentinel_addr: str | None = None
                    if spec.controller_banned_shape == "quarantined":
                        sentinel_seed, sentinel_addr = _wallet_from_faucet()
                        spec.controller_banned_sentinel_seed = sentinel_seed
                        spec.controller_banned_sentinel_address = sentinel_addr
                    spec.controller_banned_signerlist_tx = _setup_controller_banned_onchain(
                        agent_seed=seed,
                        agent_address=addr,
                        banned_address=ctrl_addr,
                        sentinel_address=sentinel_addr,
                        shape=spec.controller_banned_shape,
                    )
                elif spec.role in ("banned", "no_creds", "wrong_issuer"):
                    if spec.role == "wrong_issuer":
                        wrong_issuer_seed, _ = _wallet_from_faucet()
                        _issue_and_accept_credential(wrong_issuer_seed, seed)
                last_err = None
                break
            except Exception as e:
                last_err = e
                msg = str(e)
                transient = (
                    "tefPAST_SEQ" in msg
                    or "tefMAX_LEDGER" in msg
                    or "LastLedgerSequence" in msg
                    or "ConnectionError" in msg
                )
                if transient and attempt < 3:
                    print(f"(retry {attempt}: {type(e).__name__}) ", end="", flush=True)
                    import time as _t
                    # Exponential backoff: 4s, 8s. These ledger races are
                    # usually 1-2 ledgers behind, so 8s is enough for 4-8
                    # ledgers of recovery on a 4-ledger LastLedgerSequence.
                    _t.sleep(4 * attempt)
                    continue
                break
        if last_err is not None:
            # Graceful degradation: roll this agent to no_creds so the
            # evaluation loop can still run. Flag it in the spec so the
            # summary table reflects what happened.
            spec.role = "no_creds"
            spec.degraded_from = original_role
            spec.degraded_reason = f"{type(last_err).__name__}: {last_err}"
            print(
                f"DEGRADED to no_creds after {3} attempts "
                f"({type(last_err).__name__})"
            )
            continue
        print("done")
    return specs


# ----------------------------------------------------------------------------
# Main run
# ----------------------------------------------------------------------------

def run_live(
    n: int,
    dashboard_db: Path | None = None,
    mirror_seed: str | None = None,
    mirror_network: str = "testnet",
    mirror_deny_only: bool = False,
    collect_seeds: dict | None = None,
) -> dict:
    """Run N real testnet agents through the policy. Returns summary.

    If dashboard_db is provided, decisions are also written to the dashboard
    DB so they appear live in the dashboard UI.

    If mirror_seed is provided, each decision is also mirrored to the XRPL
    ledger as a memo on a 0-XRP self-payment. By default all decisions are
    mirrored; pass mirror_deny_only=True to mirror only DENYs (cheaper,
    matches the production default in audit.AuditLog).
    """
    if os.environ.get("RUN_LIVE") not in ("1", "true", "yes"):
        raise RuntimeError("Live mode requires RUN_LIVE=1 environment variable")

    # Fund the "good" issuer (used for valid + pending roles).
    print("Funding authoritative issuer...")
    good_issuer_seed, good_issuer_addr = _wallet_from_faucet()
    print(f"  good issuer: {good_issuer_addr}")

    # Build specs with even role distribution.
    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    specs = [LiveAgentSpec(index=i, role=roles[i % len(roles)]) for i in range(n)]
    specs = setup_live_agents(specs, good_issuer_seed)

    # Build deny list
    bans = BannedAgentRegistry(network="testnet", path=PROJECT_ROOT / "results" / "stress_bans_live.json")
    bans.load()
    for spec in specs:
        if spec.role == "banned":
            bans.add(Ban(address=spec.address, reason="live test ban", added_by="harness"))
        elif spec.role == "controller_banned":
            bans.add(Ban(address=spec.controller_banned_address, reason="live test controller ban", added_by="harness"))

    if dashboard_db is not None:
        audit_path = Path(dashboard_db)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        audit_path = PROJECT_ROOT / "results" / f"audit_live_{int(time.time())}.db"

    # Optional on-chain mirror of every decision
    xrpl_mirror = None
    mirror_addr = ""
    mirror_destination = "rrrrrrrrrrrrrrrrrrrrrrrrLvLvTp"
    if mirror_seed:
        from xrpl_agent_id.audit import XRPLMirror

        # Fund a sink wallet so the mirror can send 0-XRP somewhere that
        # isn't itself (XRPL rejects XRP self-payments).
        sink_seed, sink_addr = _wallet_from_faucet()
        mirror_destination = sink_addr
        xrpl_mirror = XRPLMirror.from_seed(mirror_seed, network=mirror_network, destination=mirror_destination)
        mirror_addr = xrpl_mirror.wallet.address
        print(f"  mirror wallet: {mirror_addr} (network={mirror_network})")
        print(f"  mirror sink  : {mirror_destination} (faucet-funded, 0-XRP destination)")
        if collect_seeds is not None:
            collect_seeds["sink"] = sink_seed

    audit = AuditLog(
        db_path=audit_path,
        xrpl_mirror=xrpl_mirror,
        mirror_on_deny_only=mirror_deny_only,
    )

    policy = AuthorizationPolicy(bans=bans)
    policy.require_credential(issuer=good_issuer_addr, credential_type=b"agent_identity_v1")

    decisions_by_role: dict[str, list] = {r: [] for r in roles}
    mirror_txs: list[dict] = []
    degraded: list[dict] = []
    for spec in specs:
        if spec.degraded_from:
            degraded.append(
                {
                    "index": spec.index,
                    "from_role": spec.degraded_from,
                    "to_role": spec.role,
                    "reason": spec.degraded_reason,
                }
            )
        ctx = RequestContext(resource="/api/protected-live")
        decision = policy.evaluate(spec.address, context=ctx)
        # If mirror is configured, manually submit it here with retry so a
        # single tefPAST_SEQ doesn't fail the whole run.
        recorded_tx: str | None = None
        if xrpl_mirror is not None and not mirror_deny_only:
            # Always mirror in this path (mirror_deny_only==False branch)
            try:
                recorded_tx = _submit_with_retry(
                    xrpl_mirror.wallet,
                    _build_mirror_tx(xrpl_mirror.wallet.address, mirror_destination, decision),
                )
            except Exception as e:
                recorded_tx = None
                print(f"      mirror submit failed: {type(e).__name__}: {e}")
        elif xrpl_mirror is not None and mirror_deny_only and not decision.allow:
            try:
                recorded_tx = _submit_with_retry(
                    xrpl_mirror.wallet,
                    _build_mirror_tx(xrpl_mirror.wallet.address, mirror_destination, decision),
                )
            except Exception as e:
                recorded_tx = None
                print(f"      mirror submit failed: {type(e).__name__}: {e}")

        row_id = audit.record(decision, extra={"role": spec.role, "mode": "live"}, attempt_mirror=False)
        # If we already submitted, persist the tx hash on the row.
        if recorded_tx:
            audit._conn.execute(  # noqa: SLF001 - intentional direct write
                "UPDATE auth_decisions SET mirrored_tx = ? WHERE id = ?",
                (recorded_tx, row_id),
            )
            mirror_txs.append(
                {
                    "row_id": row_id,
                    "role": spec.role,
                    "allow": decision.allow,
                    "tx_hash": recorded_tx,
                    "agent_did": decision.agent_did,
                }
            )
        decisions_by_role[spec.role].append(decision)
        print(
            f"  [{spec.index}] {spec.role:20s} -> {decision.summary()}"
            f"{'  mirror=' + recorded_tx if recorded_tx else ''}"
        )

    audit.close()

    summary = {
        "mode": "live",
        "n_agents": n,
        "good_issuer": good_issuer_addr,
        "mirror": {
            "enabled": xrpl_mirror is not None,
            "wallet": mirror_addr,
            "destination": mirror_destination,
            "network": mirror_network,
            "deny_only": mirror_deny_only,
            "txs": mirror_txs,
        },
        "degraded": degraded,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Live testnet stress harness")
    parser.add_argument("--n", type=int, default=6)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--dashboard-db",
        type=Path,
        default=None,
        help="Write decisions to the dashboard DB so they appear live at port 8768.",
    )
    parser.add_argument(
        "--xrpl-mirror-seed",
        type=str,
        default=None,
        help="Seed of a funded wallet that submits an XRPL memo for every decision. "
        "When omitted, the harness funds a fresh mirror wallet via the testnet "
        "faucet (default).",
    )
    parser.add_argument(
        "--xrpl-network",
        type=str,
        default="testnet",
        help="Network the mirror wallet operates on (default: testnet).",
    )
    parser.add_argument(
        "--mirror-deny-only",
        action="store_true",
        help="Only mirror DENY decisions on-chain (default: mirror every decision).",
    )
    parser.add_argument(
        "--save-seeds",
        type=Path,
        default=None,
        help="If set, write the mirror wallet seed (and any others) to this "
        "600-mode JSON file. Useful for follow-up verification without "
        "re-running the harness. Never commit this file.",
    )
    args = parser.parse_args()

    mirror_seed = args.xrpl_mirror_seed
    mirror_seeds: dict[str, str] = {}
    if mirror_seed is None:
        # Fund a fresh mirror wallet from the faucet so every decision can be
        # written on-chain. We persist the seed to a 600-mode file so the
        # caller (or a follow-up audit) can verify later without printing it.
        seed, addr = _wallet_from_faucet()
        mirror_seed = seed
        mirror_seeds["mirror"] = seed
        print(f"  mirror wallet funded via faucet: {addr}")
    summary = run_live(
        args.n,
        dashboard_db=args.dashboard_db,
        mirror_seed=mirror_seed,
        mirror_network=args.xrpl_network,
        mirror_deny_only=args.mirror_deny_only,
        collect_seeds=mirror_seeds,
    )

    if args.save_seeds and mirror_seeds:
        args.save_seeds.parent.mkdir(parents=True, exist_ok=True)
        args.save_seeds.write_text(json.dumps(mirror_seeds, indent=2))
        try:
            args.save_seeds.chmod(0o600)
        except Exception:
            pass
        print(f"  saved mirror seed to {args.save_seeds} (chmod 600)")

    out = args.out or (PROJECT_ROOT / "results" / f"stress_summary_live_{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nWrote summary to {out}")

    print()
    print(f"Mode: {summary['mode']}, N={summary['n_agents']}")
    if summary.get("degraded"):
        print(f"\nDegraded setups ({len(summary['degraded'])}):")
        for d in summary["degraded"]:
            print(f"  [{d['index']}] {d['from_role']} -> {d['to_role']}  ({d['reason'][:80]}...)")
    if summary.get("mirror", {}).get("enabled"):
        m = summary["mirror"]
        print(f"Mirror: enabled  wallet={m['wallet']}  network={m['network']}  deny_only={m['deny_only']}")
        print(f"  {len(m['txs'])} on-chain decision memos:")
        for tx in m["txs"]:
            tag = "ALLOW" if tx["allow"] else "DENY"
            print(f"    {tag:5s}  {tx['tx_hash']}  ({tx['role']})")
    else:
        print("Mirror: disabled (local-only audit)")
    print()
    print(f"{'role':<22} {'n':>4} {'allow':>6} {'deny':>6} reasons")
    print("-" * 70)
    for role, stats in summary["by_role"].items():
        rc = ", ".join(f"{k}={v}" for k, v in stats.get("reason_counts", {}).items())
        print(f"{role:<22} {stats['n']:>4} {stats['allowed']:>6} {stats['denied']:>6} {rc}")
    return 0


if __name__ == "__main__":
    import os  # late import for the env check
    sys.exit(main())
