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

__version__ = "0.2.3"

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
]
