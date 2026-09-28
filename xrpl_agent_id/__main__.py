# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.__main__ — `python -m xrpl_agent_id` smoke test.

Run with:
    python -m xrpl_agent_id

Prints the version, the public surface, and a roundtrip example showing
that the package is correctly installed and importable.
"""

from __future__ import annotations

from xrpl_agent_id import (
    AgentIdentity,
    Credential,
    DIDDocument,
    __version__,
    did_from_account,
    parse_did,
)


def main() -> None:
    print(f"xrpl_agent_id v{__version__}")
    print()
    print("Public API:")
    print(f"  - AgentIdentity  (XRPL identity wrapper)")
    print(f"  - Credential     (XLS-70 attestation)")
    print(f"  - DIDDocument    (W3C DID Document)")
    print(f"  - did_from_account(address, network)")
    print(f"  - parse_did(did) -> (network_id, idstring)")
    print()
    print("Example roundtrip:")
    addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
    did = did_from_account(addr, "mainnet")
    net, parsed_addr = parse_did(did)
    print(f"  address: {addr}")
    print(f"  did:     {did}")
    print(f"  parse:   network={net}, address={parsed_addr}")
    print(f"  match:   {parsed_addr == addr}")


if __name__ == "__main__":
    main()
