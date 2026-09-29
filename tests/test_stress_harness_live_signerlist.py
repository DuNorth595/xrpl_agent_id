# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the live harness's SignerListSet path.

We mock xrpl.transaction.submit_and_wait so the test never hits the
testnet. The point of these tests is to prove:
  1. The two shapes ('compromised', 'quarantined') produce the expected
     SignerListSet payload (right accounts, weights, quorum).
  2. A failed SignerListSet (non-tesSUCCESS) raises.
  3. setup_live_agents correctly wires SignerListSet into the
     'controller_banned' role when the spec says so.

These tests intentionally do NOT touch real ledger state — they only
exercise the harness's transaction-construction and error-handling logic.

Note on shape design: XRPL forbids the master account from appearing in
its own SignerList, so neither shape can include the master. That means
'compromised' (banned sole signer) and 'quarantined' (banned + sentinel,
quorum 2) are the only two valid real-world configurations for
exercising CONTROLLER_BANNED detection.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import stress_harness_live  # noqa: E402


# Two real, distinct testnet-class r-addresses (format-valid only — they
# never get funded). The 'master' address MUST match the seed we generate
# so _setup_controller_banned_onchain's seed/address sanity check passes.
MASTER_ADDR = "rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH"
BANNED_ADDR = "rJYM462TDsZ4JpbR9BvWqTfY3vB6KzvLcw"
SENTINEL_ADDR = "rJYH5sTx7nR8qUDcYo2qVwbHJz2wGihNDF"


def _fake_wallet_from_seed(seed: str):
    """Return a SimpleNamespace that quacks like xrpl.wallet.Wallet."""
    return SimpleNamespace(address=MASTER_ADDR, seed=seed)


def _fake_submit_and_wait_ok(tx, client, wallet):
    """A submit_and_wait stand-in that always succeeds."""
    return SimpleNamespace(
        result={
            "hash": "0x" + "a" * 64,
            "meta": {"TransactionResult": "tesSUCCESS"},
        }
    )


def _fake_submit_and_wait_bad(tx, client, wallet):
    """A submit_and_wait stand-in that always fails."""
    return SimpleNamespace(
        result={
            "hash": "0x" + "b" * 64,
            "meta": {"TransactionResult": "temBAD_SIGNER"},
        }
    )


