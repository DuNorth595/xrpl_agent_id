# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Mainnet stress harness for xrpl_agent_id authorization.

The mainnet counterpart to ``stress_harness_live.py``. That script is
hardwired to testnet: it funds every wallet from the testnet faucet, which
does not exist on mainnet. This variant funds every wallet from a single
treasury account instead, and switches every ``network="testnet"`` to
``network="mainnet"``.

What is different from the testnet harness:

  - **No faucet.** Wallets are funded by a ``Payment`` from the treasury.
  - **One treasury.** Its seed comes from the macOS keychain, never from
    an argument on the command line and never from a file on disk.
  - **Mainnet throughout** -- ``network="mainnet"`` for every client,
    identity, authority, mirror, and ban list.
  - **The treasury pays.** Every agent wallet, the mirror wallet, the
    sink, the controller-banned co-signer, and the wrong-issuer wallet are
    all funded from the treasury, which carries a 1 XRP base reserve each.

This spends REAL XRP. The treasury is a throwaway account funded solely
for this run; see ``docs/internal/MAINNET_STRESS_RUN_PLAN.md``.

Usage:
    RUN_LIVE=1 python scripts/stress_harness_mainnet.py --n=6

The treasury seed is read from the macOS keychain. If it is missing, the
script refuses to run rather than falling back to anything else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import xrpl.core.keypairs.main as _kp
from xrpl.core.keypairs import derive_classic_address
from xrpl.models.transactions import Memo, Payment, SignerEntry, SignerListSet

from xrpl_agent_id import AgentIdentity, Authority
from xrpl_agent_id.audit import AuditLog, XRPLMirror
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext
from xrpl_agent_id.banned import Ban, BannedAgentRegistry
from xrpl_agent_id.credential import Credential
from xrpl_agent_id.network import get_client
from xrpl_agent_id.tx_submit import submit_with_retry

#: Keychain service holding the mainnet treasury seed.
# The macOS keychain service that holds the treasury seed.
#
# The service name points at a secret store, so it is not published in
# this repository. Set XRPL_STRESS_KEYCHAIN_SERVICE to the service name
# used by your local keychain entry.
KEYCHAIN_SERVICE = os.environ.get(
    "XRPL_STRESS_KEYCHAIN_SERVICE", "xrpl-mainnet-stress"
)

#: Base reserve per account, in drops (1 XRP). Read off the live ledger.
RESERVE_BASE_DROPS = 1_000_000

#: Owner reserve per owned ledger object, in drops (0.2 XRP).
RESERVE_INC_DROPS = 200_000

#: Median transaction fee in drops. Budgeted at the median, not the base
#: fee, because the open-ledger cost rises exponentially past the soft
#: limit and a run that under-funds dies with telINSUF_FEE_P.
FEE_MEDIAN_DROPS = 5_000

#: Headroom multiplier on the fee line of the budget. Reserve figures are
#: exact protocol constants and are not inflated; only the fee estimate
#: carries a margin, because the open-ledger cost can exceed the median.
FEE_HEADROOM = 1.5

#: Maximum transaction fee this harness will sign, in drops. The base fee
#: is 10 drops and the median is 5,000; 50,000 is 10x the median -- high
#: enough that ordinary congestion does not trip it, low enough that a 50x
#: premium cannot be signed silently.
MAX_FEE_DROPS = 50_000

#: How many ledger objects an agent account holds at peak: one DID plus
#: one credential.
AGENT_OWNED_OBJECTS = 2

#: Transactions an agent account signs: CredentialCreate (issuer side),
#: CredentialAccept, DIDSet. The issuer signs the CredentialCreate, not the
#: agent, but the agent still needs to cover its own accept + DID.
AGENT_TX_COUNT = 2


def agent_funding_drops(owned_objects: int = AGENT_OWNED_OBJECTS) -> int:
    """Exactly what one agent account needs, in drops.

    1 XRP base reserve + 0.2 XRP per owned object it holds + fees for the
    transactions it signs.

    An agent holds a DID and, once it accepts, a credential -- 0.4 XRP of
    owner reserve. It signs CredentialAccept and DIDSet. The
    CredentialCreate is signed by the *issuer*, so the agent does not pay
    that fee.

    An agent in the ``controller_banned`` role ALSO holds a SignerList --
    the SignerListSet is submitted on the agent's own account, so the
    owner reserve for that object is charged to the agent, not to the
    co-signer. Pass owned_objects=3 for that role.

    Funding the flat 2 XRP instead would drain the treasury faster than
    necessary -- and on a tight budget that is the difference between
    fitting and not.
    """
    return (
        RESERVE_BASE_DROPS
        + owned_objects * RESERVE_INC_DROPS
        + AGENT_TX_COUNT * FEE_MEDIAN_DROPS
    )


