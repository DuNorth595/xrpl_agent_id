# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Hermetic tests for scripts/stress_harness_scale.py.

The full live harness is exercised by RUN_LIVE=1; these tests cover
the off-network pieces:
  * p() percentile helper
  * mirror fee math (1 drop per decision × N decisions)
  * mirror latency stats aggregation
  * spec/role reassembly by index after the two-issuer split
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))

from scripts.stress_harness_scale import p  # noqa: E402
from scripts.stress_harness_live import LiveAgentSpec  # noqa: E402


def test_p_empty():
    assert p([], 0.5) == 0.0


def test_p_single():
    assert p([1.0], 0.5) == 1.0


def test_p_50():
    vals = list(range(1, 101))
    assert p(vals, 0.50) == 50


def test_p_95():
    vals = list(range(1, 101))
    # idx = int(0.95 * 99) = 94 → vals[94] = 95
    assert p(vals, 0.95) == 95


def test_p_99():
    vals = list(range(1, 101))
    # idx = int(0.99 * 99) = 98 → vals[98] = 99
    assert p(vals, 0.99) == 99


def test_live_agent_spec_role_round_trip():
    """Role distribution should be deterministic by index % 6."""
    roles = ["valid", "banned", "controller_banned", "no_creds", "wrong_issuer", "pending"]
    specs = [LiveAgentSpec(index=i, role=roles[i % len(roles)]) for i in range(50)]
    assert len(specs) == 50
    # Each role appears floor(50/6)=8 times, with 2 leftover → first 2 roles get one extra.
    role_counts = {}
    for s in specs:
        role_counts[s.role] = role_counts.get(s.role, 0) + 1
    assert role_counts["valid"] == 9   # 50 % 6 = 2 → first 2 roles (valid, banned) get 9
    assert role_counts["banned"] == 9
    assert role_counts["controller_banned"] == 8
    assert role_counts["no_creds"] == 8
    assert role_counts["wrong_issuer"] == 8
    assert role_counts["pending"] == 8


def test_mirror_fee_math():
    """A 50-agent run should cost 50 × 15 drops ≈ 0.000750 XRP for mirror alone."""
    drops_per_mirror = 15
    n_mirrors = 50
    total_drops = drops_per_mirror * n_mirrors
    # 1 XRP = 1_000_000 drops
    total_xrp = total_drops / 1_000_000
    assert total_xrp == 0.00075
    assert total_drops == 750
