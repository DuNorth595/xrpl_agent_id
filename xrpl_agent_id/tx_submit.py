"""Shared transaction submission with transient-ledger-error retry.

Why this module exists
----------------------
Measured on public XRPL Testnet and Devnet, an unretried `CredentialCreate`
failed roughly 1 run in 4 with:

    XRPLReliableSubmissionException: The latest validated ledger sequence
    21759843 is greater than LastLedgerSequence 21759843 in the transaction.
    Prelim result: tefPAST_SEQ

`tefPAST_SEQ` is not a malformed transaction -- it means a ledger closed
between autofill and validation, so the transaction's 75-ledger
`LastLedgerSequence` window expired before the tx landed. Public networks
close a ledger every ~4s, so the window is easy to miss. Re-submitting the
SAME transaction object would fail identically forever, because its
`LastLedgerSequence` is already baked in.

`xrpl.transaction.submit_and_wait` autofills (`autofill=True` by default), so
each fresh call re-derives `Sequence` and `LastLedgerSequence` from the
current ledger. That makes a retry genuinely safe rather than a no-op.

This mirrors the retry that already existed in
`scripts/stress_harness_live.py::_submit_with_retry`, but at the library
level so every consumer -- not just the stress harness -- gets it.

Providers vs wallets
--------------------
The third argument may be either an `xrpl.wallet.Wallet` or a
`WalletProvider` (see `xrpl_agent_id.wallet`). The difference matters for
retries:

* With a **Wallet**, `submit_and_wait` autofills the unsigned transaction on
  every call, so `Sequence` and `LastLedgerSequence` are recomputed against
  the current ledger. Retrying the same `tx` object is safe.

* With a **provider**, the transaction is signed BEFORE it is submitted.
  `xrpl.transaction.submit_and_wait` calls `_get_signed_tx`, which returns
  immediately when `transaction.is_signed()` is true -- so it does NOT
  autofill. A retry would resubmit the identical signed blob, with the
  original `LastLedgerSequence` still baked in, and fail with
  `tefMAX_LEDGER` exactly when the retry was most needed (a slow ledger
  closing inside the backoff window).

  So in provider mode this module re-autofills AND re-signs on every
  attempt. Measured autofill horizon on public testnet: ~19 ledgers, about
  76 seconds, against a backoff of 2s then 4s -- normally inside the window,
  but not on a congested ledger.

Event loop
----------
This module is SYNCHRONOUS, and so is the xrpl-py API it builds on
(`autofill` and `submit_and_wait` are both `asyncio.run()` wrappers).
Neither can be called from inside a running event loop -- doing so raises
`RuntimeError: asyncio.run() cannot be called from a running event loop`.

That constraint is xrpl-py's, not this library's, and it predates v0.5.0:
`submit_and_wait` hits the same wall. Call this from synchronous code, or
from a thread with no loop running. An async entry point would be a separate
addition, not a change to this one.
"""

from __future__ import annotations

import time
from typing import Any, Optional

#: Error fragments that mean "a ledger closed under you", not "bad tx".
TRANSIENT_MARKERS = (
    "tefPAST_SEQ",
    "tefMAX_LEDGER",
    "LastLedgerSequence",
    "XRPLReliableSubmissionException",
)


def is_transient_ledger_error(exc: BaseException) -> bool:
    """True when `exc` looks like a ledger-close race rather than a real fault.

    Checks both the exception message and the transaction result the xrpl-py
    exception may carry, because the marker can appear in either depending on
    how far the submission got before failing.
    """
    msg = str(exc)
    if any(marker in msg for marker in TRANSIENT_MARKERS):
        return True
    result = getattr(exc, "result", None)
    if isinstance(result, str) and any(marker in result for marker in TRANSIENT_MARKERS):
        return True
    return False


def _is_provider(obj: Any) -> bool:
    """True when `obj` quacks like a `WalletProvider`.

    Structural check rather than an isinstance, so that a provider defined
    outside this package (or a test double) works without importing our
    class. The three members are the whole contract.
    """
    return (
        hasattr(obj, "sign")
        and hasattr(obj, "classic_address")
        and hasattr(obj, "public_key")
    )


def _prepare_attempt(
    tx: Any,
    client: Any,
    provider: Any,
) -> Any:
    """Return the transaction to submit for THIS attempt.

    In provider mode this autofills (recomputing `Sequence`, `Fee` and
    `LastLedgerSequence` against the current ledger) and then signs. Both
    halves are required: signing a stale transaction would still carry a
    stale `LastLedgerSequence`, and autofilling without re-signing would
    invalidate the signature.

    In wallet mode the transaction is returned untouched -- `submit_and_wait`
    autofills and signs it itself.
    """
    if not _is_provider(provider):
        return tx

    from xrpl.transaction import autofill

    fresh = autofill(tx, client)
    return provider.sign(fresh)


def submit_with_retry(
    tx: Any,
    client: Any,
    wallet: Any,
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    sleep=time.sleep,
) -> Optional[dict]:
    """`submit_and_wait` that retries transient ledger races.

    Returns the response's `.result` dict (the same shape the callers were
    already reading `hash` / `meta.TransactionResult` from), or None when the
    transaction succeeded but returned no result payload.

    Non-transient failures raise immediately -- a malformed transaction or an
    insufficient balance must surface to the caller, not be retried into a
    misleading timeout. After `max_attempts` transient failures, the last
    exception is re-raised rather than swallowed.

    Args:
        tx: the transaction to submit. Must be UNSIGNED in provider mode --
            this module signs it. In wallet mode it may be signed or
            unsigned.
        client: an xrpl-py client for the target network.
        wallet: an `xrpl.wallet.Wallet` OR a `WalletProvider`.
        max_attempts: how many times to try before re-raising.
        base_delay: seconds; the wait before attempt N+1 is `base_delay * N`.
        sleep: injection point for tests.
    """
    from xrpl.transaction import submit_and_wait

    provider_mode = _is_provider(wallet)
    # In provider mode we submit an already-signed transaction, so xrpl-py
    # must NOT be handed a wallet to sign with. Passing None makes
    # `_get_signed_tx` return the signed transaction untouched -- it does not
    # autofill it a second time.
    signer_for_xrpl = None if provider_mode else wallet

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            # In provider mode this autofills and signs for THIS attempt.
            # Doing it inside the loop (rather than once up front) is the
            # whole point: a retry must pick up a fresh Sequence and
            # LastLedgerSequence, and the signature has to cover those.
            #
            # It is exactly ONE autofill per attempt, not two: the
            # transaction we submit is already signed, so xrpl-py's
            # `_get_signed_tx` returns it untouched instead of autofilling
            # it again.
            attempt_tx = _prepare_attempt(tx, client, wallet)
            response = submit_and_wait(attempt_tx, client, signer_for_xrpl)
            return response.result
        except Exception as exc:  # xrpl raises several submission exceptions
            if not is_transient_ledger_error(exc):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                break
            # Linear backoff: a ledger is ~4s on public networks, so 2s then
            # 4s straddles the gap without sleeping longer than needed.
            delay = base_delay * attempt
            print(
                f"[xrpl_agent_id] transient ledger error on attempt "
                f"{attempt}/{max_attempts} ({exc.__class__.__name__}); "
                f"re-autofilling and retrying in {delay:.0f}s"
            )
            sleep(delay)

    # Exhausted: surface the real cause, never a bare "failed".
    assert last_exc is not None  # loop always sets it before breaking
    raise last_exc
