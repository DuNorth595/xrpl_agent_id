# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Authority — the issuer side of the credential flow.

A separate class from AgentIdentity for clarity: an Authority can ONLY issue
credentials (sign CredentialCreate) and look up existing ones. It cannot
accept or modify credentials.

In most real deployments, an Authority is a service (eval registry,
attestation provider) rather than an individual agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet

from xrpl_agent_id.identity import AgentIdentity


@dataclass
class VerificationResult:
    """Result of a batch verification (Authority.verify_set or TrustRegistry.check).

    Attributes:
        agent_did: The DID that was checked.
        results: Mapping of "<issuer_address>:<credential_type_hex>" → bool.
        missing: List of (issuer, credential_type) pairs that were NOT found.
        unexpected_extra: List of (issuer, credential_type) pairs held by the
            agent that the caller did NOT ask about (informational only).
    """

    agent_did: str
    results: dict[str, bool]
    missing: list[tuple[str, bytes]] = field(default_factory=list)
    unexpected_extra: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def all_satisfied(self) -> bool:
        """True iff every required credential was found accepted."""
        return all(self.results.values())

    def summary(self) -> str:
        n_total = len(self.results)
        n_have = sum(1 for v in self.results.values() if v)
        return f"{n_have}/{n_total} required credentials satisfied"


class Authority(AgentIdentity):
    """An Authority — an entity that can issue XLS-70 credentials.

    Inherits the wallet handling and identity properties from
    AgentIdentity. Constrained: cannot accept credentials, only issue them.
    """

    def accept_credential(self, *args, **kwargs):  # noqa: D401
        raise NotImplementedError(
            "Authority cannot accept credentials — only AgentIdentity (subject role) can."
        )

    def set_did_document(self, *args, **kwargs):  # noqa: D401
        raise NotImplementedError(
            "Authority cannot set its own DID Document — instantiate an AgentIdentity instead."
        )

    # ---- Revocation --------------------------------------------------------

    def revoke_credential(
        self,
        subject: str,
        credential_type: bytes,
        uri: str | None = None,
    ) -> str:
        """Revoke a previously issued credential via CredentialDelete.

        XLS-70 enforces uniqueness on (issuer, subject, credential_type) —
        a second CredentialCreate for the same triple returns `tecDUPLICATE`.
        The supported revocation mechanism is `CredentialDelete`, which can
        be signed by either the issuer OR the subject.

        This method submits CredentialDelete, which removes the credential
        from the ledger. For a softer revocation (audit trail preserved),
        set the credential's `expiration` field at issuance time instead.

        Returns the transaction hash.
        """
        from xrpl_agent_id.network import get_client
        from xrpl.models.transactions import CredentialDelete

        subject_addr = subject if not subject.startswith("did:xrpl:") else _did_to_addr(subject)

        tx = CredentialDelete(
            account=self.address,
            subject=subject_addr,
            credential_type=credential_type.hex().upper(),
        )
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        return (response.result or {}).get("hash", "")

    # ---- Bulk verification -------------------------------------------------

    def verify_set(
        self,
        agent_did: str,
        required: Iterable[tuple[str, bytes]],
    ) -> VerificationResult:
        """Check whether an agent holds all of a set of required credentials.

        Args:
            agent_did: The DID or classic address of the agent being verified.
            required: Iterable of (issuer_address, credential_type_bytes) pairs.

        Returns:
            VerificationResult — call .all_satisfied or .summary() for quick checks.

        Note: this queries the ledger N times where N is len(required). For
        large sets, prefer TrustRegistry which supports allow/deny lists and
        caches lookups. Each query is a single JSON-RPC round-trip.
        """
        from xrpl_agent_id.network import get_client
        from xrpl_agent_id.did import parse_did

        if agent_did.startswith("did:xrpl:"):
            _, subject_addr = parse_did(agent_did)
        else:
            subject_addr = agent_did

        results: dict[str, bool] = {}
        missing: list[tuple[str, bytes]] = []

        client = get_client(self.network)
        for issuer_addr, cred_type in required:
            key = f"{issuer_addr}:{cred_type.hex().upper()}"
            held = _check_credential(client, subject_addr, issuer_addr, cred_type)
            results[key] = held
            if not held:
                missing.append((issuer_addr, cred_type))

        return VerificationResult(
            agent_did=agent_did,
            results=results,
            missing=missing,
        )


def _check_credential(client, subject: str, issuer: str, credential_type: bytes) -> bool:
    """Single-credential lookup helper."""
    from xrpl.models.requests import LedgerEntry
    from xrpl.models.requests.ledger_entry import Credential as LedgerCredential

    try:
        response = client.request(
            LedgerEntry(
                credential=LedgerCredential(
                    subject=subject,
                    issuer=issuer,
                    credential_type=credential_type.hex().upper(),
                )
            )
        )
    except Exception:
        return False
    return (response.result or {}).get("node") is not None


def _did_to_addr(did: str) -> str:
    """Extract the address from a did:xrpl:netid:addr string."""
    from xrpl_agent_id.did import parse_did
    _, addr = parse_did(did)
    return addr
