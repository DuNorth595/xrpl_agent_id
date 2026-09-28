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

`credential_type` on-chain is a hex string (xrpl-py serializes bytes as hex).
On the Python side we keep it as raw bytes for ergonomics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xrpl_agent_id.did import DIDDocument


# Standard credential type strings used by this library.
# Pick a value ≤ 64 bytes (XLS-70 limit) and human-readable.
class CredentialType(str, Enum):
    """Canonical credential type identifiers used by xrpl_agent_id."""

    AGENT_ID_V1 = "agent_identity_v1"
    VERIFIED_AGENT_OPERATOR = "verified_agent_operator"
    EVAL_PASSED = "agent_eval_passed_v1"


# Ripple epoch: 2000-01-01 00:00:00 UTC (seconds)
RIPPLE_EPOCH = 946684800


@dataclass
class Credential:
    """A verifiable credential held by an XRPL agent.

    On-chain this maps 1:1 to a Credential ledger entry (XLS-70).
    """

    issuer: str  # did:xrpl:...
    subject: str  # did:xrpl:...
    credential_type: bytes  # arbitrary, hex-encoded on-chain
    uri: str | None = None
    expiration: int | None = None  # Ripple epoch seconds
    accepted: bool = False

    def __post_init__(self) -> None:
        # Length checks per XLS-70
        if len(self.credential_type) == 0:
            raise ValueError("credential_type must be non-empty bytes")
        if len(self.credential_type) > 64:
            raise ValueError(
                f"credential_type exceeds XLS-70 max of 64 bytes "
                f"(got {len(self.credential_type)})"
            )
        if self.uri is not None and len(self.uri.encode("utf-8")) > 256:
            raise ValueError("uri exceeds XLS-70 max of 256 bytes")

    @property
    def hex_type(self) -> str:
        """The credential_type as a hex string, ready for XRPL wire format."""
        return self.credential_type.hex().upper()

    @property
    def hex_uri(self) -> str | None:
        """The URI as a hex string (lowercase), per XRPL wire format."""
        if self.uri is None:
            return None
        return self.uri.encode("utf-8").hex().upper()

    def to_ledger_fields(self) -> dict:
        """Serialize to XRPL CredentialCreate transaction fields.

        Returns a dict with the keys `subject`, `credential_type`, `uri`,
        `expiration` in the exact form xrpl-py 4.5.0 expects.

        NB: `issuer` is NOT in this dict — CredentialCreate is signed by
        the Issuer, who supplies their own address via `account`. The
        library handles this when constructing the tx.
        """
        from xrpl_agent_id.did import parse_did

        _, subject_addr = parse_did(self.subject)
        fields: dict = {
            "subject": subject_addr,
            "credential_type": self.hex_type,
        }
        if self.uri is not None:
            fields["uri"] = self.hex_uri
        if self.expiration is not None:
            fields["expiration"] = self.expiration
        return fields

    @classmethod
    def from_ledger_entry(cls, entry: dict, *, expected_subject: str | None = None) -> "Credential":
        """Deserialize from an XRPL ledger_entry response.

        `entry` should be the `node` field of a `ledger_entry` response for a
        `credential` lookup. Decodes hex fields back to Python types.

        If `expected_subject` is provided and the entry is for a different
        subject, returns None. (Ripple indexes credential entries by issuer
        + subject + type — same lookup, different `subject` → different row.)
        """
        # XRPL returns the credential fields with these names (snake_case)
        issuer = entry.get("Issuer") or entry.get("issuer")
        subject = entry.get("Subject") or entry.get("subject")
        cred_type_hex = entry.get("CredentialType") or entry.get("credential_type")
        uri_hex = entry.get("URI") or entry.get("uri")
        expiration = entry.get("Expiration") or entry.get("expiration")

        if expected_subject is not None and subject != expected_subject:
            raise ValueError(
                f"ledger entry subject mismatch: expected {expected_subject}, "
                f"got {subject}"
            )

        from xrpl_agent_id.did import did_from_account
        from xrpl_agent_id.network import NETWORKS

        # Re-derive DIDs (we don't know which network the issuer was on,
        # but `mainnet` works because the address is the same on all networks
        # — the network_id is part of the DID method, not the address)
        issuer_did = did_from_account(issuer, "mainnet") if issuer else ""
        subject_did = did_from_account(subject, "mainnet") if subject else ""

        return cls(
            issuer=issuer_did,
            subject=subject_did,
            credential_type=bytes.fromhex(cred_type_hex),
            uri=bytes.fromhex(uri_hex).decode("utf-8") if uri_hex else None,
            expiration=expiration,
            accepted=True,  # existence in ledger implies accepted (post XLS-70d)
        )

    def to_w3c_vc(self) -> dict:
        """Render as a W3C Verifiable Credentials JSON form.

        This is for off-chain consumption (e.g. presenting to a verifier that
        doesn't read XRPL). The full claim details live at `uri`.
        """
        return {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://xrpl.org/credentials/v1",
            ],
            "type": ["VerifiableCredential", self.credential_type.decode("ascii", errors="replace")],
            "issuer": self.issuer,
            "issuanceDate": _ripple_to_iso(self.expiration),  # placeholder if no issuance timestamp
            "credentialSubject": {
                "id": self.subject,
            },
            "credentialStatus": {
                "id": f"{self.issuer}#credential-{self.hex_type}",
                "type": "XRPLCredentialStatus",
            },
        }


def _ripple_to_iso(ripple_seconds: int | None) -> str | None:
    """Convert Ripple epoch seconds to ISO 8601 (UTC)."""
    if ripple_seconds is None:
        return None
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(ripple_seconds + RIPPLE_EPOCH, tz=timezone.utc)
    return dt.isoformat()
