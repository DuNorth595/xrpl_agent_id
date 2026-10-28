# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the WalletProvider interface and provider-mode submission.

Why these exist
---------------
v0.5.0 introduced `WalletProvider` so an institution can sign XRPL
transactions with a key that never lives in our process (a KMS, an HSM, a
hardware wallet). The interface is three members; the interesting part is
what happens to the RETRY path, because a provider signs BEFORE submitting
and that changes the transient-error contract.

The bug this locks down
-----------------------
`xrpl.transaction.submit_and_wait` autofills an UNSIGNED transaction on every
call, so resubmitting the same `tx` object is safe: `Sequence` and
`LastLedgerSequence` are recomputed against the current ledger.

But `_get_signed_tx` returns IMMEDIATELY when `transaction.is_signed()` is
true. A provider-signed transaction is signed before submission, so
`submit_and_wait` does NOT autofill it. A retry would resubmit the identical
stale blob, with the original `LastLedgerSequence` still baked in, and fail
with `tefMAX_LEDGER` -- exactly when the retry was most needed.

Measured on public testnet: the autofill horizon is ~19 ledgers (~76 s) and
the retry backoff is 2 s then 4 s. Normally inside the window, but not on a
congested ledger, which is precisely when transient errors occur.

So provider mode must re-autofill AND re-sign on every attempt. These tests
assert that contract explicitly, because if it were dropped the retry would
silently become an infinite no-op against a stale value.

No network access: `submit_and_wait` and `autofill` are imported inside the
functions under test, so these tests monkeypatch them.
"""

from __future__ import annotations

import warnings

import pytest
from xrpl.wallet import Wallet

from xrpl_agent_id.identity import AgentIdentity
from xrpl_agent_id.tx_submit import submit_with_retry
from xrpl_agent_id.wallet import (
    ExternalSignerProvider,
    SeedWalletProvider,
    WalletProvider,
)


class _Resp:
    def __init__(self, result):
        self.result = result


class _TransientErr(Exception):
    """Mimics XRPLReliableSubmissionException's shape for tefPAST_SEQ."""


class _FakeProvider:
    """A minimal provider double.

    Records every sign() call so tests can assert on how many times the
    transaction was re-signed, and on WHICH transaction.
    """

    def __init__(self, classic_address="rFakeProviderAddress", public_key="ED" + "11" * 32):
        self.classic_address = classic_address
        self.public_key = public_key
        self.signed = []

    def sign(self, transaction):
        self.signed.append(transaction)
        signed = _FakeSignedTx(transaction)
        return signed


class _FakeSignedTx:
    """Stands in for a signed xrpl Transaction.

    Only `is_signed()` matters to the code under test; xrpl-py's real
    `submit_and_wait` is monkeypatched away in every test here.
    """

    def __init__(self, original):
        self.original = original
        self.sequence = getattr(original, "sequence", None)
        self.last_ledger_sequence = getattr(original, "last_ledger_sequence", None)

    def is_signed(self):
        return True

    def to_xrpl(self):
        return self.original.to_xrpl()

    def __repr__(self):
        return f"_FakeSignedTx(seq={self.sequence}, lls={self.last_ledger_sequence})"


# ---- Protocol conformance ---------------------------------------------


def test_seed_provider_satisfies_protocol():
    provider = SeedWalletProvider.from_seed(Wallet.create().seed)
    assert isinstance(provider, WalletProvider)


def test_external_provider_satisfies_protocol():
    provider = ExternalSignerProvider(
        classic_address="rExternalProviderAddr",
        public_key="ED" + "22" * 32,
        signer=lambda message_hex: "AB" * 32,
    )
    assert isinstance(provider, WalletProvider)


def test_provider_protocol_is_structural():
    """Any object with the three members satisfies the protocol.

    No inheritance or registration is required -- that is what makes a
    `xrpl4j` or `xrpl.js` provider implementable against the same shape.
    """

    class Duck:
        classic_address = "rDuck"
        public_key = "ED" + "33" * 32

        def sign(self, transaction):
            return transaction

    assert isinstance(Duck(), WalletProvider)


