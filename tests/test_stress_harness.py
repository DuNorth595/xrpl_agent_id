# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the sim-mode stress harness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import stress_harness  # noqa: E402


@pytest.fixture
def cleanup_results():
    """Clean up harness-created artifacts after each test."""
    yield
    for f in (PROJECT_ROOT / "results").glob("stress_summary_sim_*.json"):
        f.unlink(missing_ok=True)
    for f in (PROJECT_ROOT / "results").glob("audit_sim_*.db"):
        f.unlink(missing_ok=True)
    f = PROJECT_ROOT / "results" / "stress_bans.json"
    if f.exists():
        f.unlink()


# ---------------------------------------------------------------------------
# Phantom construction
# ---------------------------------------------------------------------------

class TestPhantomAgents:
    def test_each_factory_constructs(self):
        for role, factory in stress_harness.PHANTOM_FACTORIES.items():
            p = factory(0)
            assert p.address.startswith("r"), f"{role} should have r-address"
            assert p.did.startswith("did:xrpl:2:")
            assert p.role == role

    def test_valid_has_accepted_kyc(self):
        p = stress_harness._phantom_valid(0)
        assert len(p.credentials) == 1
        c = p.credentials[0]
        assert c.credential_type == b"KYC"
        assert c.accepted is True

    def test_pending_has_unaccepted_kyc(self):
        p = stress_harness._phantom_pending(0)
        assert len(p.credentials) == 1
        assert p.credentials[0].accepted is False

    def test_banned_has_no_creds(self):
        p = stress_harness._phantom_banned(0)
        assert p.credentials == []

    def test_controller_banned_has_extra_controller(self):
        p = stress_harness._phantom_controller_banned(0)
        assert len(p.controllers) == 2
        assert p.address in p.controllers
        assert any("BADC0NTRL" in c for c in p.controllers)

    def test_phantom_to_record(self):
        p = stress_harness._phantom_valid(0)
        rec = stress_harness._phantom_to_record(p)
        assert rec.agent_address == p.address
        assert rec.agent_did == p.did
        assert rec.credentials == p.credentials
        assert rec.controllers == p.controllers
        assert "0/1" in rec.summary() or "1/1" in rec.summary()


# ---------------------------------------------------------------------------
# Sim run
# ---------------------------------------------------------------------------

class TestRunSim:
    def test_run_sim_produces_summary(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        assert summary["mode"] == "sim"
        assert summary["n_phantoms"] == 12
        assert "by_role" in summary
        assert "audit_db" in summary

    def test_run_sim_covers_all_roles(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        # 12 / 6 roles = 2 per role
        for role in stress_harness.PHANTOM_FACTORIES:
            assert role in summary["by_role"], f"missing role: {role}"
            assert summary["by_role"][role]["n"] == 2

    def test_run_sim_valid_role_allows(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        valid = summary["by_role"]["valid"]
        assert valid["allowed"] == valid["n"]
        assert valid["denied"] == 0
        assert "OK" in valid["reason_counts"]

    def test_run_sim_banned_denies(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        banned = summary["by_role"]["banned"]
        assert banned["allowed"] == 0
        assert banned["denied"] == banned["n"]
        assert "AGENT_BANNED" in banned["reason_counts"]

    def test_run_sim_controller_banned_denies(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        ctrl_banned = summary["by_role"]["controller_banned"]
        assert ctrl_banned["allowed"] == 0
        assert "CONTROLLER_BANNED" in ctrl_banned["reason_counts"]

    def test_run_sim_no_creds_denies(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        no_creds = summary["by_role"]["no_creds"]
        assert no_creds["allowed"] == 0
        assert "NO_CREDENTIALS" in no_creds["reason_counts"]

    def test_run_sim_wrong_issuer_denies(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        wrong = summary["by_role"]["wrong_issuer"]
        assert wrong["allowed"] == 0
        assert "CREDENTIAL_MISSING" in wrong["reason_counts"]

    def test_run_sim_pending_denies(self, cleanup_results):
        summary = stress_harness.run_sim(n=12, seed=0)
        pending = summary["by_role"]["pending"]
        assert pending["allowed"] == 0
        assert "CREDENTIAL_REVOKED" in pending["reason_counts"]

    def test_run_sim_audit_db_written(self, cleanup_results):
        summary = stress_harness.run_sim(n=6, seed=0)
        audit_path = Path(summary["audit_db"])
        assert audit_path.exists()
        assert audit_path.stat().st_size > 0

    def test_run_sim_writes_summary_file(self, cleanup_results, tmp_path):
        out = tmp_path / "summary.json"
        summary = stress_harness.run_sim(n=6, seed=0)
        out.write_text(json.dumps(summary, indent=2))
        loaded = json.loads(out.read_text())
        assert loaded["n_phantoms"] == 6


# ---------------------------------------------------------------------------
# Larger runs / scaling
# ---------------------------------------------------------------------------

class TestScaling:
    def test_run_sim_60_phantoms(self, cleanup_results):
        summary = stress_harness.run_sim(n=60, seed=42)
        assert summary["n_phantoms"] == 60
        # 60 / 6 = 10 per role
        for role, stats in summary["by_role"].items():
            assert stats["n"] == 10
        # All counts add up to total
        total = sum(s["n"] for s in summary["by_role"].values())
        assert total == 60
        # All valid still allow
        assert summary["by_role"]["valid"]["allowed"] == 10
        # Everything else denies
        for role, stats in summary["by_role"].items():
            if role == "valid":
                continue
            assert stats["denied"] == stats["n"], f"{role} should fully deny"
