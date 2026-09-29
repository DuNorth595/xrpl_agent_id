# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Hermetic tests for scripts/stress_harness_throughput.py."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))

from scripts.stress_harness_throughput import (  # noqa: E402
    build_memo_payload,
    p,
    submit_one,
)


def test_build_memo_payload_determinism():
    """Same (tx_index, run_nonce) must produce same decision_id."""
    a = build_memo_payload(7, "nonce123")
    b = build_memo_payload(7, "nonce123")
    # ts is wall-clock and will differ; everything else must match.
    a.pop("ts", None)
    b.pop("ts", None)
    assert a == b
    assert a["app"] == "xrpl_agent_id_audit"
    assert a["v"] == 1
    assert a["decision_id"] == hashlib.sha256(b"throughput:nonce123:7").hexdigest().upper()
    assert a["agent"] == "did:xrpl:2:rTHROUGHPUT00000007"


def test_build_memo_payload_distinct_indices():
    a = build_memo_payload(0, "nonce")
    b = build_memo_payload(1, "nonce")
    assert a["decision_id"] != b["decision_id"]
    assert a["agent"] != b["agent"]


def test_build_memo_payload_allow_distribution():
    """tx_index % 7 == 0 is the allow path."""
    allowed = [i for i in range(14) if build_memo_payload(i, "n")["allow"]]
    assert allowed == [0, 7]


def test_p_empty():
    assert p([], 0.5) == 0.0


def test_p_single():
    assert p([1.0], 0.5) == 1.0


def test_p_50():
    """p50 of [1..100] is 50 (the 50th value at index 49 of 99)."""
    vals = list(range(1, 101))
    assert p(vals, 0.50) == 50


def test_p_95():
    vals = list(range(1, 101))
    # idx = int(0.95 * 99) = 94 → vals[94] = 95
    assert p(vals, 0.95) == 95


def test_submit_one_error_classification(monkeypatch):
    """submit_one must classify 'tefPAST_SEQ' into error_class without raising."""
    class FakeSubmitError(Exception):
        pass

    def boom(*_args, **_kwargs):
        raise FakeSubmitError("XRPL tefPAST_SEQ for tx (LastLedgerSequence)")

    # Patch the symbol where submit_one imports it from (it does `from xrpl.transaction import submit_and_wait`).
    import scripts.stress_harness_throughput as sht
    monkeypatch.setattr(sht, "submit_and_wait", boom)

    # Use a real valid-format seed so Wallet.from_seed doesn't fail before reaching our boom.
    valid_seed = "sEdTvzEV1emGqQykRv4M8K4d1J6sNAm"
    # Must differ from the wallet's own address (XRPL rejects self-payment).
    valid_addr = "rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH"

    r = submit_one(valid_seed, valid_addr, 0, "nonce")
    assert r["ok"] is False
    assert r["error_class"] == "tefPAST_SEQ"
    assert r["index"] == 0
    assert "tefPAST_SEQ" in r["error"]
