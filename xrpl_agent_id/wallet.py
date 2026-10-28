# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""WalletProvider — a pluggable signer for XRPL transactions.

Why this exists
---------------
`xrpl-py` binds signing to a concrete `xrpl.wallet.Wallet` object that holds
the private key in process. That is fine for a hobby script and wrong for an
institution: a key management system, a cloud HSM, or a hardware wallet signs
*remotely* and never reveals the key. Such a signer cannot be expressed as an
`xrpl.wallet.Wallet`.

The XRP Ledger already supports the shape such a provider needs: `rippled`'s
`submit` method accepts a pre-signed `tx_blob`, and `xrpl-py`'s
`submit_and_wait` accepts an already-signed transaction with `wallet=None`.
So the ledger-side plumbing for an external signer already exists — what was
missing was a Python-side interface for it.

The interface
-------------
Three members, deliberately minimal and language-neutral so that a future
`xrpl4j` or `xrpl.js` provider can implement the same shape:

    classic_address  -> the account's r... address
    public_key       -> the compressed master public key (hex)
    sign(tx)         -> a signed copy of the transaction

What is deliberately ABSENT: `seed` and `private_key`. A provider that holds a
key in process has one; a KMS-backed provider does not and must not. If you
need a seed, you are using `SeedWalletProvider`, and you can get it from
`.wallet.seed` there — deliberately, not by accident.

Providers shipped here
----------------------
    SeedWalletProvider     wraps an `xrpl.wallet.Wallet` (current behaviour)
    ExternalSignerProvider signs via any callable that returns a signature

Usage
-----
    from xrpl_agent_id.wallet import SeedWalletProvider

    provider = SeedWalletProvider.from_seed(seed, network="testnet")
    identity = AgentIdentity(provider=provider, network="testnet")

For an external signer, implement `sign` yourself — see
`ExternalSignerProvider`, which takes a signing callable and does the
XRPL-specific encoding for you.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from xrpl.core.binarycodec import encode_for_signing
from xrpl.core.keypairs import sign as _keypairs_sign
from xrpl.models.transactions.transaction import Transaction
from xrpl.wallet import Wallet


@runtime_checkable
class WalletProvider(Protocol):
    """Something that can identify an account and sign transactions for it.

    This is a `typing.Protocol`, so any object with these three members
    satisfies it — no inheritance, no registration. Implementations are
    expected to be safe to use across threads only if they say so; nothing
    here assumes a lock is held.
    """

    @property
    def classic_address(self) -> str:
        """The account's classic address (`r...`)."""
        ...

    @property
    def public_key(self) -> str:
        """The compressed master public key, hex-encoded."""
        ...

    def sign(self, transaction: Transaction) -> Transaction:
        """Return a signed copy of `transaction`.

        The returned transaction must carry `SigningPubKey` and `TxnSignature`
        so that `Transaction.is_signed()` is True. The input transaction
        should not be mutated.
        """
        ...


class SeedWalletProvider:
    """A `WalletProvider` backed by an in-process `xrpl.wallet.Wallet`.

    This is the current behaviour of the library, unchanged. It exists so that
    switching to a provider is a refactor, not a behaviour change.

    Unlike the other providers, this one HAS a seed and a private key. It
    exposes `.wallet` for callers that need them — that is the deliberate
    escape hatch, and it is why `AgentIdentity.wallet` is deprecated in
    favour of `AgentIdentity.provider`.
    """

    def __init__(self, wallet: Wallet) -> None:
        self.wallet = wallet

    @classmethod
    def from_seed(cls, seed: str) -> "SeedWalletProvider":
        """Build a provider from a master seed (family seed, `s...`)."""
        return cls(Wallet.from_seed(seed))

    @property
    def classic_address(self) -> str:
        return self.wallet.classic_address

    @property
    def public_key(self) -> str:
        return self.wallet.public_key

    def sign(self, transaction: Transaction) -> Transaction:
        """Sign using the wallet's key.

        Mirrors what `xrpl.transaction.sign` does, but goes through the
        wallet directly so the result is identical whether the caller goes
        through this provider or through `submit_and_wait`'s own signing path.
        """
        tx_json = transaction.to_xrpl()
        tx_json["SigningPubKey"] = self.wallet.public_key
        tx_json["TxnSignature"] = _keypairs_sign(
            bytes.fromhex(encode_for_signing(tx_json)),
            self.wallet.private_key,
        )
        return Transaction.from_xrpl(tx_json)

    def __repr__(self) -> str:
        # Never let a seed reach a log line or a traceback.
        return f"SeedWalletProvider(classic_address={self.classic_address!r})"


class ExternalSignerProvider:
    """A `WalletProvider` that delegates signing to an external system.

    The signer is any callable that takes the canonical XRPL signing bytes
    (as a hex string) and returns a hex signature. That is the shape a KMS,
    an HSM, or a hardware wallet can satisfy — each of them can sign a
    digest, and none of them can hand back the key.

    Example -- an AWS/GCP/Azure KMS, sketched (not tested against a live
    KMS; see the note in the class docstring):

        class KmsSigner:
            def __init__(self, key_id, kms_client):
                self._key_id = key_id
                self._kms = kms_client

            def __call__(self, message_hex: str) -> str:
                # The KMS signs a digest, not a pre-hashed message, so the
                # bytes have to be hashed the way the KMS expects.
                digest = hashlib.sha256(bytes.fromhex(message_hex)).digest()
                response = self._kms.sign(KeyId=self._key_id, Message=digest, ...)
                return response["Signature"].hex()

        provider = ExternalSignerProvider(
            classic_address="r...",
            public_key="ED...",
            signer=KmsSigner(...),
        )

    Notes
    -----
    * The public key must be the COMPRESSED XRPL form (hex, `ED` prefix for
      Ed25519 or `EC` prefix for secp256k1). Cloud KMS systems usually hand
      back DER or PEM, which has to be decompressed first. See
      `xrpl4j#441` upstream, which is still the best written description of
      that problem.
    * AWS KMS does not support Ed25519. Use secp256k1 in the KMS and expect
      the `EC` prefix on the public key.
    * This class performs NO caching of signatures and NO internal retry.
      Retries belong to `submit_with_retry`, so there is exactly one retry
      story rather than two.
    """

    def __init__(
        self,
        classic_address: str,
        public_key: str,
        signer: Any,
    ) -> None:
        """
        Args:
            classic_address: the account's classic address (`r...`).
            public_key: the compressed master public key, hex-encoded.
            signer: a callable taking the signing bytes as a hex string and
                returning a hex signature.
        """
        self._classic_address = classic_address
        self._public_key = public_key
        self._signer = signer

    @property
    def classic_address(self) -> str:
        return self._classic_address

    @property
    def public_key(self) -> str:
        return self._public_key

    def sign(self, transaction: Transaction) -> Transaction:
        """Sign `transaction` by delegating to the external signer."""
        tx_json = transaction.to_xrpl()
        tx_json["SigningPubKey"] = self._public_key
        tx_json["TxnSignature"] = self._signer(encode_for_signing(tx_json))
        return Transaction.from_xrpl(tx_json)

    def __repr__(self) -> str:
        return (
            f"ExternalSignerProvider(classic_address={self._classic_address!r}, "
            f"public_key={self._public_key[:8]!r}...)"
        )


def provider_from_wallet(wallet: Wallet) -> SeedWalletProvider:
    """Convenience: wrap an existing `Wallet` in a provider."""
    return SeedWalletProvider(wallet)
