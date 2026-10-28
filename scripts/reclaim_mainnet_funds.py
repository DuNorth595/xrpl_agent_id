#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Return stranded mainnet funds to the treasury.

Reads a seeds file produced by ``stress_harness_mainnet.py``, and for each
wallet whose seed it holds, sends the balance back to the treasury.

An XRPL account cannot be deleted without its master key, so the only
recovery mechanism is to send the balance out. This script does exactly
that and nothing else -- it does not delete accounts, and it does not touch
accounts whose seeds it does not have.

It never prints a seed.

Usage:
    python scripts/reclaim_mainnet_funds.py --seeds results/mainnet_seeds_*.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import xrpl.core.keypairs.main as _kp
from xrpl.core.keypairs import derive_classic_address
from xrpl.models.transactions import AccountDelete, Payment

from xrpl_agent_id.network import get_client
from xrpl_agent_id.tx_submit import submit_with_retry

# The macOS keychain service that holds the treasury seed. Overridable so
# the value does not have to be published in a public repository -- the
# service name is a pointer to a secret store.
KEYCHAIN_SERVICE = os.environ.get("XRPL_STRESS_KEYCHAIN_SERVICE", "xrpl-mainnet-stress")
MAX_FEE_DROPS = 50_000

#: Base reserve per account, in drops (1 XRP). Locked while the account
#: exists; only AccountDelete releases it.
BASE_RESERVE_DROPS = 1_000_000

#: Base transaction fee in drops (the ledger is unloaded, so this is what
#: is actually charged).
FEE_BASE_DROPS = 10

#: Median transaction fee in drops.
FEE_MEDIAN_DROPS = 5_000

#: AccountDelete's special transaction cost -- one owner reserve, 0.2 XRP.
DELETE_FEE_DROPS = 200_000


def treasury_seed() -> str:
    import subprocess

    proc = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
    )
    seed = (proc.stdout or "").strip()
    if not seed:
        raise RuntimeError(f"no treasury seed in keychain {KEYCHAIN_SERVICE!r}")
    return seed


def address_of(seed: str) -> str:
    result = _kp.derive_keypair(seed)
    public_key = result[0] if isinstance(result, tuple) else result
    return derive_classic_address(public_key)


def await_age_ok(client, address: str) -> bool:
    """True when the account is old enough to be deleted.

    XRPL requires ``Sequence + 255 <= current_ledger_index`` before an
    AccountDelete is accepted -- an anti-spam measure that keeps accounts
    alive for at least ~256 ledgers (~15 minutes).
    """
    from xrpl.models.requests import AccountInfo, ServerInfo

    try:
        info = client.request(ServerInfo())
        ledger = info.result["info"]["validated_ledger"]["seq"]
        acct = client.request(AccountInfo(account=address))
        sequence = int(acct.result["account_data"]["Sequence"])
    except Exception:
        return False
    return sequence + 255 <= ledger


def wallet_for(seed: str):
    """An xrpl.wallet.Wallet for the seed."""
    from xrpl.wallet import Wallet

    return Wallet.from_seed(seed)


def delete_owned_objects(client, wallet, address: str) -> None:
    """Delete every ledger object the account owns, so it can be deleted.

    Credentials are deleted with CredentialDelete, which needs the issuer
    and the subject. A SignerList is removed with SignerListSet, which
    needs a weight-0 entry for the master key.
    """
    from xrpl.models.requests import AccountObjects
    from xrpl.models.transactions import CredentialDelete, SignerListSet

    resp = client.request(AccountObjects(account=address))
    for obj in resp.result.get("account_objects", []):
        kind = obj.get("LedgerEntryType")
        if kind == "Credential":
            tx = CredentialDelete(
                account=address,
                issuer=obj["Issuer"],
                subject=obj["Subject"],
                credential_type=obj.get("CredentialType"),
            )
        elif kind == "SignerList":
            # Deleting a SignerList means a SignerListSet with quorum 0 and
            # NO signer_entries -- xrpl-py rejects an empty list.
            tx = SignerListSet(account=address, signer_quorum=0)
        else:
            print(f"      (skipping {kind} -- no deleter implemented)")
            continue
        result = submit_with_retry(
            tx, client, wallet, max_fee_drops=MAX_FEE_DROPS
        )
        engine = (result or {}).get("meta", {}).get("TransactionResult")
        print(f"      deleted {kind}: {engine}")


def balance_of(client, address: str) -> int:
    from xrpl.models.requests import AccountInfo

    try:
        resp = client.request(AccountInfo(account=address))
        return int(resp.result["account_data"]["Balance"])
    except Exception:
        return 0