class TestSetupControllerBannedOnchain:
    def test_compromised_shape_uses_only_banned_addr_quorum_1(self):
        captured: list[dict] = []

        def capture(tx, client, wallet):
            captured.append(tx.to_dict())
            return _fake_submit_and_wait_ok(tx, client, wallet)

        with patch("stress_harness_live.submit_and_wait", side_effect=capture), \
             patch("stress_harness_live.Wallet.from_seed",
                   side_effect=lambda seed: _fake_wallet_from_seed(seed)):
            tx_hash = stress_harness_live._setup_controller_banned_onchain(
                agent_seed="any",
                agent_address=MASTER_ADDR,
                banned_address=BANNED_ADDR,
                sentinel_address=None,
                shape="compromised",
            )

        assert tx_hash == "0x" + "a" * 64
        sl = captured[0]
        assert sl["transaction_type"] == "SignerListSet"
        assert sl["account"] == MASTER_ADDR
        assert sl["signer_quorum"] == 1
        entries = sl["signer_entries"]
        assert len(entries) == 1
        assert entries[0]["signer_entry"]["account"] == BANNED_ADDR
        assert entries[0]["signer_entry"]["signer_weight"] == 1
        # Master MUST NOT appear in its own signer list (XRPL invariant).
        assert MASTER_ADDR not in {e["signer_entry"]["account"] for e in entries}

    def test_quarantined_shape_uses_banned_and_sentinel_quorum_2(self):
        captured: list[dict] = []

        def capture(tx, client, wallet):
            captured.append(tx.to_dict())
            return _fake_submit_and_wait_ok(tx, client, wallet)

        with patch("stress_harness_live.submit_and_wait", side_effect=capture), \
             patch("stress_harness_live.Wallet.from_seed",
                   side_effect=lambda seed: _fake_wallet_from_seed(seed)):
            stress_harness_live._setup_controller_banned_onchain(
                agent_seed="any",
                agent_address=MASTER_ADDR,
                banned_address=BANNED_ADDR,
                sentinel_address=SENTINEL_ADDR,
                shape="quarantined",
            )

        sl = captured[0]
        assert sl["signer_quorum"] == 2
        entries = sl["signer_entries"]
        assert len(entries) == 2
        addrs = {e["signer_entry"]["account"] for e in entries}
        assert addrs == {BANNED_ADDR, SENTINEL_ADDR}
        # Master again MUST NOT appear.
        assert MASTER_ADDR not in addrs

    def test_invalid_shape_raises(self):
        with patch(
            "stress_harness_live.Wallet.from_seed",
            side_effect=lambda seed: _fake_wallet_from_seed(seed),
        ):
            with pytest.raises(ValueError, match="unknown controller_banned_shape"):
                stress_harness_live._setup_controller_banned_onchain(
                    agent_seed="any",
                    agent_address=MASTER_ADDR,
                    banned_address=BANNED_ADDR,
                    sentinel_address=None,
                    shape="tri-signer",  # not supported
                )

    def test_quarantined_without_sentinel_raises(self):
        with patch(
            "stress_harness_live.Wallet.from_seed",
            side_effect=lambda seed: _fake_wallet_from_seed(seed),
        ):
            with pytest.raises(ValueError, match="requires a sentinel_address"):
                stress_harness_live._setup_controller_banned_onchain(
                    agent_seed="any",
                    agent_address=MASTER_ADDR,
                    banned_address=BANNED_ADDR,
                    sentinel_address=None,
                    shape="quarantined",
                )

    def test_seed_address_mismatch_raises(self):
        with patch(
            "stress_harness_live.Wallet.from_seed",
            side_effect=lambda seed: _fake_wallet_from_seed(seed),
        ):
            with pytest.raises(RuntimeError, match="seed/address mismatch"):
                stress_harness_live._setup_controller_banned_onchain(
                    agent_seed="any",
                    agent_address="rDifferentAddressFromSeed123456789",
                    banned_address=BANNED_ADDR,
                    sentinel_address=None,
                    shape="compromised",
                )

    def test_non_tes_success_raises(self):
        with patch(
            "stress_harness_live.submit_and_wait",
            side_effect=lambda tx, c, w: _fake_submit_and_wait_bad(tx, c, w),
        ), patch(
            "stress_harness_live.Wallet.from_seed",
            side_effect=lambda seed: _fake_wallet_from_seed(seed),
        ):
            with pytest.raises(RuntimeError, match="did not tesSUCCESS"):
                stress_harness_live._setup_controller_banned_onchain(
                    agent_seed="any",
                    agent_address=MASTER_ADDR,
                    banned_address=BANNED_ADDR,
                    sentinel_address=None,
                    shape="compromised",
                )