def plain_wallet_funding_drops() -> int:
    """Funding for an account that holds no owned objects.

    The mirror wallet, the sink, and the controller co-signer just need
    their base reserve plus fees. The wrong issuer does NOT belong here
    -- it creates a credential, so it holds an owned object.
    """
    return RESERVE_BASE_DROPS + 4 * FEE_MEDIAN_DROPS


def issuer_funding_drops(n_credentials: int) -> int:
    """Funding for the issuer account.

    The issuer holds a credential object for EVERY credential it creates --
    that owner reserve is charged to the ISSUER, not the subject. It also
    signs the CredentialCreate transaction for each one.

    Getting this wrong fails the run with tecINSUFFICIENT_RESERVE, which
    is what happened on the first real attempt on 2026-10-28.
    """
    return (
        RESERVE_BASE_DROPS
        + n_credentials * RESERVE_INC_DROPS
        + n_credentials * FEE_MEDIAN_DROPS
    )


# ----------------------------------------------------------------------------
# Treasury
# ----------------------------------------------------------------------------

def treasury_seed() -> str:
    """Read the treasury seed from the macOS keychain.

    Raises RuntimeError if the keychain entry is missing. There is no
    fallback: a mainnet run must never source a seed from anywhere else.
    """
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
    )
    seed = (proc.stdout or "").strip()
    if not seed:
        raise RuntimeError(
            f"no seed in keychain service {KEYCHAIN_SERVICE!r} -- "
            "the mainnet treasury is not set up"
        )
    return seed


def treasury_address(seed: str) -> str:
    """Derive the treasury's classic address from its seed."""
    result = _kp.derive_keypair(seed)
    public_key = result[0] if isinstance(result, tuple) else result
    return derive_classic_address(public_key)


def _submit_with_retry(wallet, tx, *, max_attempts: int = 3) -> str:
    """Submit a transaction, retrying transient ledger races.

    Uses the library's own ``submit_with_retry`` rather than xrpl-py's
    ``submit_and_wait``. That is not a stylistic choice: ``submit_and_wait``
    takes an ``xrpl.wallet.Wallet``, and v0.5.0 introduced ``WalletProvider``
    for signers that are not wallets at all. The library's helper
    re-autofills and re-signs per attempt in provider mode, which is what
    makes a retry safe.

    Returns the tx hash. Raises on non-transient failure or after the
    attempts are exhausted.
    """
    client = get_client("mainnet")
    result = submit_with_retry(
        tx,
        client,
        wallet,
        max_attempts=max_attempts,
        max_fee_drops=MAX_FEE_DROPS,
    )
    if result is None:
        raise RuntimeError("submit_with_retry returned no result payload")
    engine = (result.get("meta") or {}).get("TransactionResult")
    if engine and engine != "tesSUCCESS":
        raise RuntimeError(
            f"transaction did not tesSUCCESS: {engine} (hash={result.get('hash')})"
        )
    return result.get("hash", "") or ""


def wait_for_balance(address: str, min_drops: int, timeout_s: int = 60) -> int:
    """Poll account_info until the balance reaches min_drops."""
    from xrpl.models.requests import AccountInfo

    client = get_client("mainnet")
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
    raise TimeoutError(
        f"Account {address} never reached {min_drops} drops (last: {last})"
    )


def fund_from_treasury(
    treasury_wallet, address: str, amount_drops: int
) -> str:
    """Fund `address` from the treasury. Returns the Payment tx hash."""
    tx = Payment(
        account=treasury_wallet.address,
        destination=address,
        amount=str(amount_drops),
    )
    tx_hash = _submit_with_retry(treasury_wallet, tx)
    wait_for_balance(address, amount_drops)
    return tx_hash


