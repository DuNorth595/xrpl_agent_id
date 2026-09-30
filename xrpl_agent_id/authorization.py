# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""AuthorizationPolicy — the org-side decision layer.

Where `TrustRegistry` answers "does this agent have the credentials I
require?", `AuthorizationPolicy` answers the broader question:

    "Should my organization transact with this agent right now?"

It composes:

  - AgentRegistry (read-side ledger state: DIDs, controllers, credentials)
  - BannedAgentRegistry (org-level deny list of addresses / controllers)
  - TrustRegistry (legacy require/deny rules from v0.2.0)

Decision model: ALLOW or DENY. Each decision carries a list of
AuthorizationReason entries — one per rule that fired (or one summary
reason if the policy has no rules but the agent is on the deny list).

Reasons are stable string codes, suitable for logging and dashboards:

  AGENT_BANNED              The agent's address is on the deny list.
  CONTROLLER_BANNED         A controller (signer) is on the deny list.
  CREDENTIAL_MISSING        A required credential was not found or not accepted.
  CREDENTIAL_REVOKED        A required credential was on ledger but not accepted.
  CREDENTIAL_TYPE_DENIED    A `deny` rule fired on a credential the agent holds.
  CREDENTIAL_EXPIRED        A required credential's expiration is in the past.
  NO_CREDENTIALS            Agent has zero credentials on ledger.
  OK                        All rules passed.

Usage:

    from xrpl_agent_id.authorization import (
        AuthorizationPolicy, RequestContext
    )
    from xrpl_agent_id.banned import BannedAgentRegistry

    bans = BannedAgentRegistry(path="banned.json")
    policy = AuthorizationPolicy(network="testnet", bans=bans)
    policy.require_credential(issuer="rAuditor...", credential_type=b"KYC")
    policy.require_credential(issuer="rEval...", credential_type=b"EVAL_PASS")

    decision = policy.evaluate(
        "did:xrpl:2:rAgent...",
        context=RequestContext(request_id="req-42", resource="/api/transfer"),
    )
    if decision.allow:
        # proceed
    else:
        # deny with reason codes for logging
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from xrpl_agent_id.banned import BannedAgentRegistry
from xrpl_agent_id.credential import RIPPLE_EPOCH
from xrpl_agent_id.registry import AgentRegistry


# Stable reason codes. New codes must be appended; never repurpose existing ones.
class ReasonCode(str, Enum):
    AGENT_BANNED = "AGENT_BANNED"
    CONTROLLER_BANNED = "CONTROLLER_BANNED"
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
    CREDENTIAL_REVOKED = "CREDENTIAL_REVOKED"  # credential not accepted by subject
    CREDENTIAL_TYPE_DENIED = "CREDENTIAL_TYPE_DENIED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    OK = "OK"


@dataclass
class AuthorizationReason:
    """A single reason entry attached to an AuthorizationDecision."""

    code: ReasonCode
    detail: str = ""
    issuer: str | None = None  # populated for credential-related reasons
    credential_type: bytes | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "detail": self.detail,
            "issuer": self.issuer,
            "credential_type": (
                self.credential_type.hex().upper() if self.credential_type else None
            ),
        }


@dataclass
class RequestContext:
    """Optional context for an authorization request.

    Attributes:
        request_id: Unique ID for this request. Auto-generated if not set.
        resource: The resource or operation being requested (e.g. "/api/transfer").
        requested_at: ISO 8601 UTC timestamp; auto-set if not provided.
        extra: Free-form metadata (e.g. user-agent, IP). Stored on the decision.
    """

    request_id: str = ""
    resource: str = ""
    requested_at: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = f"req-{uuid.uuid4().hex[:12]}"
        if not self.requested_at:
            self.requested_at = datetime.now(timezone.utc).isoformat()


