# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Network constants and JSON-RPC client factory.

Single source of truth for XRPL endpoints across the library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xrpl.clients import JsonRpcClient


@dataclass(frozen=True)
class NetworkEndpoint:
    """An XRPL network's connection endpoints."""

    name: str
    network_id: int
    json_rpc_url: str
    websocket_url: str
    faucet_url: str | None = None


# Network ID map. Mainnet is `1` (per XLS-40d README example
# `did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh`). The legacy value 0 means
# "any network" and is reserved — NOT a chain ID.
NETWORKS: dict[str, NetworkEndpoint] = {
    "mainnet": NetworkEndpoint(
        name="mainnet",
        network_id=1,
        json_rpc_url="https://xrplcluster.com",
        websocket_url="wss://xrplcluster.com",
        faucet_url=None,
    ),
    "testnet": NetworkEndpoint(
        name="testnet",
        network_id=2,
        json_rpc_url="https://s.altnet.rippletest.net:51234",
        websocket_url="wss://s.altnet.rippletest.net:51233",
        faucet_url="https://faucet.altnet.rippletest.net/accounts",
    ),
    "devnet": NetworkEndpoint(
        name="devnet",
        network_id=3,
        json_rpc_url="https://s.devnet.rippletest.net:51234",
        websocket_url="wss://s.devnet.rippletest.net:51233",
        faucet_url="https://faucet.devnet.rippletest.net/accounts",
    ),
}


def get_network(name: str) -> NetworkEndpoint:
    """Return the NetworkEndpoint for a named XRPL network."""
    net = NETWORKS.get(name)
    if net is None:
        raise ValueError(
            f"Unknown network: {name!r}. Known: {sorted(NETWORKS)}"
        )
    return net


def get_client(network: str = "testnet") -> "JsonRpcClient":
    """Build a JsonRpcClient for the named network.

    Imported lazily so the rest of the library has no xrpl import cost.
    """
    from xrpl.clients import JsonRpcClient

    return JsonRpcClient(get_network(network).json_rpc_url)