def persist_seeds(seeds: dict, path: Path) -> None:
    """Write the seeds to a 600-mode file. Called after EVERY new wallet.

    This is called incrementally rather than once at the end because a
    crash mid-run must not cost the seeds of wallets already funded. On
    2026-10-28 a dry run leaked 4 XRP that could not be reclaimed because
    the seeds were never written.
    """
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seeds, indent=2))
    try:
        path.chmod(0o600)
    except Exception:
        pass


def new_wallet_from_treasury(
    treasury_wallet, seeds: dict, label: str, amount_drops: int, seeds_path: Path | None = None
):
    """Create a fresh wallet and fund it from the treasury.

    Returns (seed, address). The seed is recorded in `seeds` so the caller
    can persist it for later verification without it ever hitting stdout.

    The seed MUST be persisted before any transaction that depends on it.
    A wallet whose seed is lost is unrecoverable money -- there is no
    AccountDelete without the master key.
    """
    from xrpl.wallet import Wallet

    wallet = Wallet.create()
    seed = wallet.seed
    address = wallet.classic_address
    if not seed or not address:
        raise RuntimeError("Wallet.create() did not return a usable seed/address")
    seeds[label] = seed
    # Persist BEFORE funding. If the funding transaction is the thing that
    # goes wrong, the seed must already be on disk.
    if seeds_path is not None:
        persist_seeds(seeds, seeds_path)
    fund_from_treasury(treasury_wallet, address, amount_drops)
    return seed, address


# ----------------------------------------------------------------------------
# Role setup
# ----------------------------------------------------------------------------

def _issue_and_accept_credential(
    issuer_seed: str, subject_seed: str, credential_type: bytes = b"agent_identity_v1"
) -> tuple[Credential, str]:
    """Issue + accept a credential between two funded wallets."""
    issuer = Authority.from_seed(issuer_seed, network="mainnet")
    subject = AgentIdentity.from_seed(subject_seed, network="mainnet")

    issued = issuer.issue_credential(
        subject=subject.address,
        credential_type=credential_type,
    )
    accept_tx = subject.accept_credential(
        issuer=issuer.address,
        credential_type=credential_type,
    )
    return issued, accept_tx


def _setup_controller_banned_onchain(
    agent_seed: str,
    agent_address: str,
    banned_address: str,
) -> str:
    """Publish a SignerListSet that makes the banned address a signer.

    XRPL forbids the master account from appearing in its own SignerList,
    so the master key is implicitly the lone signer until a SignerListSet
    is published -- at which point the SignerList replaces it. That gives
    the banned address full authority over the account, which is the
    realistic compromise shape.
    """
    from xrpl.wallet import Wallet

    master = Wallet.from_seed(agent_seed)
    if master.classic_address != agent_address:
        raise RuntimeError(
            f"seed/address mismatch: seed yields {master.classic_address}, "
            f"expected {agent_address}"
        )

    tx = SignerListSet(
        account=master.classic_address,
        signer_quorum=1,
        signer_entries=[SignerEntry(account=banned_address, signer_weight=1)],
    )
    # A plain xrpl.wallet.Wallet is a valid signer here -- submit_with_retry
    # routes non-provider signers down xrpl-py's own submit_and_wait path.
    return _submit_with_retry(master, tx)