@dataclass
class AuthorizationDecision:
    """The outcome of an AuthorizationPolicy.evaluate() call.

    Attributes:
        agent_did: DID that was evaluated.
        allow: True iff the agent is authorized.
        reasons: All reason entries — both passing (OK) and failing.
        evaluated_at: ISO 8601 UTC timestamp.
        request: The RequestContext used (or None if not provided).
        agent_record_summary: One-line summary of the resolved AgentRecord,
            for inclusion in audit logs without dumping the full record.
    """

    agent_did: str
    allow: bool
    reasons: list[AuthorizationReason]
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request: RequestContext | None = None
    agent_record_summary: str = ""

    @property
    def deny_reasons(self) -> list[AuthorizationReason]:
        return [r for r in self.reasons if r.code != ReasonCode.OK]

    def summary(self) -> str:
        if self.allow:
            return f"✓ ALLOW {self.agent_did}"
        codes = ",".join(r.code.value for r in self.deny_reasons)
        return f"✗ DENY  {self.agent_did}  reasons=[{codes}]"

    def to_dict(self) -> dict:
        return {
            "agent_did": self.agent_did,
            "allow": self.allow,
            "reasons": [r.to_dict() for r in self.reasons],
            "evaluated_at": self.evaluated_at,
            "request": (self.request.__dict__ if self.request else None),
            "agent_record_summary": self.agent_record_summary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class AuthorizationPolicy:
    """The org-side decision layer.

    Composes ledger state (via AgentRegistry), an org-level deny list (via
    BannedAgentRegistry), and explicit require/deny rules (built into this
    class — see .require_credential(), .deny_credential(), etc.).
    """

    def __init__(
        self,
        network: str = "testnet",
        bans: BannedAgentRegistry | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.network = network
        self.bans = bans or BannedAgentRegistry(network=network)
        self.registry = registry or AgentRegistry(network=network)
        self._required_credentials: list[tuple[str | None, bytes]] = []
        self._denied_credential_types: list[bytes] = []

    # ---- rule construction -----------------------------------------------

    def require_credential(
        self,
        issuer: str | None,
        credential_type: bytes,
        description: str = "",
    ) -> "AuthorizationPolicy":
        """Add a `require` rule: agent must hold credential of `credential_type`,
        from `issuer` (or any issuer if issuer is None).
        """
        self._required_credentials.append((issuer, credential_type))
        return self

    def deny_credential_type(
        self,
        credential_type: bytes,
        description: str = "",
    ) -> "AuthorizationPolicy":
        """Add a `deny` rule: agent must NOT hold this credential type."""
        self._denied_credential_types.append(credential_type)
        return self

    # ---- evaluation ------------------------------------------------------

    def evaluate(
        self,
        agent_identifier: str,
        context: RequestContext | None = None,
    ) -> AuthorizationDecision:
        """Evaluate an agent against this policy. Returns ALLOW or DENY + reasons."""
        if context is None:
            context = RequestContext()

        reasons: list[AuthorizationReason] = []
        # Resolve the agent record once. We never want to evaluate a stale cache.
        record = self.registry.resolve(agent_identifier, use_cache=False)

        # Rule 1: deny list — agent address itself
        agent_ban = self.bans.get(record.agent_address)
        if agent_ban is not None:
            reasons.append(
                AuthorizationReason(
                    code=ReasonCode.AGENT_BANNED,
                    detail=agent_ban.reason,
                    issuer=None,
                    credential_type=None,
                )
            )
            return self._make_decision(record, reasons, context)

        # Rule 2: deny list — any controller banned
        for ctrl in record.controllers:
            ctrl_ban = self.bans.get(ctrl)
            if ctrl_ban is not None:
                reasons.append(
                    AuthorizationReason(
                        code=ReasonCode.CONTROLLER_BANNED,
                        detail=f"controller {ctrl}: {ctrl_ban.reason}",
                        issuer=None,
                        credential_type=None,
                    )
                )
                break  # one controller-ban reason is enough

        # Rule 3: credential-level denies — if the agent holds a denied type
        creds_by_issuer_type: dict[tuple[str, bytes], object] = {
            (c.issuer, c.credential_type): c for c in record.credentials
        }
        for cred in record.credentials:
            if cred.credential_type in self._denied_credential_types:
                # extract issuer address from DID for the detail
                from xrpl_agent_id.did import parse_did
                try:
                    _, iss_addr = parse_did(cred.issuer)
                except Exception:
                    iss_addr = cred.issuer
                reasons.append(
                    AuthorizationReason(
                        code=ReasonCode.CREDENTIAL_TYPE_DENIED,
                        detail=f"agent holds banned credential type",
                        issuer=iss_addr,
                        credential_type=cred.credential_type,
                    )
                )

        # Rule 4: required credentials
        if self._required_credentials:
            if not record.credentials:
                # Fast path — agent has zero credentials, can't satisfy any requires.
                for issuer, ctype in self._required_credentials:
                    reasons.append(
                        AuthorizationReason(
                            code=ReasonCode.NO_CREDENTIALS,
                            detail="agent holds no credentials on ledger",
                            issuer=issuer,
                            credential_type=ctype,
                        )
                    )
            else:
                for req_issuer, req_ctype in self._required_credentials:
                    # Look for a matching credential on the record.
                    matched = False
                    for cred in record.credentials:
                        if cred.credential_type != req_ctype:
                            continue
                        if req_issuer is not None:
                            # Issuer must match. Compare DIDs by their addresses.
                            from xrpl_agent_id.did import parse_did
                            try:
                                _, cred_iss_addr = parse_did(cred.issuer)
                            except Exception:
                                cred_iss_addr = ""
                            if cred_iss_addr != req_issuer:
                                continue
                        # Expiry check first: an expired credential is dead
                        # regardless of accepted state. XLS-70 stores expiration
                        # as Ripple epoch seconds; None means no expiry.
                        if cred.expiration is not None:
                            now_ripple = int(time.time()) - RIPPLE_EPOCH
                            if cred.expiration < now_ripple:
                                reasons.append(
                                    AuthorizationReason(
                                        code=ReasonCode.CREDENTIAL_EXPIRED,
                                        detail=(
                                            f"credential expired at Ripple epoch "
                                            f"{cred.expiration} (now {now_ripple})"
                                        ),
                                        issuer=req_issuer,
                                        credential_type=req_ctype,
                                    )
                                )
                                matched = True
                                break
                        if not cred.accepted:
                            reasons.append(
                                AuthorizationReason(
                                    code=ReasonCode.CREDENTIAL_REVOKED,
                                    detail="credential on ledger but not accepted by subject",
                                    issuer=req_issuer,
                                    credential_type=req_ctype,
                                )
                            )
                        else:
                            # OK — found an accepted matching credential.
                            # No reason entry — we only log denies.
                            pass
                        matched = True
                        break
                    if not matched:
                        reasons.append(
                            AuthorizationReason(
                                code=ReasonCode.CREDENTIAL_MISSING,
                                detail="required credential not found on ledger",
                                issuer=req_issuer,
                                credential_type=req_ctype,
                            )
                        )

        if not reasons:
            reasons.append(AuthorizationReason(code=ReasonCode.OK, detail="all rules passed"))

        return self._make_decision(record, reasons, context)

    # ---- internals -------------------------------------------------------

    def _make_decision(
        self,
        record,
        reasons: list[AuthorizationReason],
        context: RequestContext,
    ) -> AuthorizationDecision:
        allow = all(r.code == ReasonCode.OK for r in reasons)
        return AuthorizationDecision(
            agent_did=record.agent_did,
            allow=allow,
            reasons=reasons,
            request=context,
            agent_record_summary=record.summary(),
        )


__all__ = [
    "AuthorizationPolicy",
    "AuthorizationDecision",
    "AuthorizationReason",
    "ReasonCode",
    "RequestContext",
]
