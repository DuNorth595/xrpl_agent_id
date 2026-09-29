# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Live smoke test for the controller_banned SignerListSet path.

Funds three testnet wallets (agent + banned co-signer + sentinel),
publishes a SignerListSet for each of the two shapes, then verifies
via account_objects that the SignerList landed on the agent account
with the expected entries and quorum.

Run:
    RUN_LIVE=1 /usr/bin/python3 scripts/live_signerlist_smoke.py

Output:
    Prints the funded addresses, the two SignerListSet tx hashes, and
    the resolved SignerList account_objects for both shapes. Exits
    non-zero if either SignerListSet does not produce tesSUCCESS or
    the account_objects query does not return the expected entries.

Costs ~6 drops (3 SignerListSet txs at 1 drop base + reserve).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from xrpl.wallet import Wallet
from xrpl.transaction import submit_and_wait
from xrpl.models.transactions import SignerListSet, SignerEntry
from xrpl.models.requests import AccountObjects

from xrpl_agent_id.network import get_client


def _fund() -> tuple[str, str]:
    """Hit the testnet faucet for a fresh wallet. Returns (seed, address)."""
    import urllib.request
    req = urllib.request.Request(
        "https://faucet.altnet.rippletest.net/accounts",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    seed = data["seed"]
    address = data["account"]["address"]
    # Wait for the faucet to settle (funding usually takes ~3s).
    deadline = time.time() + 30
    client = get_client("testnet")
    while time.time() < deadline:
        from xrpl.models.requests import AccountInfo
        try:
            resp = client.request(AccountInfo(account=address))
            bal = int(resp.result["account_data"]["Balance"])
            if bal >= 10_000_000:
                return seed, address
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"faucet never funded {address}")


def _publish(agent_seed: str, entries: list[SignerEntry], quorum: int) -> str:
    """Submit a SignerListSet and return the tx hash. Asserts tesSUCCESS."""
    master = Wallet.from_seed(agent_seed)
    tx = SignerListSet(
        account=master.address,
        signer_quorum=quorum,
        signer_entries=entries,
    )
    client = get_client("testnet")
    resp = submit_and_wait(tx, client, master)
    result = resp.result or {}
    engine = (result.get("meta") or {}).get("TransactionResult")
    tx_hash = result.get("hash", "")
    if engine != "tesSUCCESS":
        raise RuntimeError(
            f"SignerListSet failed: result={engine}, hash={tx_hash}"
        )
    return tx_hash


def _fetch_signer_list(address: str) -> dict | None:
    """Return the first SignerList account_object for the address, or None."""
    client = get_client("testnet")
    marker = None
    for _ in range(5):
        kwargs = {"account": address, "type": "signer_list", "limit": 10}
        if marker:
            kwargs["marker"] = marker
        resp = client.request(AccountObjects(**kwargs))
        data = resp.result or {}
        objs = data.get("account_objects", [])
        if objs:
            return objs[0]
        marker = data.get("marker")
        if marker is None:
            break
    return None


def main() -> int:
    if os.environ.get("RUN_LIVE") not in ("1", "true", "yes"):
        print("RUN_LIVE=1 required for live mode", file=sys.stderr)
        return 2

    print("=== Live SignerListSet smoke test ===\n")

    # --- Setup: 3 fresh wallets ---
    print("Funding 3 wallets via testnet faucet...")
    agent1_seed, agent1_addr = _fund()
    agent2_seed, agent2_addr = _fund()
    sentinel_seed, sentinel_addr = _fund()
    print(f"  agent1 (compromised shape) : {agent1_addr}")
    print(f"  agent2 (quarantined shape) : {agent2_addr}")
    print(f"  sentinel                    : {sentinel_addr}")

    # --- Shape 1: compromised (agent1) ---
    # Master removed; banned = agent1's master itself? No — banned must be
    # a DIFFERENT address. We use agent2 as the banned co-signer.
    print("\n--- Shape 1: compromised ---")
    compromised_tx = _publish(
        agent1_seed,
        [SignerEntry(account=agent2_addr, signer_weight=1)],
        quorum=1,
    )
    print(f"  SignerListSet tx hash: {compromised_tx}")
    sl1 = _fetch_signer_list(agent1_addr)
    assert sl1 is not None, f"no signer_list on {agent1_addr}"
    assert int(sl1["SignerQuorum"]) == 1, f"quorum expected 1, got {sl1['SignerQuorum']}"
    addrs1 = {e["SignerEntry"]["Account"] for e in sl1["SignerEntries"]}
    assert addrs1 == {agent2_addr}, f"expected only {agent2_addr}, got {addrs1}"
    print(f"  on-chain signer list verified: quorum=1, entries={addrs1}")

    # --- Shape 2: quarantined (agent2) ---
    # Master removed; agent1 + sentinel at 1:1, quorum 2 (frozen).
    print("\n--- Shape 2: quarantined ---")
    quarantined_tx = _publish(
        agent2_seed,
        [
            SignerEntry(account=agent1_addr, signer_weight=1),
            SignerEntry(account=sentinel_addr, signer_weight=1),
        ],
        quorum=2,
    )
    print(f"  SignerListSet tx hash: {quarantined_tx}")
    sl2 = _fetch_signer_list(agent2_addr)
    assert sl2 is not None, f"no signer_list on {agent2_addr}"
    assert int(sl2["SignerQuorum"]) == 2, f"quorum expected 2, got {sl2['SignerQuorum']}"
    addrs2 = {e["SignerEntry"]["Account"] for e in sl2["SignerEntries"]}
    assert addrs2 == {agent1_addr, sentinel_addr}, \
        f"expected {{agent1, sentinel}}, got {addrs2}"
    print(f"  on-chain signer list verified: quorum=2, entries={addrs2}")

    # --- Done ---
    print("\n=== Smoke test PASSED ===")
    print(f"  compromised  : {agent1_addr}  -> tx {compromised_tx[:16]}...")
    print(f"  quarantined  : {agent2_addr}  -> tx {quarantined_tx[:16]}...")
    print(f"  sentinel     : {sentinel_addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
