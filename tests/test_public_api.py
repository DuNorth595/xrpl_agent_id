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
    # Wallet providers (v0.5.0)
    "ExternalSignerProvider",
    "SeedWalletProvider",
    "WalletProvider",
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
    expected = f"{major}.{minor}.{patch}"
    # Exact match, not a prefix. A prefix check would let (0, 4, 0) pass
    # against "0.5.0" -- which is exactly the drift this test exists to
    # catch. A pre-release suffix ("0.5.0rc1") is still allowed.
    assert xrpl_agent_id.__version__ == expected or (
        xrpl_agent_id.__version__.startswith(expected)
        and not xrpl_agent_id.__version__[len(expected)].isdigit()
    ), (
        f"version_info {info} doesn't match __version__ {xrpl_agent_id.__version__!r}"
    )


@pytest.mark.parametrize("name", REQUIRED_PUBLIC_API)
def test_each_required_name_resolves(name: str) -> None:
    import xrpl_agent_id

    obj = getattr(xrpl_agent_id, name)
    assert obj is not None


# --- Document / code synchronisation -------------------------------------
#
# PUBLIC_API.md is the contract. The test above pins the *code* against a
# hand-maintained list; this pins the *document* against the code. Without
# it, the document can drift -- and it did: the "What is NOT in the public
# API" section listed six names that do not exist in the package at all,
# and omitted six that do. A contract that lies is worse than no contract.

import re
from pathlib import Path

_DOC = Path(__file__).resolve().parent.parent / "xrpl_agent_id" / "PUBLIC_API.md"

# The part of the document that declares the contract. Everything after
# this heading is commentary about what is NOT public.
_CONTRACT_END = "## Submodule access"


def _documented_names() -> set[str]:
    text = _DOC.read_text(encoding="utf-8")
    contract = text.split(_CONTRACT_END)[0]
    # Table rows: | `Name` | ... |   and   | `name(args)` | ... |
    found = set(re.findall(r"\|\s*`([A-Za-z_][A-Za-z0-9_]*)", contract))
    # Module-level constants are written as bare UPPER_CASE in table cells.
    found |= set(re.findall(r"\|\s*`([A-Z_]{3,})`", contract))
    return found


def test_public_api_doc_exists() -> None:
    assert _DOC.is_file(), f"missing API contract document: {_DOC}"


def test_every_exported_name_is_documented() -> None:
    """Every name in ``__all__`` must appear in the contract section."""
    import xrpl_agent_id

    documented = _documented_names()
    undocumented = sorted(set(xrpl_agent_id.__all__) - documented)
    assert not undocumented, (
        f"PUBLIC_API.md does not document these exported names: {undocumented}. "
        "Add them to the contract tables, or remove them from __all__."
    )


def test_documented_names_all_exist() -> None:
    """Every name the contract claims must be a real export.

    This is the check that was missing. The document previously listed
    ``Publisher``, ``AcceptancePolicy``, ``LedgerAuditLog``, ``Journal``,
    ``AgentRecord``, and ``Registry`` as internal-only -- none of them
    exist in the package. A reader who went looking for them would find
    nothing.
    """
    import xrpl_agent_id

    documented = _documented_names()
    # Names that appear in the contract prose rather than as API names.
    non_api = {"xrpl_agent_id", "merkle_root", "submit_with_retry", "AuditLog"}
    claimed = documented - non_api
    missing = sorted(name for name in claimed if not hasattr(xrpl_agent_id, name))
    assert not missing, (
        f"PUBLIC_API.md claims these names are public, but they are not "
        f"importable from xrpl_agent_id: {missing}"
    )


def test_nonexistent_names_are_not_claimed_anywhere_in_the_doc() -> None:
    """The document must not name symbols that do not exist.

    Guards against a repeat of the specific defect this file was written
    to catch: invented symbol names in the API contract.
    """
    import importlib
    import xrpl_agent_id

    text = _DOC.read_text(encoding="utf-8")
    # Names the document asserts do not exist.
    asserted_absent = re.search(
        r"do not exist in the\s*\n?\s*package at all\*\*:?\s*(.+?)\n\n",
        text,
        re.S,
    )
    assert asserted_absent, "the 'does not exist' list is missing from PUBLIC_API.md"

    # The list may wrap across several lines, so keep scanning until the
    # paragraph ends -- not just the first line.
    absent_list = asserted_absent.group(1)
    for continuation in re.findall(r"\n`([A-Za-z_][A-Za-z0-9_]*)`", absent_list):
        absent_list += " " + continuation

    for name in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", absent_list):
        modules = [
            "xrpl_agent_id",
            "xrpl_agent_id.authorization",
            "xrpl_agent_id.audit",
            "xrpl_agent_id.banned",
            "xrpl_agent_id.identity",
            "xrpl_agent_id.trust",
            "xrpl_agent_id.wallet",
            "xrpl_agent_id.network",
            "xrpl_agent_id.credential",
            "xrpl_agent_id.did",
            "xrpl_agent_id.tx_submit",
        ]
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert not hasattr(mod, name), (
                f"PUBLIC_API.md says {name!r} does not exist, but it is "
                f"importable from {mod_name}"
            )
