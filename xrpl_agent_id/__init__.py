# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id — Identity for AI agents on the XRP Ledger.

Public surface is intentionally small. Detailed implementation lives in submodules.

Core types:
    AgentIdentity — a single agent's XRPL identity (DID + keys + credentials)
    Authority     — an issuer-side identity that can only sign CredentialCreate
    Credential    — an attestation issued by one agent to another
    DIDDocument   — W3C DID Document for an XRPL account
"""

from xrpl_agent_id.identity import AgentIdentity
from xrpl_agent_id.authority import Authority, VerificationResult
from xrpl_agent_id.credential import Credential, CredentialType
from xrpl_agent_id.did import (
    DIDDocument,
    NETWORK_IDS,
    did_from_account,
    parse_did,
    resolve_did,
)
from xrpl_agent_id.network import (
    NETWORKS,
    NetworkEndpoint,
    get_client,
    get_network,
)
from xrpl_agent_id.trust import (
    TrustCheckResult,
    TrustPolicy,
    TrustRegistry,
)
from xrpl_agent_id.wallet import (
    ExternalSignerProvider,
    SeedWalletProvider,
    WalletProvider,
)
from xrpl_agent_id.tx_submit import (
    FeeCeilingExceeded,
    max_fee_drops_for_usd,
)

__version__ = "0.5.2"
XRPL_AGENT_ID_VERSION = __version__  # canonical alias — referenced in docs/tests

# The source literal above is authoritative. It is the version of the code
# that is actually executing.
#
# An earlier version preferred importlib.metadata and overwrote __version__
# with it, on the theory that metadata "cannot drift out of sync with
# pyproject.toml". It does the opposite: with an editable install, the
# installed metadata is stale the moment you bump the source and before you
# reinstall, so __version__ silently reported the OLD version for the NEW
# code. Verified: with the source at 0.4.3 and installed metadata still
# 0.4.2, `xrpl_agent_id --version` printed 0.4.2 while running 0.4.3 --
# a drift report that looks self-consistent, which is the worst direction
# for it to be wrong in.
#
# So: report the source, and make drift LOUD instead of silent.
try:
    from importlib.metadata import version as _pkg_version

    _installed_version = _pkg_version("xrpl_agent_id")
    if _installed_version and _installed_version != __version__:
        import warnings as _warnings

        _warnings.warn(
            f"xrpl_agent_id source version is {__version__!r} but installed "
            f"package metadata says {_installed_version!r}. Reporting "
            f"{__version__!r} because that is the code actually running. "
            f"Reinstall to sync (pip install -e .).",
            RuntimeWarning,
            stacklevel=2,
        )
except Exception:
    pass

# Version tuple for libraries that want to introspect (PEP 440-ish, loose).
def _version_info() -> tuple[int, int, int]:
    parts = __version__.split(".")
    if len(parts) >= 3:
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2].split("-")[0]))
        except ValueError:
            pass
    return (0, 0, 0)


XRPL_AGENT_ID_VERSION_INFO = _version_info()

__all__ = [
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
    # Fee control (v0.5.0)
    "FeeCeilingExceeded",
    "max_fee_drops_for_usd",
    # Version (contract — pinned by tests/test_public_api.py)
    "XRPL_AGENT_ID_VERSION",
    "XRPL_AGENT_ID_VERSION_INFO",
]
