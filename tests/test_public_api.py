# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Pin the top-level public API of xrpl_agent_id.

Every name in ``REQUIRED_PUBLIC_API`` must be importable from
``xrpl_agent_id``. If you remove or rename one of these names, this test
fails — that is the point.

Also asserts ``XRPL_AGENT_ID_VERSION`` matches ``__version__`` so they can
never drift apart.
"""

from __future__ import annotations

import pytest


# Contract — every name here MUST be reachable as xrpl_agent_id.<NAME>.
# Mirrors xrpl_agent_id/PUBLIC_API.md.
REQUIRED_PUBLIC_API = [
    # Core types
    "AgentIdentity",
    "Authority",
    "Credential",
    "CredentialType",
    "DIDDocument",
    "NetworkEndpoint",
    "VerificationResult",
    # DID helpers
    "NETWORK_IDS",
    "NETWORKS",
    "did_from_account",
    "parse_did",
    "resolve_did",
    # Network helpers
    "get_client",
    "get_network",
    # Trust library
    "TrustCheckResult",
    "TrustPolicy",
    "TrustRegistry",
    # Version
    "XRPL_AGENT_ID_VERSION",
    "XRPL_AGENT_ID_VERSION_INFO",
]


def test_all_required_names_importable() -> None:
    import xrpl_agent_id

    missing = [name for name in REQUIRED_PUBLIC_API if not hasattr(xrpl_agent_id, name)]
    assert not missing, (
        f"xrpl_agent_id is missing required public-API names: {missing}. "
        "If you intentionally removed one, update PUBLIC_API.md and bump "
        "the major version."
    )


def test_all_listed_in_all() -> None:
    """Every name in __all__ must be reachable on the module."""
    import xrpl_agent_id

    for name in xrpl_agent_id.__all__:
        assert hasattr(xrpl_agent_id, name), f"__all__ lists {name!r} but it's missing"


def test_version_aliases_in_sync() -> None:
    """__version__ and XRPL_AGENT_ID_VERSION must agree."""
    import xrpl_agent_id

    assert xrpl_agent_id.__version__ == xrpl_agent_id.XRPL_AGENT_ID_VERSION, (
        f"version drift: __version__={xrpl_agent_id.__version__!r} "
        f"!= XRPL_AGENT_ID_VERSION={xrpl_agent_id.XRPL_AGENT_ID_VERSION!r}"
    )


def test_version_info_tuple() -> None:
    """XRPL_AGENT_ID_VERSION_INFO is (major, minor, patch) and matches the string."""
    import xrpl_agent_id

    info = xrpl_agent_id.XRPL_AGENT_ID_VERSION_INFO
    assert isinstance(info, tuple)
    assert len(info) == 3
    assert all(isinstance(x, int) for x in info)

    major, minor, patch = info
    expected_prefix = f"{major}.{minor}.{patch}"
    assert xrpl_agent_id.__version__.startswith(expected_prefix), (
        f"version_info {info} doesn't match __version__ {xrpl_agent_id.__version__!r}"
    )


@pytest.mark.parametrize("name", REQUIRED_PUBLIC_API)
def test_each_required_name_resolves(name: str) -> None:
    import xrpl_agent_id

    obj = getattr(xrpl_agent_id, name)
    assert obj is not None
