# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id — Identity for AI agents on the XRP Ledger.

Public surface is intentionally small. Detailed implementation lives in submodules.

Core types:
    AgentIdentity — a single agent's XRPL identity (DID + keys + credentials)
    Credential    — an attestation issued by one agent to another
    DIDDocument   — W3C DID Document for an XRPL account
"""

from xrpl_agent_id.identity import AgentIdentity
from xrpl_agent_id.credential import Credential
from xrpl_agent_id.did import (
    DIDDocument,
    NETWORK_IDS,
    did_from_account,
    parse_did,
    resolve_did,
)

__version__ = "0.0.0"

__all__ = [
    "AgentIdentity",
    "Credential",
    "DIDDocument",
    "NETWORK_IDS",
    "did_from_account",
    "parse_did",
    "resolve_did",
]
