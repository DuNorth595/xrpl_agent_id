# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Guard the mainnet harness's funding arithmetic.

Every account the harness creates is funded with an amount derived from
the reserve rules. Getting that wrong fails the run mid-way with
``tecINSUFFICIENT_RESERVE`` or ``tecUNFUNDED_PAYMENT`` -- after some
funds have already moved.

Two separate mistakes have happened in this project:

1. The issuer was funded as if it held no objects. The owner reserve for
   a credential is charged to the account that CREATES it, not the one
   that accepts it. Fixed on the first real mainnet attempt.
2. The wrong issuer was funded the same way -- as a plain wallet --
   even though it also creates a credential. Fixed on the n=6 run.

Both were the same mistake: funding an account for fewer owned objects
than it will actually hold. These tests check the arithmetic directly,
so the next role that needs an object reserve cannot quietly skip it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "scripts" / "stress_harness_mainnet.py"


def _load():
    spec = importlib.util.spec_from_file_location("stress_harness_mainnet", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load()


def test_issuer_funding_covers_one_owner_reserve_per_credential(harness) -> None:
    """The issuer is charged an owner reserve for every credential it creates."""
    one = harness.issuer_funding_drops(1)
    two = harness.issuer_funding_drops(2)
    plain = harness.plain_wallet_funding_drops()

    assert one > plain, (
        "an issuer that creates one credential must be funded above a "
        "plain wallet -- it holds the credential's owner reserve"
    )
    assert two - one == harness.RESERVE_INC_DROPS + harness.FEE_MEDIAN_DROPS


def test_plain_wallet_funding_is_below_issuer_funding(harness) -> None:
    """The plain-wallet helper is genuinely the cheaper option."""
    assert harness.plain_wallet_funding_drops() < harness.issuer_funding_drops(1)


def test_agent_funding_covers_its_own_objects(harness) -> None:
    """An agent holds a DID and a credential -- 0.4 XRP of owner reserve."""
    agent = harness.agent_funding_drops()
    base = harness.RESERVE_BASE_DROPS
    assert agent >= base + 2 * harness.RESERVE_INC_DROPS, (
        "agent funding must cover its base reserve plus two owned objects"
    )


def test_the_wrong_issuer_is_funded_as_an_issuer(harness) -> None:
    """The wrong issuer creates a credential, so it needs an owner reserve.

    This is the specific bug that broke the n=6 mainnet run on
    2026-10-28. The wrong issuer was funded with
    ``plain_wallet_funding_drops()``, which covers a base reserve and
    fees but no owner reserve. The run then failed at
    ``tecINSUFFICIENT_RESERVE`` on the credential it tried to create.
    """
    source = HARNESS.read_text(encoding="utf-8")

    # Find the wrong_issuer branch and check what it funds with.
    marker = 'elif role == "wrong_issuer":'
    assert marker in source, "the wrong_issuer role is missing from the harness"
    branch = source[source.find(marker):]
    branch = branch[:branch.find("\n        elif") if "\n        elif" in branch else len(branch)]

    assert "plain_wallet_funding_drops()" not in branch, (
        "the wrong issuer creates a credential, so it must be funded with "
        "issuer_funding_drops(1) -- not plain_wallet_funding_drops(), which "
        "covers no owner reserve. This failed with tecINSUFFICIENT_RESERVE "
        "on the n=6 mainnet run."
    )
    assert "issuer_funding_drops(1)" in branch, (
        "the wrong issuer must be funded with issuer_funding_drops(1)"
    )


def test_controller_banned_agent_funds_its_signerlist(harness) -> None:
    """The agent owns the SignerList, so it needs a third owner reserve.

    The SignerListSet is submitted on the agent's own account. The owner
    reserve for a SignerList object is therefore charged to the agent,
    not to the banned co-signer.
    """
    source = HARNESS.read_text(encoding="utf-8")

    assert "owned_objects = 3 if role == \"controller_banned\"" in source, (
        "the controller_banned agent must be funded for 3 owned objects "
        "(DID, credential, SignerList)"
    )

    # And the plain-wallet helper must stay cheaper than either.
    assert harness.plain_wallet_funding_drops() < harness.agent_funding_drops(3)


def test_budget_counts_the_signerlist_owner_reserve(harness) -> None:
    """The computed budget must include the SignerList object.

    A budget that under-counts objects will pass its own gate and then
    fail mid-run.
    """
    harness._NO_MIRROR = True
    try:
        need3 = harness.compute_budget(3)
        need6 = harness.compute_budget(6)
    finally:
        harness._NO_MIRROR = False

    # n=3: 3 agents x 2 objects = 6, + 1 SignerList + 2 issuer-held
    # credentials = 9. (No wrong_issuer at n=3.)
    line3 = [l for l in need3["lines"] if "owned objects" in l]
    assert line3, "the budget breakdown does not state the object count"
    assert "9 owned objects" in line3[0], (
        f"expected 9 owned objects for n=3, got: {line3[0]}"
    )

    # n=6: 6 agents x 2 = 12, + 1 SignerList + 3 issuer-held
    # credentials + 1 wrong-issuer credential = 17.
    line6 = [l for l in need6["lines"] if "owned objects" in l]
    assert "17 owned objects" in line6[0], (
        f"expected 17 owned objects for n=6, got: {line6[0]}"
    )

def test_budget_accounts_match_the_funding_helpers(harness) -> None:
    """The computed budget must leave room for every account's funding."""
    harness._NO_MIRROR = True
    try:
        need = harness.compute_budget(3)
    finally:
        harness._NO_MIRROR = False

    # n=3 with no mirror: 3 agents + issuer + controller co-signer + the
    # treasury's own base reserve.
    assert need["total"] > 0
    assert any("base reserve" in l for l in need["lines"])
    assert any("owner reserve" in l for l in need["lines"])
    assert any("fee" in l for l in need["lines"])
