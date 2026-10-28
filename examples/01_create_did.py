#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""01_create_did.py -- Give an AI agent an identity on the XRP Ledger.

Runs entirely against XRPL **testnet**. No API keys, no database, no
service to start.

    pip install xrpl-agent-id
    python examples/01_create_did.py

What it does:
  1. Generates a testnet wallet (the agent's key).
  2. Derives the agent's DID: xrpl_agent_id.did_from_account()
  3. Resolves that DID back into a DID Document, read from the ledger.
  4. Prints the DID Document as JSON.

The DID is the whole point: it is a W3C-compatible identifier whose
resolution is a ledger lookup, not a database query.
"""

from __future__ import annotations

import json

from xrpl.core.keypairs import derive_classic_address
from xrpl.core.keypairs import derive_keypair
from xrpl.wallet import Wallet

from xrpl_agent_id import did_from_account
from xrpl_agent_id import parse_did
from xrpl_agent_id import resolve_did

NETWORK = "testnet"


def main() -> None:
    print("xrpl_agent_id -- example 1: create a DID")
    print("=" * 60)

    # 1. An agent needs a key. In production this comes from a KMS or
    #    hardware signer; here we generate a throwaway testnet wallet.
    wallet = Wallet.create()
    address = derive_classic_address(wallet.public_key)
    print(f"  address : {address}")

    # 2. The DID is derived from the address -- there is no registry to
    #    register with and no issuance step.
    did = did_from_account(address, network=NETWORK)
    print(f"  DID     : {did}")

    # 3. Parse it back. did:xrpl:<network-id>:<address>
    network_id, parsed_address = parse_did(did)
    print(f"  network : {network_id}")
    print(f"  subject : {parsed_address}")
    assert parsed_address == address, "DID round-trip failed"

    # 4. Resolve it against the ledger. This reads the DIDSet object
    #    published for the account.
    try:
        document = resolve_did(did, network=NETWORK)
    except Exception as exc:  # noqa: BLE001 -- the ledger may not have it
        print(f"  resolve : skipped ({type(exc).__name__}: {exc})")
        print()
        print("  A DID Document appears on the ledger once a DIDSet has been")
        print("  published for the account. See examples/02_publish_did_set.py")
        return

    print("  DID Document:")
    print(json.dumps(document.to_dict(), indent=2))
    print()
    print("  Resolve any did:xrpl with:  resolve_did(did)")


if __name__ == "__main__":
    main()