# ---- SeedWalletProvider ------------------------------------------------


def test_seed_provider_exposes_address_and_public_key():
    wallet = Wallet.create()
    provider = SeedWalletProvider(wallet)
    assert provider.classic_address == wallet.classic_address
    assert provider.public_key == wallet.public_key


def test_seed_provider_signs_a_transaction():
    from xrpl.models.transactions import Payment

    wallet = Wallet.create()
    provider = SeedWalletProvider(wallet)
    tx = Payment(
        account=wallet.classic_address,
        destination="rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
        amount="1",
        sequence=1,
        fee="12",
        last_ledger_sequence=100,
    )
    signed = provider.sign(tx)
    assert signed.is_signed(), "the provider must produce a signed transaction"
    assert signed.to_xrpl().get("SigningPubKey") == wallet.public_key
    assert signed.to_xrpl().get("TxnSignature")


def test_seed_provider_repr_does_not_leak_the_seed():
    wallet = Wallet.create()
    provider = SeedWalletProvider(wallet)
    text = repr(provider)
    assert wallet.seed not in text, "the seed must never appear in a repr"
    assert wallet.classic_address in text, "the address SHOULD appear"


# ---- ExternalSignerProvider --------------------------------------------


def test_external_provider_delegates_to_the_signer():
    from xrpl.models.transactions import Payment

    seen = {}

    def signer(message_hex):
        seen["message"] = message_hex
        return "CD" * 32

    wallet = Wallet.create()
    provider = ExternalSignerProvider(
        classic_address=wallet.classic_address,
        public_key=wallet.public_key,
        signer=signer,
    )
    tx = Payment(
        account=wallet.classic_address,
        destination="rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
        amount="1",
        sequence=1,
        fee="12",
        last_ledger_sequence=100,
    )
    signed = provider.sign(tx)
    assert seen["message"], "the signer must be called"
    assert signed.is_signed()
    assert signed.to_xrpl().get("TxnSignature") == "CD" * 32


def test_external_provider_repr_does_not_leak_key_material():
    provider = ExternalSignerProvider(
        classic_address="rExternalProviderAddr",
        public_key="ED" + "55" * 32,
        signer=lambda message_hex: "EF" * 32,
    )
    text = repr(provider)
    assert "EF" * 32 not in text, "no signature material in a repr"
    assert provider.classic_address in text


def test_external_provider_has_no_seed_attribute():
    provider = ExternalSignerProvider(
        classic_address="rExternalProviderAddr",
        public_key="ED" + "66" * 32,
        signer=lambda message_hex: "AB" * 32,
    )
    assert not hasattr(provider, "seed")
    assert not hasattr(provider, "private_key")


# ---- AgentIdentity integration -----------------------------------------


def test_identity_from_seed_still_works():
    """`from_seed` must keep producing a working identity."""
    identity = AgentIdentity.from_seed(Wallet.create().seed, network="testnet")
    assert identity.address.startswith("r")
    assert identity.did.startswith("did:xrpl:2:")
    assert identity.public_key


def test_identity_provider_is_a_seed_provider():
    identity = AgentIdentity.from_seed(Wallet.create().seed, network="testnet")
    assert isinstance(identity.provider, SeedWalletProvider)


def test_identity_works_with_an_external_provider():
    """An external provider must produce the SAME identity as a wallet.

    The DID is derived from the account address, and the DID Document from
    the public key -- both of which the provider supplies. Swapping the
    signer must not change the identity.
    """
    wallet = Wallet.create()
    from_seed = AgentIdentity.from_seed(wallet.seed, network="testnet")
    from_provider = AgentIdentity(
        provider=ExternalSignerProvider(
            classic_address=wallet.classic_address,
            public_key=wallet.public_key,
            signer=lambda message_hex: "AB" * 32,
        ),
        network="testnet",
    )
    assert from_provider.address == from_seed.address
    assert from_provider.did == from_seed.did
    assert from_provider.public_key == from_seed.public_key