class TestSetupLiveAgentsWiring:
    """End-to-end through setup_live_agents for a single controller_banned
    spec. We mock every external call so the test is hermetic.
    """

    def test_controller_banned_publishes_compromised_signerlist(self, monkeypatch):
        monkeypatch.setattr(stress_harness_live, "get_client", lambda n: None)
        captured: list[dict] = []
        signerlist_returns = ["0x" + "d" * 64]  # tx hash to return

        def fake_setup(**kwargs):
            captured.append(kwargs)
            return signerlist_returns.pop(0)

        # _wallet_from_faucet called in this order: agent first, THEN banned
        # co-signer. (Sentinel not needed for 'compromised' shape.)
        seq = iter([
            ("agent_seed", MASTER_ADDR),
            ("banned_seed", BANNED_ADDR),
        ])

        monkeypatch.setattr(
            stress_harness_live, "_wallet_from_faucet", lambda: next(seq)
        )
        monkeypatch.setattr(
            stress_harness_live, "_setup_controller_banned_onchain", fake_setup
        )
        monkeypatch.setattr(
            stress_harness_live,
            "_issue_and_accept_credential",
            lambda i, s, credential_type=b"agent_identity_v1": (None, "0x" + "e" * 64),
        )

        spec = stress_harness_live.LiveAgentSpec(index=0, role="controller_banned")
        stress_harness_live.setup_live_agents([spec], issuer_seed="issuer")

        assert spec.address == MASTER_ADDR
        assert spec.controller_banned_address == BANNED_ADDR
        assert spec.controller_banned_signerlist_tx == "0x" + "d" * 64
        assert spec.controller_banned_sentinel_address == ""  # not used
        assert len(captured) == 1
        kwargs = captured[0]
        assert kwargs["agent_address"] == MASTER_ADDR
        assert kwargs["banned_address"] == BANNED_ADDR
        assert kwargs["shape"] == "compromised"  # default
        assert kwargs["sentinel_address"] is None

    def test_controller_banned_quarantined_funds_sentinel(self, monkeypatch):
        monkeypatch.setattr(stress_harness_live, "get_client", lambda n: None)

        captured_kwargs: list[dict] = []

        def fake_setup(**kwargs):
            captured_kwargs.append(kwargs)
            return "0x" + "f" * 64

        # agent + banned + sentinel = 3 faucet calls
        seq = iter([
            ("agent_seed", MASTER_ADDR),
            ("banned_seed", BANNED_ADDR),
            ("sentinel_seed", SENTINEL_ADDR),
        ])

        monkeypatch.setattr(
            stress_harness_live, "_wallet_from_faucet", lambda: next(seq)
        )
        monkeypatch.setattr(
            stress_harness_live, "_setup_controller_banned_onchain", fake_setup
        )
        monkeypatch.setattr(
            stress_harness_live,
            "_issue_and_accept_credential",
            lambda i, s, credential_type=b"agent_identity_v1": (None, "0x"),
        )

        spec = stress_harness_live.LiveAgentSpec(
            index=0,
            role="controller_banned",
            controller_banned_shape="quarantined",
        )
        stress_harness_live.setup_live_agents([spec], issuer_seed="issuer")

        assert captured_kwargs[0]["shape"] == "quarantined"
        assert captured_kwargs[0]["sentinel_address"] == SENTINEL_ADDR
        assert captured_kwargs[0]["banned_address"] == BANNED_ADDR
        assert spec.controller_banned_signerlist_tx == "0x" + "f" * 64
        assert spec.controller_banned_sentinel_address == SENTINEL_ADDR
        assert spec.controller_banned_sentinel_seed == "sentinel_seed"

    def test_signerlist_failure_degrades_to_no_creds(self, monkeypatch, capsys):
        monkeypatch.setattr(stress_harness_live, "get_client", lambda n: None)
        # Order: agent first, then banned co-signer.
        seq = iter([
            ("agent_seed", MASTER_ADDR),
            ("banned_seed", BANNED_ADDR),
        ])
        monkeypatch.setattr(
            stress_harness_live, "_wallet_from_faucet", lambda: next(seq)
        )

        def boom(**kwargs):
            raise RuntimeError("SignerListSet did not tesSUCCESS")

        monkeypatch.setattr(
            stress_harness_live, "_setup_controller_banned_onchain", boom
        )
        monkeypatch.setattr(
            stress_harness_live,
            "_issue_and_accept_credential",
            lambda i, s, credential_type=b"agent_identity_v1": (None, "0x"),
        )

        spec = stress_harness_live.LiveAgentSpec(index=0, role="controller_banned")
        stress_harness_live.setup_live_agents([spec], issuer_seed="issuer")

        # Role should degrade gracefully — agent ends up as no_creds with
        # the original role recorded for the audit log.
        assert spec.role == "no_creds"
        assert spec.degraded_from == "controller_banned"
        assert "SignerListSet did not tesSUCCESS" in spec.degraded_reason
        out = capsys.readouterr().out
        assert "DEGRADED to no_creds" in out
