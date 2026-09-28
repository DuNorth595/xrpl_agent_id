# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""xrpl_agent_id.dashboard — live agent-ID dashboard for XRPL testnet.

Subscribes to the XRPL testnet websocket, filters for agent-identity
transactions (CredentialCreate / Accept / Delete, DIDSet / DIDDelete),
stores them in SQLite, and serves a single-page HTML dashboard.

Public entry points:
    python -m xrpl_agent_id.dashboard.monitor   # websocket subscriber
    python -m xrpl_agent_id.dashboard.server    # HTTP dashboard

Architecture mirrors the wallet-monitor pattern from XRPL_DEEPDIVE/scripts/.
"""