def test_identity_wallet_property_is_deprecated():
    identity = AgentIdentity.from_seed(Wallet.create().seed, network="testnet")
    with pytest.warns(DeprecationWarning, match="removed in v0.6.0"):
        assert identity.wallet is not None


def test_identity_seed_property_is_deprecated():
    identity = AgentIdentity.from_seed(Wallet.create().seed, network="testnet")
    with pytest.warns(DeprecationWarning, match="removed in v0.6.0"):
        identity.seed


def test_identity_seed_raises_for_an_external_provider():
    """A provider that signs externally has no seed to expose."""
    identity = AgentIdentity(
        provider=ExternalSignerProvider(
            classic_address="rExternalProviderAddr",
            public_key="ED" + "77" * 32,
            signer=lambda message_hex: "AB" * 32,
        ),
        network="testnet",
    )
    with pytest.warns(DeprecationWarning):
        with pytest.raises(AttributeError, match="does not wrap"):
            identity.seed


def test_identity_wallet_raises_for_an_external_provider():
    identity = AgentIdentity(
        provider=ExternalSignerProvider(
            classic_address="rExternalProviderAddr",
            public_key="ED" + "88" * 32,
            signer=lambda message_hex: "AB" * 32,
        ),
        network="testnet",
    )
    with pytest.warns(DeprecationWarning):
        with pytest.raises(AttributeError, match="does not wrap"):
            identity.wallet


# ---- Provider-mode submission and the retry contract --------------------


def test_provider_mode_submits_with_wallet_none(monkeypatch):
    """A provider-signed tx must be submitted with NO wallet.

    `submit_and_wait` would otherwise sign it itself -- and it would also
    skip the autofill, which is the bug this module exists to prevent.
    """
    seen = {}

    def fake(tx, client, wallet):
        seen["wallet"] = wallet
        return _Resp({"hash": "ABC", "meta": {"TransactionResult": "tesSUCCESS"}})

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    monkeypatch.setattr("xrpl.transaction.autofill", lambda tx, client: tx)

    provider = _FakeProvider()
    result = submit_with_retry("tx", "client", provider)

    assert result["hash"] == "ABC"
    assert seen["wallet"] is None, "provider mode must pass wallet=None"


def test_wallet_mode_still_passes_the_wallet(monkeypatch):
    """The existing wallet path must be unchanged."""
    seen = {}

    def fake(tx, client, wallet):
        seen["wallet"] = wallet
        return _Resp({"hash": "ABC"})

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)

    wallet = Wallet.create()
    submit_with_retry("tx", "client", wallet)
    assert seen["wallet"] is wallet


def test_provider_mode_signs_before_submitting(monkeypatch):
    monkeypatch.setattr("xrpl.transaction.submit_and_wait",
                        lambda tx, client, wallet: _Resp({"hash": "ABC"}))
    monkeypatch.setattr("xrpl.transaction.autofill", lambda tx, client: tx)

    provider = _FakeProvider()
    submit_with_retry("tx", "client", provider)
    assert len(provider.signed) == 1, "the provider must sign the transaction"


