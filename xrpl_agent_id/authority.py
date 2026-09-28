# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Authority — the issuer side of the credential flow.

A separate class from AgentIdentity for clarity: an Authority can ONLY issue
credentials (sign CredentialCreate) and look up existing ones. It cannot
accept or modify credentials.

In most real deployments, an Authority is a service (eval registry,
attestation provider) rather than an individual agent.
"""

from __future__ import annotations

from xrpl.wallet import Wallet

from xrpl_agent_id.identity import AgentIdentity


class Authority(AgentIdentity):
    """An Authority — an entity that can issue XLS-70 credentials.

    Inherits the wallet handling and identity properties from
    AgentIdentity. Constrained: cannot accept credentials, only issue them.
    """

    def accept_credential(self, *args, **kwargs):  # noqa: D401
        raise NotImplementedError(
            "Authority cannot accept credentials — only AgentIdentity (subject role) can."
        )

    def set_did_document(self, *args, **kwargs):  # noqa: D401
        raise NotImplementedError(
            "Authority cannot set its own DID Document — instantiate an AgentIdentity instead."
        )
