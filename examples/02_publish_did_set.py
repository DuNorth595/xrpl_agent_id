#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""02_publish_did_set.py -- Publish an agent's DID Document on the ledger.

Runs against XRPL **testnet**. This example submits a real transaction,
so it needs a funded wallet -- see "Needs a wallet" below.

    pip install xrpl-agent-id
    python examples/02_publish_did_set.py

What it does:
  1. Loads (or generates) a testnet wallet.
  2. Builds a W3C DID Document for the agent's DID.
  3. Publishes it with a DIDSet transaction.
  4. Resolves the DID back from the ledger and prints the document.

After this, anyone can resolve the DID. There is no server of ours in
the path -- resolution is a ledger lookup.

Note on the 256-byte cap
------------------------
XLS-40d caps DIDSet.DIDDocument and DIDSet.URI at 256 bytes each. A
canonical W3C DID Document with a Multikey verification method is
around 495 bytes, so it does not fit. The XLS-40d pattern is to put a
small document (identifiers only) on the ledger and the full document
behind a URI. This example follows that pattern and shows the byte
count of both forms.
"""

from __future__ import annotations

import json
import os
import time

from xrpl.core.keypairs import derive_classic_address
from xrpl.models.transactions import DIDSet
from xrpl.wallet import Wallet

from xrpl_agent_id import did_from_account
from xrpl_agent_id import get_client
from xrpl_agent_id import resolve_did
from xrpl_agent_id.did import DIDDocument
from xrpl_agent_id.tx_submit import submit_with_retry

NETWORK = "testnet"
DOC_CAP_CHARS = 256  # hex characters, per the XLS-40d field limit


def _wallet() -> Wallet:
    """Return a funded testnet wallet.

    Uses XRPL_EXAMPLE_SEED if set. Otherwise generates one and asks you
    to fund the printed address.
    """
    seed = os.environ.get("XRPL_EXAMPLE_SEED")
    if seed:
        return Wallet.from_seed(seed)

    wallet = Wallet.create()
    address = derive_classic_address(wallet.public_key)
    client = get_client(NETWORK)
    try:
        balance = client.request_balance(address)
    except Exception:  # noqa: BLE001
        balance = 0
    if balance <= 0:
        raise SystemExit(
            f"\n  Wallet {address} has no testnet balance.\n\n"
            "  Fund it here, then re-run:\n"
            "    https://testnet.xrpl.org/fund_genesis_account/\n\n"
            "  Or export a funded seed and re-run:\n"
            "    export XRPL_EXAMPLE_SEED=<seed>\n"
        )
    return wallet


def main() -> None:
    print("xrpl_agent_id -- example 2: publish a DIDSet")
    print("=" * 62)

    wallet = _wallet()
    address = derive_classic_address(wallet.public_key)
    did = did_from_account(address, network=NETWORK)
    print(f"  address : {address}")
    print(f"  DID     : {did}")

    # The full W3C document, for reference.
    full_doc = DIDDocument(
        id=did,
        verification_method=[
            {
                "id": f"{did}#keys-1",
                "type": "Multikey",
                "controller": did,
                "publicKeyHex": wallet.public_key,
            }
        ],
        authentication=[f"{did}#keys-1"],
    )
    full_json = json.dumps(full_doc.to_json(), separators=(",", ":"))
    full_hex = full_json.encode("utf-8").hex().upper()
    print(f"  full DID Document : {len(full_json)} bytes JSON = {len(full_hex)} hex chars")
    print(f"                      (cap is {DOC_CAP_CHARS} hex chars -- does not fit)")

    # The XLS-40d pattern: put a URI on the ledger and the full document
    # behind it. The URI field has the same 256-hex-character cap.
    did_uri = f"https://sdevlabs.dev/did/{address}"
    uri_hex = did_uri.encode("utf-8").hex().upper()
    print(f"  URI strategy      : {did_uri}")
    print(f"                      {len(uri_hex)} hex chars (fits the {DOC_CAP_CHARS} cap)")

    # Publish. URI-only is the XLS-40d pattern for cap compliance.
    client = get_client(NETWORK)
    did_set = DIDSet(account=address, uri=did_uri.encode("utf-8").hex().upper())
    print()
    print("  submitting DIDSet ...")

    # submit_with_retry handles the ledger-close race that plain
    # submit_and_wait trips over when the validated ledger advances
    # between building and submitting the transaction.
    result = submit_with_retry(did_set, client, wallet) or {}
    meta = result.get("meta") or {}
    print(f"  tx_hash    : {result.get('hash')}")
    print(f"  tx_result  : {meta.get('TransactionResult')}")
    print(f"  ledger     : {result.get('ledger_index')}")

    if meta.get("TransactionResult") != "tesSUCCESS":
        print()
        print("  DIDSet did not succeed. If the document is still over the cap,")
        print("  publish the URI-only form (already attempted above).")
        return

    print()
    print("  waiting for the ledger to close ...")
    time.sleep(5)

    print("  resolving the DID from the ledger ...")
    try:
        resolved = resolve_did(did, network=NETWORK)
        print(json.dumps(resolved.to_json(), indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"  resolve deferred: {type(exc).__name__}: {exc}")
        print("  (the ledger may need another close cycle -- try again shortly)")

    print()
    print("  Anyone can now resolve this DID. No server of ours is involved.")


if __name__ == "__main__":
    main()
