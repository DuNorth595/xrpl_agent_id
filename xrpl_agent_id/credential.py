# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Credential — an attestation issued by one XRPL identity to another.

Wraps an XRPL Credential ledger entry. The on-chain fields per XLS-70:
    - Issuer (account)
    - Subject (account)
    - CredentialType (hex of arbitrary bytes, max 64 bytes)
    - Expiration (Ripple epoch seconds, optional)
    - URI (max 256 bytes, optional — points to full claim document off-chain)
    - Flags (lsfAccepted lives here once accepted)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xrpl_agent_id.did import DIDDocument


@dataclass
class Credential:
    """A verifiable credential held by an XRPL agent."""

    issuer: str  # did:xrpl:...
    subject: str  # did:xrpl:...
    credential_type: bytes  # arbitrary, hex-encoded on-chain
    uri: str | None = None
    expiration: int | None = None  # Ripple epoch seconds
    accepted: bool = False

    def to_ledger_fields(self) -> dict:
        """Serialize to XRPL CredentialCreate tx fields."""
        raise NotImplementedError

    @classmethod
    def from_ledger_entry(cls, entry: dict) -> "Credential":
        """Deserialize from an XRPL ledger_entry response."""
        raise NotImplementedError
