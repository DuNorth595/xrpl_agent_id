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
    """
    from xrpl.transaction import submit_and_wait

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            # submit_and_wait autofills each call, so Sequence and
            # LastLedgerSequence are recomputed against the current ledger.
            # The tx object itself is never mutated with a stale value.
            response = submit_and_wait(tx, client, wallet)
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