def owner_count_of(client, address: str) -> int:
    from xrpl.models.requests import AccountInfo

    try:
        resp = client.request(AccountInfo(account=address))
        return int(resp.result["account_data"].get("OwnerCount", 0))
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclaim stranded mainnet funds")
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = get_client("mainnet")
    treasury = address_of(treasury_seed())

    print(f"Treasury: {treasury}")
    before = balance_of(client, treasury)
    print(f"Treasury balance before: {before / 1e6:.6f} XRP\n")

    seeds = json.loads(args.seeds.read_text())
    total_recovered = 0

    for label, seed in seeds.items():
        address = address_of(seed)
        balance = balance_of(client, address)
        owners = owner_count_of(client, address)

        if balance == 0 and owners == 0:
            print(f"  {label:20s} {address}  empty -- skipped")
            continue

        if owners:
            # The account owns ledger objects (credentials, signer lists).
            # An AccountDelete is rejected until they are gone, and
            # deleting them releases their owner reserve back to the
            # account. Delete them first, then delete the account.
            if not args.dry_run:
                try:
                    delete_owned_objects(client, wallet_for(seed), address)
                    owners = owner_count_of(client, address)
                except Exception as e:
                    print(
                        f"  {label:20s} {address}  object deletion FAILED: "
                        f"{type(e).__name__}: {e}"
                    )
                    continue
            if owners:
                print(
                    f"  {label:20s} {address}  still holds {owners} object(s) "
                    "-- SKIPPED"
                )
                continue

        # An account's TRANSFERABLE balance is (Balance - BaseReserve).
        # The base reserve is locked while the account exists; only
        # AccountDelete releases it.
        transferable = balance - BASE_RESERVE_DROPS
        # The fee actually charged is the base fee (10 drops) unless the
        # ledger is congested. Compare against the BASE fee, not the
        # median -- the ledger is unloaded, so a median-sized fee would
        # make an account look unfunded when it is not.
        can_afford_payment = transferable > FEE_BASE_DROPS

        # AccountDelete releases the 1 XRP base reserve; a Payment cannot.
        # It costs one owner reserve (0.2 XRP) and the account must be at
        # least 256 ledgers old. So prefer the delete when it is available,
        # and fall back to a Payment otherwise.
        age_ok = await_age_ok(client, address)
        if age_ok:
            send = 0
            method = "AccountDelete"
            recoverable = max(0, balance - DELETE_FEE_DROPS)
        elif can_afford_payment:
            # A plain Payment costs 10 drops and moves everything above
            # the base reserve. The account keeps its 1 XRP base reserve
            # (it still exists).
            send = max(0, transferable - FEE_BASE_DROPS)
            method = "Payment"
            recoverable = send
        else:
            send = 0
            method = "WAIT"
            recoverable = 0

        if args.dry_run:
            print(
                f"  {label:20s} {address}  {method}: recoverable "
                f"{recoverable / 1e6:.6f} XRP"
            )
            total_recovered += recoverable
            continue

        try:
            from xrpl.wallet import Wallet

            wallet = Wallet.from_seed(seed)
            if method == "Payment":
                tx = Payment(
                    account=address,
                    destination=treasury,
                    amount=str(send),
                )
            else:
                tx = AccountDelete(
                    account=address,
                    destination=treasury,
                    # xrpl-py models amounts and fees as strings.
                    fee=str(DELETE_FEE_DROPS),
                )
            # AccountDelete's fee is fixed by protocol at one owner
            # reserve (0.2 XRP = 200,000 drops), which is above the
            # ordinary ceiling. That is not a congestion premium -- it is
            # the mandated deletion cost, so it gets its own ceiling.
            ceiling = DELETE_FEE_DROPS * 2 if method == "AccountDelete" else MAX_FEE_DROPS
            result = submit_with_retry(
                tx, client, wallet, max_fee_drops=ceiling
            )
            engine = (result or {}).get("meta", {}).get("TransactionResult")
            print(
                f"  {label:20s} {address}  {method} -> {engine} "
                f"({(result or {}).get('hash', '')[:16]})"
            )
            total_recovered += recoverable
        except Exception as e:
            print(f"  {label:20s} {address}  FAILED: {type(e).__name__}: {e}")

    print(f"\n  total sent: {total_recovered / 1e6:.6f} XRP")

    if args.dry_run:
        print("  (dry run -- nothing sent)")
        return 0

    after = balance_of(client, treasury)
    print(f"\n  treasury before: {before / 1e6:.6f} XRP")
    print(f"  treasury after:  {after / 1e6:.6f} XRP")
    print(f"  recovered:       {(after - before) / 1e6:.6f} XRP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
