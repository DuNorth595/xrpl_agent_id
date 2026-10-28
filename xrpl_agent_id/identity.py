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
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xrpl_agent_id.tx_submit import submit_with_retry
from xrpl.wallet import Wallet

from xrpl_agent_id.did import DIDDocument, did_from_account
from xrpl_agent_id.wallet import SeedWalletProvider, WalletProvider


class XRPLTransactionError(RuntimeError):
    """A transaction reached the ledger but did not succeed (a tec* result).

    submit_and_wait returns normally in that case, so callers that only catch
    exceptions would otherwise treat a failed CredentialCreate as a success.
    """

if TYPE_CHECKING:
    from xrpl_agent_id.credential import Credential


@dataclass
class AgentIdentity:
    """An XRPL identity for an AI agent.

    Holds a `WalletProvider` -- something that can identify an account and
    sign transactions for it -- and exposes a high-level identity API on top.

    The provider holds the keys (or, for a KMS-backed provider, does NOT
    hold the keys and signs remotely). This class holds the identity layer.

    .. deprecated:: 0.5.0
       The ``wallet`` attribute is deprecated in favour of ``provider`` and
       will be removed in v0.6.0. It is a *field rename*, not a behaviour
       change: `AgentIdentity.from_seed` still produces an identity backed by
       an in-process wallet, and every operation behaves identically.
    """

    provider: WalletProvider
    network: str = "testnet"

    @classmethod
    def from_seed(cls, seed: str, network: str = "testnet") -> "AgentIdentity":
        """Create an AgentIdentity from a master seed (family seed, 's...')."""
        # xrpl-py Wallet auto-detects testnet/mainnet from the seed prefix.
        # We override with the explicit network param.
        w = Wallet.from_seed(seed)
        return cls(provider=SeedWalletProvider(w), network=network)

    @property
    def wallet(self) -> Wallet:
        """The underlying xrpl-py Wallet.

        .. deprecated:: 0.5.0
           Removed in v0.6.0. Use ``.provider`` instead. This only works
           when the provider actually wraps a wallet; a KMS-backed provider
           has no wallet, and asking for one raises ``AttributeError`` with a
           message that says so.
        """
        warnings.warn(
            "AgentIdentity.wallet is deprecated and will be removed in "
            "v0.6.0; use AgentIdentity.provider instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        underlying = getattr(self.provider, "wallet", None)
        if underlying is None:
            raise AttributeError(
                "This AgentIdentity's provider does not wrap an "
                "xrpl.wallet.Wallet (it signs externally, e.g. via a KMS), "
                "so it has no .wallet. Use .provider instead."
            )
        return underlying

    @property
    def did(self) -> str:
        """did:xrpl:<network-id>:<account> — see XLS-40d."""
        return did_from_account(self.address, self.network)

    @property
    def address(self) -> str:
        """The raw XRPL classic address (rXXX...)."""
        return self.provider.classic_address

    @property
    def public_key(self) -> str:
        """The master public key (hex, compressed)."""
        return self.provider.public_key

    @property
    def seed(self) -> str:
        """The master seed. NEVER log this. NEVER print this.

        .. deprecated:: 0.5.0
           Removed in v0.6.0. A provider that signs externally (a KMS, an
           HSM, a hardware wallet) has no seed at all, so this property
           cannot be part of the identity contract -- only of the
           in-process-wallet case. Use ``.provider`` and, if you truly need
           the seed, ``.provider.wallet.seed`` on a
           `SeedWalletProvider`.
        """
        warnings.warn(
            "AgentIdentity.seed is deprecated and will be removed in v0.6.0; "
            "a provider that signs externally has no seed to expose.",
            DeprecationWarning,
            stacklevel=2,
        )
        underlying = getattr(self.provider, "wallet", None)
        if underlying is None:
            raise AttributeError(
                "This AgentIdentity's provider does not wrap an "
                "xrpl.wallet.Wallet, so it has no seed. A KMS-backed "
                "provider never holds the key at all."
            )
        return underlying.seed

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
        result = submit_with_retry(tx, client, self.provider)
        tx_hash = result.get("hash")
        # tx_result like "tesSUCCESS"
        tx_result = (result.get("meta") or {}).get("TransactionResult")

        # submit_and_wait returns normally for a tec* result that still reached
        # the ledger, so returning the Credential here reported success for a
        # transaction that failed. Surface it instead of swallowing it.
        if tx_result and tx_result != "tesSUCCESS":
            raise XRPLTransactionError(
                f"CredentialCreate failed: tx_result={tx_result} (tx {tx_hash})"
            )

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
        result = submit_with_retry(tx, client, self.provider)
        return (result or {}).get("hash", "")

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
        result = submit_with_retry(tx, client, self.provider)
        return (result or {}).get("hash", "")

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
        result = submit_with_retry(tx, client, self.provider)
        return (result or {}).get("hash", "")

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
        if node is None:
            return False
        # Existence is not acceptance: XLS-70 sets lsfAccepted only when the
        # subject signs. An issuer-created credential nobody accepted must
        # not count as held.
        from xrpl_agent_id.credential import credential_is_accepted
        return credential_is_accepted(node)


    def resolve_did_document(self) -> DIDDocument:
        """Fetch this agent's DID Document from the ledger."""
        from xrpl_agent_id.did import resolve_did
        return resolve_did(self.did, network=self.network)


def _did_to_addr(did: str) -> str:
    """Extract the address from a did:xrpl:netid:addr string."""
    from xrpl_agent_id.did import parse_did
    _, addr = parse_did(did)
    return addr
