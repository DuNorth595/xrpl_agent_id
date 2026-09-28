# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""AgentIdentity — the central object.

A single AgentIdentity wraps an xrpl-py Wallet plus:
    - A DID (did:xrpl:<network>:<account>)
    - A set of credentials (issued to this agent, or issued BY this agent)
    - Methods to issue, accept, verify, revoke credentials

This file is the surface only. Submodules hold the actual logic so this file
stays readable as the API contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xrpl.wallet import Wallet

    from xrpl_agent_id.credential import Credential
    from xrpl_agent_id.did import DIDDocument


@dataclass
class AgentIdentity:
    """An XRPL identity for an AI agent.

    Wraps an xrpl-py Wallet and exposes a high-level identity API.
    The wallet holds the keys. This class holds the identity layer on top.
    """

    wallet: "Wallet"
    network: str = "testnet"  # "testnet" or "mainnet"
    _credentials: list["Credential"] = field(default_factory=list)

    @classmethod
    def from_seed(cls, seed: str, network: str = "testnet") -> "AgentIdentity":
        """Create an AgentIdentity from a master seed (family seed, 's...')."""
        raise NotImplementedError

    @property
    def did(self) -> str:
        """did:xrpl:<network-id>:<account> — see XLS-40d."""
        raise NotImplementedError

    @property
    def address(self) -> str:
        """The raw XRPL classic address (rXXX...)."""
        return self.wallet.classic_address

    def issue_credential(
        self,
        subject: str,
        credential_type: bytes,
        uri: str | None = None,
        expiration: int | None = None,
    ) -> "Credential":
        """Issue a credential to another agent's DID."""
        raise NotImplementedError

    def accept_credential(self, credential: "Credential") -> None:
        """Accept a credential that was issued to this agent."""
        raise NotImplementedError

    def has_credential(self, issuer_did: str, credential_type: bytes) -> bool:
        """Check whether this agent holds an accepted credential of the given type from the issuer."""
        raise NotImplementedError

    def resolve_did_document(self) -> "DIDDocument":
        """Fetch this agent's DID Document from the ledger."""
        raise NotImplementedError
