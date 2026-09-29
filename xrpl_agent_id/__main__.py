# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.__main__ — entry point for ``python -m xrpl_agent_id``.

Three modes:

* ``python -m xrpl_agent_id --version``
    Print just the package version and exit. Useful for scripts and CI.
* ``python -m xrpl_agent_id --api``
    Print the package version, the public API surface, and a DID roundtrip
    example. This is the original smoke test, kept as the default-with-flag.
* ``python -m xrpl_agent_id`` (no args)
    Same as ``--api`` — the original behaviour, kept for backward compatibility.
"""

from __future__ import annotations

import argparse
import sys

from xrpl_agent_id import (
    AgentIdentity,
    Authority,
    Credential,
    CredentialType,
    DIDDocument,
    NetworkEndpoint,
    TrustCheckResult,
    TrustPolicy,
    TrustRegistry,
    __version__,
    did_from_account,
    get_client,
    get_network,
    parse_did,
)


def _print_api() -> None:
    print(f"xrpl_agent_id v{__version__}")
    print()
    print("Public API:")
    print("  - AgentIdentity            (XRPL identity wrapper)")
    print("  - Authority               (XRPL credential issuer)")
    print("  - Credential              (XLS-70 attestation)")
    print("  - CredentialType          (closed enum: AGENT_ID_V1, VERIFIED_AGENT_OPERATOR, EVAL_PASSED)")
    print("  - DIDDocument             (W3C DID Document)")
    print("  - NetworkEndpoint         (named XRPL network: mainnet / testnet / devnet)")
    print("  - TrustRegistry / TrustPolicy / TrustCheckResult")
    print("  - did_from_account(address, network)")
    print("  - parse_did(did) -> (network_id, idstring)")
    print("  - get_client(network)     (async JSON-RPC client)")
    print("  - get_network(name)       (returns NetworkEndpoint)")
    print()
    print("Example roundtrip:")
    addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
    did = did_from_account(addr, "mainnet")
    net, parsed_addr = parse_did(did)
    print(f"  address: {addr}")
    print(f"  did:     {did}")
    print(f"  parse:   network={net}, address={parsed_addr}")
    print(f"  match:   {parsed_addr == addr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m xrpl_agent_id",
        description="xrpl_agent_id — identity for AI agents on the XRP Ledger.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version and exit.",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Print the public API surface and a DID roundtrip example.",
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"xrpl_agent_id {__version__}")
        return 0

    # Default behaviour: --api (preserves backward compatibility).
    _print_api()
    return 0


if __name__ == "__main__":
    sys.exit(main())
