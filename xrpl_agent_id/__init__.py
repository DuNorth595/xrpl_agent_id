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

__version__ = "0.4.0"
XRPL_AGENT_ID_VERSION = __version__  # canonical alias — referenced in docs/tests

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
    # Version (contract — pinned by tests/test_public_api.py)
    "XRPL_AGENT_ID_VERSION",
    "XRPL_AGENT_ID_VERSION_INFO",
]