def _build_mirror_tx(wallet_address: str, destination: str, decision):
    """Build the 1-drop audit memo Payment.

    The destination MUST differ from the source -- XRPL rejects XRP
    self-payments.
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
        amount="1",
        memos=[
            Memo(
                memo_data=memo_data.encode("utf-8").hex().upper(),
                memo_format="68747470733A2F2F7872706C2D6167656E742D69642E6578616D706C652F6175646974",
            )
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Mainnet stress harness")
    parser.add_argument("--n", type=int, default=6)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--save-seeds",
        type=Path,
        default=None,
        help="Write every generated seed to this 600-mode JSON file so a "
        "follow-up audit can verify without re-running. Never commit it. "
        "Defaults to a 600-mode file under results/ -- ALWAYS persist the "
        "seeds: a wallet whose seed is lost is unrecoverable money.",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Skip the on-chain audit mirror (fewer transactions).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exercise every code path against a mocked ledger. Spends no "
        "XRP and submits no transactions. Use this to validate the harness "
        "before a real mainnet run.",
    )
    args = parser.parse_args()

    if args.save_seeds is None:
        args.save_seeds = PROJECT_ROOT / "results" / f"mainnet_seeds_{int(time.time())}.json"

    global _NO_MIRROR
    _NO_MIRROR = args.no_mirror

    if args.dry_run:
        return _dry_run(args.n)

    import os

    if os.environ.get("RUN_LIVE") not in ("1", "true", "yes"):
        raise RuntimeError(
            "Mainnet mode requires RUN_LIVE=1 environment variable"
        )

    # ---- treasury ----
    seed = treasury_seed()
    addr = treasury_address(seed)
    print(f"Treasury: {addr}")
    from xrpl.wallet import Wallet

    treasury_wallet = Wallet.from_seed(seed)
    balance = wait_for_balance(addr, 1)
    print(f"  balance: {balance / 1e6} XRP")

    # ---- budget gate ----
    # The budget is DERIVED, not estimated by hand. Hand-estimating it is
    # what caused two failures on 2026-10-28: first a dry run that spent
    # 4 XRP because the mock missed a path, then a tecINSUFFICIENT_RESERVE
    # because the issuer was funded for zero credential reserves when it
    # creates one per agent. Derive it from the ledger's own reserve rules
    # and REFUSE TO START if the treasury cannot cover it.
    need = compute_budget(args.n)
    print(f"  computed budget: {need['total'] / 1e6:.3f} XRP")
    for line in need["lines"]:
        print(f"    {line}")
    if balance < need["total"]:
        shortfall = need["total"] - balance
        print(
            f"\n  ABORT: treasury holds {balance / 1e6:.6f} XRP but the run "
            f"needs {need['total'] / 1e6:.3f} XRP "
            f"(short by {shortfall / 1e6:.3f} XRP)."
        )
        print("  Nothing was submitted. Fund the treasury and retry.")
        return 2
    print(f"  headroom: {(balance - need['total']) / 1e6:.3f} XRP")

    seeds: dict[str, str] = {}

    # ---- mirror wallet + sink ----
    mirror_addr = ""
    sink_addr = ""
    if not args.no_mirror:
        mirror_seed, mirror_addr = new_wallet_from_treasury(
            treasury_wallet, seeds, "mirror", plain_wallet_funding_drops(), args.save_seeds
        )
        _, sink_addr = new_wallet_from_treasury(
            treasury_wallet, seeds, "sink", plain_wallet_funding_drops(), args.save_seeds
        )
        print(f"  mirror: {mirror_addr} -> sink {sink_addr}")

    # ---- issuer ----
    # Count the credentials the issuer will create BEFORE funding it: each
    # one costs the issuer a 0.2 XRP owner reserve, on top of its base
    # reserve. Under-funding it fails with tecINSUFFICIENT_RESERVE.
    roles_preview = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    roles_used = [roles_preview[i % len(roles_preview)] for i in range(args.n)]
    issuer_credential_count = sum(
        1 for r in roles_used if r in ("valid", "controller_banned", "pending")
    )
    issuer_seed, issuer_addr = new_wallet_from_treasury(
        treasury_wallet,
        seeds,
        "good_issuer",
        issuer_funding_drops(issuer_credential_count),
        args.save_seeds,
    )
    print(
        f"  good issuer: {issuer_addr} "
        f"(funding {issuer_funding_drops(issuer_credential_count) / 1e6:.4f} XRP "
        f"for {issuer_credential_count} credential(s) it will create)"
    )

    # ---- agents, one per role ----
    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    specs = []
    for i in range(args.n):
        role = roles[i % len(roles)]
        # An agent that publishes a SignerList owns a third object, so it
        # needs a third owner reserve. The SignerListSet is submitted on
        # the agent's own account, which is what makes the agent -- not
        # the co-signer -- the account charged for it.
        owned_objects = 3 if role == "controller_banned" else AGENT_OWNED_OBJECTS
        agent_seed, agent_addr = new_wallet_from_treasury(
            treasury_wallet,
            seeds,
            f"agent{i}_{role}",
            agent_funding_drops(owned_objects),
            args.save_seeds,
        )
        spec = {
            "index": i,
            "role": role,
            "seed": agent_seed,
            "address": agent_addr,
            "did": f"did:xrpl:1:{agent_addr}",
            "controller_banned_address": "",
            "controller_banned_signerlist_tx": "",
        }
        print(f"  [{i}] {role:20s} {agent_addr} ... ", end="", flush=True)

        if role == "valid":
            _issue_and_accept_credential(issuer_seed, agent_seed)
        elif role == "pending":
            issuer = Authority.from_seed(issuer_seed, network="mainnet")
            issuer.issue_credential(
                subject=agent_addr, credential_type=b"agent_identity_v1"
            )
        elif role == "controller_banned":
            ctrl_seed, ctrl_addr = new_wallet_from_treasury(
                treasury_wallet,
                seeds,
                f"agent{i}_controller",
                plain_wallet_funding_drops(),
                args.save_seeds,
            )
            spec["controller_banned_address"] = ctrl_addr
            _issue_and_accept_credential(issuer_seed, agent_seed)
            spec["controller_banned_signerlist_tx"] = _setup_controller_banned_onchain(
                agent_seed=agent_seed,
                agent_address=agent_addr,
                banned_address=ctrl_addr,
            )
        elif role == "wrong_issuer":
            # The wrong issuer CREATES a credential, so it holds one owned
            # object -- the owner reserve is charged to the issuer, not
            # the subject. Funding it as a plain wallet (base reserve
            # only) fails with tecINSUFFICIENT_RESERVE, which is what
            # happened on the n=6 run on 2026-10-28.
            wrong_seed, _ = new_wallet_from_treasury(
                treasury_wallet,
                seeds,
                f"agent{i}_wrongissuer",
                issuer_funding_drops(1),
                args.save_seeds,
            )
            _issue_and_accept_credential(wrong_seed, agent_seed)

        print("done")
        specs.append(spec)

    # ---- ban list ----
    bans_path = PROJECT_ROOT / "results" / "stress_bans_mainnet.json"
    bans = BannedAgentRegistry(network="mainnet", path=bans_path)
    bans.load()
    for spec in specs:
        if spec["role"] == "banned":
            bans.add(
                Ban(
                    address=spec["address"],
                    reason="mainnet stress ban",
                    added_by="harness",
                )
            )
        elif spec["role"] == "controller_banned":
            bans.add(
                Ban(
                    address=spec["controller_banned_address"],
                    reason="mainnet stress controller ban",
                    added_by="harness",
                )
            )

    # ---- audit log ----
    xrpl_mirror = None
    if not args.no_mirror:
        xrpl_mirror = XRPLMirror.from_seed(
            seeds["mirror"], network="mainnet", destination=sink_addr
        )
        mirror_addr = xrpl_mirror.provider.classic_address

    audit_path = PROJECT_ROOT / "results" / f"audit_mainnet_{int(time.time())}.db"
    audit = AuditLog(db_path=audit_path, xrpl_mirror=xrpl_mirror, mirror_on_deny_only=False)

    policy = AuthorizationPolicy(network="mainnet", bans=bans)
    policy.require_credential(issuer=issuer_addr, credential_type=b"agent_identity_v1")

    print(f"\n--- Evaluating {len(specs)} agents against live mainnet ---")
    decisions_by_role: dict[str, list] = {r: [] for r in roles}
    mirror_txs: list[dict] = []
    for spec in specs:
        ctx = RequestContext(resource="/api/protected-mainnet")
        decision = policy.evaluate(spec["address"], context=ctx)
        recorded_tx: str | None = None
        if xrpl_mirror is not None:
            try:
                recorded_tx = _submit_with_retry(
                    xrpl_mirror.provider,
                    _build_mirror_tx(
                        xrpl_mirror.provider.classic_address, sink_addr, decision
                    ),
                )
            except Exception as e:
                print(f"      mirror submit failed: {type(e).__name__}: {e}")

        row_id = audit.record(
            decision,
            extra={"role": spec["role"], "mode": "mainnet"},
            attempt_mirror=False,
        )
        if recorded_tx:
            audit._conn.execute(  # noqa: SLF001 - intentional direct write
                "UPDATE auth_decisions SET mirrored_tx = ? WHERE id = ?",
                (recorded_tx, row_id),
            )
            mirror_txs.append(
                {
                    "row_id": row_id,
                    "role": spec["role"],
                    "allow": decision.allow,
                    "tx_hash": recorded_tx,
                    "agent_did": decision.agent_did,
                }
            )
        decisions_by_role[spec["role"]].append(decision)
        print(
            f"  [{spec['index']}] {spec['role']:20s} -> {decision.summary()}"
            f"{'  mirror=' + recorded_tx if recorded_tx else ''}"
        )

    audit.close()

    summary = {
        "mode": "mainnet",
        "n_agents": args.n,
        "treasury": addr,
        "good_issuer": issuer_addr,
        "mirror": {
            "enabled": xrpl_mirror is not None,
            "wallet": mirror_addr,
            "destination": sink_addr,
            "network": "mainnet",
            "txs": mirror_txs,
        },
        "agents": [
            {k: v for k, v in s.items() if k != "seed"} for s in specs
        ],
        "audit_db": str(audit_path),
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }
    for role, decisions in decisions_by_role.items():
        reason_counts: dict[str, int] = {}
        for d in decisions:
            for r in d.reasons:
                reason_counts[r.code.value] = reason_counts.get(r.code.value, 0) + 1
        summary.setdefault("by_role", {})[role] = {
            "n": len(decisions),
            "allowed": sum(1 for d in decisions if d.allow),
            "denied": sum(1 for d in decisions if not d.allow),
            "reason_counts": reason_counts,
        }

    out = args.out or (
        PROJECT_ROOT / "results" / f"stress_summary_mainnet_{int(time.time())}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nWrote summary to {out}")

    if args.save_seeds:
        args.save_seeds.parent.mkdir(parents=True, exist_ok=True)
        args.save_seeds.write_text(json.dumps(seeds, indent=2))
        try:
            args.save_seeds.chmod(0o600)
        except Exception:
            pass
        print(f"  saved seeds to {args.save_seeds} (chmod 600)")

    print()
    print(f"Mode: mainnet, N={summary['n_agents']}")
    print(f"{'role':<22} {'n':>4} {'allow':>6} {'deny':>6} reasons")
    print("-" * 70)
    for role, stats in summary.get("by_role", {}).items():
        rc = ", ".join(f"{k}={v}" for k, v in stats.get("reason_counts", {}).items())
        print(
            f"{role:<22} {stats['n']:>4} {stats['allowed']:>6} "
            f"{stats['denied']:>6} {rc}"
        )
    return 0


# ----------------------------------------------------------------------------
# Dry run
# ----------------------------------------------------------------------------

def compute_budget(n: int) -> dict:
    """Derive the XRP the run needs, from the ledger's own reserve rules.

    Returns a dict with "total" (drops) and a human-readable breakdown.
    The point is that this is COMPUTED, not estimated -- every account's
    base reserve and every owned object's owner reserve is counted at the
    account that actually holds it.
    """
    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    roles_used = [roles[i % len(roles)] for i in range(n)]
    need = set(roles_used)

    mirror = not args_no_mirror()
    # Accounts funded from the treasury (each needs its own base reserve).
    n_accounts = n                                  # agents
    n_accounts += 1                                # issuer
    if mirror:
        n_accounts += 2                            # mirror + sink
    if "controller_banned" in need:
        n_accounts += 1                            # banned co-signer
    if "wrong_issuer" in need:
        n_accounts += 1                            # the wrong issuer

    # Owned objects, charged to the account that HOLDS them.
    agent_objs = 2 * n                             # 1 DID + 1 credential each
    # A controller_banned agent ALSO owns a SignerList -- the SignerListSet
    # is submitted on the agent's own account, so the agent is charged the
    # owner reserve for it, not the co-signer.
    signerlist_objs = sum(1 for r in roles_used if r == "controller_banned")
    issuer_objs = sum(
        1 for r in roles_used if r in ("valid", "controller_banned", "pending")
    )
    wrong_issuer_objs = 1 if "wrong_issuer" in need else 0
    total_objs = (
        agent_objs + signerlist_objs + issuer_objs + wrong_issuer_objs
    )

    # The TREASURY is itself an account and carries its own 1 XRP base
    # reserve, which is locked while it exists. The budget must include
    # it, or the treasury runs out of transferable balance part-way
    # through the funding sequence -- which is what happened on the
    # second real attempt (tecUNFUNDED_PAYMENT on the last Payment).
    reserve = (n_accounts + 1) * RESERVE_BASE_DROPS + total_objs * RESERVE_INC_DROPS

    # Transactions: funding + the agent's own + the issuer's + mirror memos.
    n_txs = n_accounts + n * 2                     # funding + accept/DID
    n_txs += issuer_objs                           # CredentialCreate
    if "controller_banned" in need:
        n_txs += 1                                 # SignerListSet
    if mirror:
        n_txs += n                                 # one memo per decision
    fees = n_txs * FEE_MEDIAN_DROPS

    # Headroom. The budget was validated against the real n=4 attempt on
    # 2026-10-28: computed 9.085 XRP against 9.104 XRP actually required --
    # short by 0.2%, because one transaction paid 13,155 drops where the
    # budget assumed 5,000. A budget that lands within a fraction of a
    # percent of the truth is a budget that will trip on fee variance, so
    # carry a margin.
    #
    # This is a HEADROOM multiplier on the fee line only -- the reserve
    # figures are exact protocol constants and must not be inflated.
    fees = int(fees * FEE_HEADROOM)

    lines = [
        f"{n_accounts + 1} accounts x {RESERVE_BASE_DROPS / 1e6} base reserve "
        f"(includes the treasury's own)",
        f"{total_objs} owned objects x {RESERVE_INC_DROPS / 1e6} owner reserve",
        f"~{n_txs} transactions x {FEE_MEDIAN_DROPS} fee",
    ]
    return {"total": reserve + fees, "lines": lines, "accounts": n_accounts}


def args_no_mirror() -> bool:
    """Whether the mirror is disabled. Set by main() before budgeting."""
    return globals().get("_NO_MIRROR", False)


def _treasury_balance() -> int:
    """The treasury's live balance in drops. 0 if unreadable."""
    from xrpl.models.requests import AccountInfo

    try:
        client = get_client("mainnet")
        resp = client.request(AccountInfo(account=treasury_address(treasury_seed())))
        return int(resp.result["account_data"]["Balance"])
    except Exception:
        return 0


