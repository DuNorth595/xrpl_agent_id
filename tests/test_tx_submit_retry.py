"""Regression tests for transient ledger-error retry in tx submission.

The bug these lock down
-----------------------
An unretried `submit_and_wait` fails roughly 1 run in 4 on public XRPL
Testnet/Devnet with `tefPAST_SEQ`: a ledger closed between autofill and
validation, so the transaction's 75-ledger `LastLedgerSequence` window
expired. Measured as 5 passed / 1 failed across repeated live runs.

Why retry is safe (and not a no-op)
-----------------------------------
`xrpl.transaction.submit_and_wait` autofills on every call, re-deriving
`Sequence` and `LastLedgerSequence` from the current ledger. Resubmitting the
same transaction object therefore gets a fresh window. These tests assert that
contract explicitly, because if `submit_and_wait` ever stopped autofilling the
retry would silently become an infinite no-op loop against a stale value.

No network access: `submit_and_wait` is imported inside the function under
test, so these tests monkeypatch `xrpl.transaction.submit_and_wait`.
"""

import pytest

from xrpl_agent_id.tx_submit import (
    TRANSIENT_MARKERS,
    is_transient_ledger_error,
    submit_with_retry,
)


class _Resp:
    def __init__(self, result):
        self.result = result


class _TransientErr(Exception):
    """Mimics XRPLReliableSubmissionException's shape for tefPAST_SEQ."""


class _PermanentErr(Exception):
    pass


def test_transient_markers_classified_true():
    for marker in TRANSIENT_MARKERS:
        exc = _TransientErr(f"The latest validated ledger sequence 1 is greater "
                            f"than LastLedgerSequence 1 ({marker})")
        assert is_transient_ledger_error(exc), f"{marker} must be transient"


def test_permanent_error_is_not_retried():
    assert not is_transient_ledger_error(
        _PermanentErr("telINSUF_FEE_P: Insufficient fee"))
    assert not is_transient_ledger_error(
        _PermanentErr("temBAD_AMOUNT: Payment amount is invalid"))


def test_transient_detected_via_result_attribute():
    """Marker can land on exc.result rather than the message."""
    exc = _TransientErr("Transaction failed")
    exc.result = "tefPAST_SEQ"
    assert is_transient_ledger_error(exc)


def test_success_on_first_attempt_does_not_retry(monkeypatch):
    calls = []

    def fake(tx, client, wallet):
        calls.append(1)
        return _Resp({"hash": "ABC", "meta": {"TransactionResult": "tesSUCCESS"}})

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    sleeps = []
    result = submit_with_retry("tx", "client", "wallet", sleep=sleeps.append)

    assert result["hash"] == "ABC"
    assert len(calls) == 1, "a clean submission must not retry"
    assert sleeps == [], "a clean submission must not sleep"


def test_transient_then_success_retries_exactly_once(monkeypatch):
    calls = []

    def fake(tx, client, wallet):
        calls.append(1)
        if len(calls) == 1:
            raise _TransientErr("Prelim result: tefPAST_SEQ, LastLedgerSequence")
        return _Resp({"hash": "XYZ", "meta": {"TransactionResult": "tesSUCCESS"}})

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    sleeps = []
    result = submit_with_retry("tx", "client", "wallet", sleep=sleeps.append)

    assert result["hash"] == "XYZ"
    assert len(calls) == 2
    assert len(sleeps) == 1, "exactly one backoff before the retry"


def test_permanent_error_raises_immediately_without_retry(monkeypatch):
    calls = []

    def fake(tx, client, wallet):
        calls.append(1)
        raise _PermanentErr("telINSUF_FEE_P: Insufficient fee")

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    sleeps = []

    with pytest.raises(_PermanentErr):
        submit_with_retry("tx", "client", "wallet", sleep=sleeps.append)

    assert len(calls) == 1, "a permanent error must NOT be retried"
    assert sleeps == []


def test_exhausted_retries_reraise_real_cause(monkeypatch):
    calls = []

    def fake(tx, client, wallet):
        calls.append(1)
        raise _TransientErr("Prelim result: tefPAST_SEQ")

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    sleeps = []

    with pytest.raises(_TransientErr, match="tefPAST_SEQ"):
        submit_with_retry("tx", "client", "wallet", max_attempts=3,
                          sleep=sleeps.append)

    assert len(calls) == 3, "must attempt exactly max_attempts times"
    assert len(sleeps) == 2, "must not sleep after the final attempt"


def test_backoff_increases(monkeypatch):
    """The point of backoff is to let a ledger close before retrying."""
    def fake(tx, client, wallet):
        raise _TransientErr("tefPAST_SEQ")

    monkeypatch.setattr("xrpl.transaction.submit_and_wait", fake)
    sleeps = []
    with pytest.raises(_TransientErr):
        submit_with_retry("tx", "client", "wallet", max_attempts=4,
                          base_delay=2.0, sleep=sleeps.append)

    assert sleeps == sorted(sleeps), "backoff must be non-decreasing"
    assert len(set(sleeps)) > 1, "backoff must actually vary between attempts"


def test_retry_calls_submit_and_wait_which_autofills(monkeypatch):
    """THE load-bearing invariant.

    Retry is only meaningful because submit_and_wait autofills each call,
    giving the transaction a fresh LastLedgerSequence. Assert the real
    default is autofill=True -- if upstream ever changes it, retry silently
    becomes a loop resubmitting a dead window and this test fails loudly.
    """
    import inspect

    from xrpl.transaction import submit_and_wait

    sig = inspect.signature(submit_and_wait)
    assert "autofill" in sig.parameters, "submit_and_wait lost its autofill param"
    assert sig.parameters["autofill"].default is True, (
        "submit_and_wait no longer autofills by default; retry on "
        "tefPAST_SEQ would resubmit the same expired LastLedgerSequence "
        "forever. Rethink tx_submit.submit_with_retry."
    )


def test_library_sites_use_the_retry_helper():
    """Every submit call site must route through the retry helper.

    A single un-migrated site leaves a live flake behind, which is exactly
    the defect this change exists to remove.
    """
    import pathlib
    import re

    import xrpl_agent_id

    pkg = pathlib.Path(xrpl_agent_id.__file__).parent
    offenders = []
    for py in pkg.glob("*.py"):
        if py.name == "tx_submit.py":
            continue
        src = py.read_text()
        # a direct, un-retried library call
        if re.search(r"=\s*submit_and_wait\(", src):
            offenders.append(py.name)
    assert not offenders, (
        f"these modules still call submit_and_wait directly and will flake "
        f"on tefPAST_SEQ: {offenders}"
    )