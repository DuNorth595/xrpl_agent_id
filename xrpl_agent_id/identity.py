# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""AgentIdentity — wraps an xrpl-py Wallet with identity operations.

Surface:
    AgentIdentity.from_seed(seed, network)   — build from master seed
    .did / .address / .public_key            — identity attributes
    .accept_credential(...)                  — accept a CredentialCreate
    .set_did_document(doc)                   — write a DID to the ledger
    .has_credential(issuer, type)            — check ledger
    .resolve_did_document()                  — read back from ledger

Issuance is split into `Authority` (xrpl_agent_id.authority) because in the
canonical XLS-70 flow the Issuer signs and submits, not the Subject.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet

from xrpl_agent_id.did import DIDDocument, did_from_account

if TYPE_CHECKING:
    from xrpl_agent_id.credential import Credential


@dataclass
class AgentIdentity:
    """An XRPL identity for an AI agent.

    Wraps an xrpl-py Wallet and exposes a high-level identity API.
    The wallet holds the keys. This class holds the identity layer on top.
    """

    wallet: Wallet
    network: str = "testnet"

    @classmethod
    def from_seed(cls, seed: str, network: str = "testnet") -> "AgentIdentity":
        """Create an AgentIdentity from a master seed (family seed, 's...')."""
        # xrpl-py Wallet auto-detects testnet/mainnet from the seed prefix.
        # We override with the explicit network param.
        w = Wallet.from_seed(seed)
        return cls(wallet=w, network=network)

    @property
    def did(self) -> str:
        """did:xrpl:<network-id>:<account> — see XLS-40d."""
        return did_from_account(self.address, self.network)

    @property
    def address(self) -> str:
        """The raw XRPL classic address (rXXX...)."""
        return self.wallet.classic_address

    @property
    def public_key(self) -> str:
        """The master public key (hex, compressed)."""
        return self.wallet.public_key

    @property
    def seed(self) -> str:
        """The master seed. NEVER log this. NEVER print this."""
        return self.wallet.seed

    # ---- Issuer-side operations -------------------------------------------

    def issue_credential(
        self,
        subject: str,
        credential_type: bytes,
        uri: str | None = None,
        expiration: int | None = None,
    ) -> "Credential":
        """Issue a credential to a subject's DID or address.

        Submits a CredentialCreate transaction. Returns the Credential
        object (signed but not yet on the subject's side until accepted).
        """
        from xrpl_agent_id.credential import Credential
        from xrpl_agent_id.network import get_client

        # Build the credential object
        subject_did = subject if subject.startswith("did:xrpl:") else did_from_account(subject, self.network)
        cred = Credential(
            issuer=self.did,
            subject=subject_did,
            credential_type=credential_type,
            uri=uri,
            expiration=expiration,
            accepted=False,
        )

        # Build the CredentialCreate tx
        from xrpl.models.transactions import CredentialCreate

        tx_fields = cred.to_ledger_fields()
        tx = CredentialCreate(account=self.address, **tx_fields)

        # Submit
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        result = response.result or {}
        tx_hash = result.get("hash")
        # tx_result like "tesSUCCESS"
        tx_result = (result.get("meta") or {}).get("TransactionResult")

        # The Credential object we return represents what we just created;
        # the subject has not yet accepted.
        cred.accepted = False
        return cred

    # ---- Subject-side operations ------------------------------------------

    def accept_credential(
        self,
        issuer: str,
        credential_type: bytes,
    ) -> str:
        """Accept a CredentialCreate by submitting CredentialAccept.

        Returns the transaction hash.
        """
        from xrpl_agent_id.network import get_client
        from xrpl.models.transactions import CredentialAccept

        issuer_addr = issuer if not issuer.startswith("did:xrpl:") else _did_to_addr(issuer)
        tx = CredentialAccept(
            account=self.address,
            issuer=issuer_addr,
            credential_type=credential_type.hex().upper(),
        )
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        return (response.result or {}).get("hash", "")

    # ---- DID operations ----------------------------------------------------

    def set_did_document(self, doc: DIDDocument) -> str:
        """Write this agent's DID Document to the ledger via DIDSet.

        Returns the transaction hash.
        """
        from xrpl_agent_id.network import get_client
        from xrpl.models.transactions import DIDSet

        doc_json = doc.to_json()
        doc_hex = _json.dumps(doc_json, separators=(",", ":")).encode("utf-8").hex().upper()

        if len(doc_hex) > 512:  # 256 bytes = 512 hex chars
            raise ValueError(
                f"DIDDocument hex-encoded exceeds 256 bytes "
                f"({len(doc_hex) // 2} bytes). Use set_did_uri() instead."
            )

        tx = DIDSet(
            account=self.address,
            did_document=doc_hex,
        )
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        return (response.result or {}).get("hash", "")

    def set_did_uri(self, uri: str) -> str:
        """Write only the DID's URI field (DIDDocument stays implicit).

        Use this when the DID Document is too large for the 256-byte
        on-ledger cap — store a small on-ledger doc, point at the full
        doc via URI (HTTPS / IPFS).

        Returns the transaction hash.
        """
        from xrpl_agent_id.network import get_client
        from xrpl.models.transactions import DIDSet

        uri_hex = uri.encode("utf-8").hex().upper()
        if len(uri_hex) > 512:
            raise ValueError(f"URI hex exceeds 256 bytes ({len(uri_hex) // 2} bytes)")

        tx = DIDSet(account=self.address, uri=uri_hex)
        client = get_client(self.network)
        response = submit_and_wait(tx, client, self.wallet)
        return (response.result or {}).get("hash", "")

    # ---- Verification ------------------------------------------------------

    def has_credential(
        self,
        issuer: str,
        credential_type: bytes,
    ) -> bool:
        """Return True iff the ledger has an accepted Credential entry for
        (issuer, self.address, credential_type).
        """
        from xrpl_agent_id.network import get_client
        from xrpl.models.requests import LedgerEntry
        from xrpl.models.requests.ledger_entry import Credential as LedgerCredential

        issuer_addr = issuer if not issuer.startswith("did:xrpl:") else _did_to_addr(issuer)
        client = get_client(self.network)
        try:
            response = client.request(
                LedgerEntry(
                    credential=LedgerCredential(
                        subject=self.address,
                        issuer=issuer_addr,
                        credential_type=credential_type.hex().upper(),
                    )
                )
            )
        except Exception:
            return False
        node = (response.result or {}).get("node")
        return node is not None

    def resolve_did_document(self) -> DIDDocument:
        """Fetch this agent's DID Document from the ledger."""
        from xrpl_agent_id.did import resolve_did
        return resolve_did(self.did, network=self.network)


def _did_to_addr(did: str) -> str:
    """Extract the address from a did:xrpl:netid:addr string."""
    from xrpl_agent_id.did import parse_did
    _, addr = parse_did(did)
    return addr