def test_provider_mode_resigns_on_retry(monkeypatch):
    """THE KEY TEST.

    A retry must re-autofill AND re-sign. Re-signing a stale transaction
    would still carry the stale `LastLedgerSequence`; re-autofilling without
    re-signing would invalidate the signature.
    """
    attempts = {"n": 0}

    def flaky(tx, client, wallet):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _TransientErr("tefPAST_SEQ: LastLedgerSequence expired")
        return _Resp({"hash": "ABC"})

    autofilled = []

    def fake_autofill(tx, client):
        # Each autofill yields a DIFFERENT ledger horizon, so the test can
        # tell a genuine re-autofill from a stale resubmit.
        fresh = _FakeSignedTx(tx)
        fresh.sequence = len(autofilled) + 1
        fresh.last_ledger_sequence = 1000 + len(autofilled)
        autofilled.append(fresh)
        return fresh

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", flaky)
    monkeypatch.setattr("xrpl.transaction.autofill", fake_autofill)

    provider = _FakeProvider()
    sleeps = []
    result = submit_with_retry("tx", "client", provider, sleep=sleeps.append)

    assert result["hash"] == "ABC"
    assert attempts["n"] == 3, "should have taken three attempts"
    # One autofill AND one sign per attempt.
    assert len(autofilled) == 3, "each attempt must re-autofill"
    assert len(provider.signed) == 3, "each attempt must re-sign"
    # And each attempt used a FRESH horizon, not a stale one.
    horizons = [t.last_ledger_sequence for t in provider.signed]
    assert horizons == sorted(horizons), "the horizon must advance, not repeat"
    assert len(set(horizons)) == 3, "no attempt may reuse a stale horizon"
    # The backoff still happened.
    assert sleeps == [2.0, 4.0]


def test_provider_mode_does_not_retry_a_permanent_error(monkeypatch):
    """A permanent failure must surface immediately, not be retried."""

    def boom(tx, client, wallet):
        raise RuntimeError("telINSUF_FEE_P: Insufficient fee")

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", boom)
    monkeypatch.setattr("xrpl.transaction.autofill", lambda tx, client: tx)

    provider = _FakeProvider()
    with pytest.raises(RuntimeError, match="Insufficient fee"):
        submit_with_retry("tx", "client", provider, sleep=lambda s: None)
    assert len(provider.signed) == 1, "a permanent error must not be retried"


def test_provider_mode_raises_the_last_error_when_exhausted(monkeypatch):
    def always_transient(tx, client, wallet):
        raise _TransientErr("tefPAST_SEQ")

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", always_transient)
    monkeypatch.setattr("xrpl.transaction.autofill", lambda tx, client: tx)

    provider = _FakeProvider()
    with pytest.raises(_TransientErr):
        submit_with_retry("tx", "client", provider, max_attempts=3, sleep=lambda s: None)
    assert len(provider.signed) == 3


def test_provider_mode_does_not_mutate_the_caller_transaction(monkeypatch):
    """The caller's transaction object must not be autofilled in place.

    `autofill` returns a NEW transaction; the original keeps whatever
    sequence/lls the caller gave it. Mutating it would silently corrupt a
    caller's intent.
    """
    monkeypatch.setattr("xrpl.transaction.submit_and_wait",
                        lambda tx, client, wallet: _Resp({"hash": "ABC"}))

    class _Tx:
        sequence = 7
        last_ledger_sequence = 500

        def to_xrpl(self):
            return {}

    original = _Tx()

    def fake_autofill(tx, client):
        fresh = _FakeSignedTx(tx)
        fresh.sequence = 99
        fresh.last_ledger_sequence = 9999
        return fresh

    monkeypatch.setattr("xrpl.transaction.autofill", fake_autofill)

    provider = _FakeProvider()
    submit_with_retry(original, "client", provider)
    assert original.sequence == 7, "the caller's transaction was mutated"
    assert original.last_ledger_sequence == 500


# ---- No key material leaks ---------------------------------------------


def test_no_seed_appears_in_the_provider_reprs():
    wallet = Wallet.create()
    assert wallet.seed not in repr(SeedWalletProvider(wallet))


def test_warnings_do_not_mention_the_seed():
    """The deprecation warnings must not carry key material."""
    seed = Wallet.create().seed
    identity = AgentIdentity.from_seed(seed, network="testnet")
    wallet_seed = seed
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            identity.seed
        except Exception:
            pass
    for w in caught:
        assert wallet_seed not in str(w.message)