def _dry_run(n: int) -> int:
    """Exercise every code path against a mocked ledger.

    Nothing is submitted and no XRP moves. The point is to catch a coding
    error in the harness BEFORE it spends real mainnet XRP: wrong
    attribute names, wrong argument shapes, wrong network strings.
    """
    import unittest.mock as mock

    print("DRY RUN — no transactions will be submitted, no XRP will move.\n")

    # A dry run that spends real money is worse than no dry run: it looks
    # like a safety check, so it suppresses the scrutiny one would earn.
    # The ONLY thing that makes a dry run trustworthy is this assertion --
    # read the balance before and after and require it unchanged.
    #
    # This harness previously leaked 4 XRP on 2026-10-28 because the mock
    # missed an import path. The patches below enumerate every path the
    # submit function can be reached through, and the balance assertion
    # at the end proves it.

    # The treasury is the one thing we must not mock away: it proves the
    # keychain entry is readable and derives the expected address.
    seed = treasury_seed()
    addr = treasury_address(seed)
    print("  treasury seed: readable from keychain")
    print(f"  treasury address: {addr}")

    before = _treasury_balance()
    print(f"  treasury balance before: {before} drops")

    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    print(f"\n  roles for n={n}: {[roles[i % len(roles)] for i in range(n)]}")
    covered = {roles[i % len(roles)] for i in range(n)}
    missing = set(roles) - covered
    if missing:
        print(f"  WARNING: n={n} does not cover roles: {sorted(missing)}")
    else:
        print("  all six roles covered")

    print("\n  exercising the transaction-building and signing paths:")
    from xrpl.wallet import Wallet

    def fake_submit(tx, client, wallet=None, **kwargs):
        """Mimic a successful submit.

        Must satisfy BOTH callers: the library's ``submit_with_retry``
        (which expects a dict) and xrpl-py's ``submit_and_wait`` (which
        expects a Response object with a ``.result`` attribute).
        """
        payload = {
            "hash": "F" * 64,
            "meta": {"TransactionResult": "tesSUCCESS"},
        }

        class _Resp:
            def __init__(self, p):
                self.result = p

        # xrpl-py's submit_and_wait path returns a Response; the library's
        # path returns the dict. Return whichever the caller will accept by
        # making the dict ALSO carry a .result attribute.
        class _Dict(dict):
            def __init__(self, p):
                super().__init__(p)
                self.result = p

        return _Dict(payload)

    # Patch BOTH the harness's import and the library's internal one --
    # xrpl_agent_id.identity calls xrpl_agent_id.tx_submit.submit_with_retry
    # directly, so patching only the harness would let a real autofill
    # (and therefore a real account_info call) happen.
    # Patch EVERY path to the submit function.
    #
    # A mock patches one NAME at a time. The submit function is reachable
    # through several bindings, and patching the wrong ones lets real
    # Payments out -- which is exactly what happened on 2026-10-28 when
    # this dry run leaked 4 XRP. The funding path escaped both the
    # harness binding and the library binding.
    #
    # Belt: patch the module attribute at the SOURCE (tx_submit).
    # Braces: patch every module that did `from ... import submit_with_retry`.
    # And the one that actually bites: patch the wallet-submission helper
    # too, so nothing can reach the network even via a new binding.
    import xrpl_agent_id.identity as _identity_mod
    import xrpl_agent_id.tx_submit as _tx_mod

    def _no_network(*args, **kwargs):
        raise RuntimeError(
            "dry run attempted a network submission -- this is a bug in the "
            "mock coverage, not a ledger error"
        )

    # NOTE on patch targets: this script runs as __main__, so a string
    # target like "scripts.stress_harness_mainnet.X" would patch a SECOND,
    # separate module object and have no effect on the running code. Every
    # patch below therefore targets THIS module's globals directly.
    _this = sys.modules[__name__]

    with mock.patch.object(
        _tx_mod, "submit_with_retry", side_effect=fake_submit
    ), mock.patch.object(
        _identity_mod, "submit_with_retry", side_effect=fake_submit
    ), mock.patch.object(
        _this, "submit_with_retry", side_effect=fake_submit
    ), mock.patch.object(
        _this, "wait_for_balance", return_value=agent_funding_drops()
    ), mock.patch(
        "xrpl.transaction.submit_and_wait", side_effect=fake_submit
    ), mock.patch(
        "xrpl.transaction.autofill", side_effect=_no_network
    ):

        # 1. funding path
        treasury_wallet = Wallet.from_seed(seed)
        agent = Wallet.create()
        fund_from_treasury(
            treasury_wallet, agent.classic_address, agent_funding_drops()
        )
        print(f"    funding path: OK (Payment built for {agent.classic_address})")

        # 2. issue + accept
        issuer = Wallet.create()
        _issue_and_accept_credential(issuer.seed, agent.seed)
        print("    issue + accept path: OK")

        # 3. SignerListSet path
        ctrl = Wallet.create()
        _setup_controller_banned_onchain(
            agent_seed=agent.seed,
            agent_address=agent.classic_address,
            banned_address=ctrl.classic_address,
        )
        print("    SignerListSet path: OK")

        # 4. mirror memo path
        from xrpl_agent_id.authorization import (
            AuthorizationDecision,
            AuthorizationReason,
            ReasonCode,
        )

        decision = AuthorizationDecision(
            agent_did=f"did:xrpl:1:{agent.classic_address}",
            allow=True,
            reasons=[AuthorizationReason(code=ReasonCode.OK, detail="ok")],
        )
        tx = _build_mirror_tx(agent.classic_address, ctrl.classic_address, decision)
        print(f"    mirror memo path: OK (amount={tx.amount} drops, memos={len(tx.memos)})")

        # 5. AuditLog + policy wiring
        import tempfile
        from pathlib import Path as P

        with tempfile.TemporaryDirectory() as td:
            audit = AuditLog(
                db_path=P(td) / "dry.db",
                xrpl_mirror=None,
                mirror_on_deny_only=False,
            )
            row = audit.record(decision, extra={"role": "dry"}, attempt_mirror=False)
            audit.close()
            print(f"    AuditLog path: OK (row_id={row})")

    print("\n  All code paths exercised.")

    # --- THE ASSERTION THAT MAKES THIS TRUSTWORTHY ---
    after = _treasury_balance()
    if after != before:
        print(
            f"\n  *** DRY RUN SPENT MONEY: {before} -> {after} "
            f"(delta {after - before} drops) ***"
        )
        return 1
    print(f"  Treasury balance unchanged: {after} drops. No XRP spent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
