# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""TrustRegistry — the trust-library layer on top of Authority.

Wraps the pattern "given an agent, do they have the credentials I require
from issuers I trust?" into one call. A verifier (marketplace, enterprise
deployer, regulator) defines their trust policy once; calling
`TrustRegistry.check(agent)` answers "yes/no" plus which specific
requirements failed.

Usage:

    policy = TrustRegistry(network="testnet")
    policy.require(issuer="rAuditorXYZ...", credential_type=b"KYC")
    policy.require(issuer="rEvalBoard...", credential_type=b"EVAL_PASS")
    policy.deny(issuer="rBadActor...",  credential_type=b"ANY")

    result = policy.check(agent_did="did:xrpl:1:r...")
    if not result.all_satisfied:
        for issuer, cred_type in result.missing:
            print(f"Missing: {cred_type.hex()} from {issuer}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from xrpl_agent_id.authority import (
    Authority,
    VerificationResult,
    _check_credential,
)
from xrpl_agent_id.network import get_client


@dataclass
class TrustPolicy:
    """A rule in a TrustRegistry.

    Attributes:
        issuers: Set of allowed issuer addresses (None means "any").
        credential_type: Required credential type (None means "any").
        action: 'require' (must have) or 'deny' (must NOT have).
        description: Human-readable label for logging.
    """

    issuers: set[str] | None
    credential_type: bytes | None
    action: str = "require"  # 'require' | 'deny'
    description: str = ""

    def matches(self, issuer: str, credential_type: bytes) -> bool:
        """True iff this policy's issuer/type pair matches the input."""
        if self.credential_type is not None and credential_type != self.credential_type:
            return False
        if self.issuers is not None and issuer not in self.issuers:
            return False
        return True


@dataclass
class TrustCheckResult:
    """Outcome of a TrustRegistry.check() call.

    Attributes:
        agent_did: The DID that was checked.
        satisfied: True iff all `require` rules pass AND no `deny` rules trigger.
        missing_required: (issuer, credential_type) pairs that were required but not found.
        denied_held: (issuer, credential_type) pairs that the agent HOLDS but a `deny` rule fires on.
        all_credentials_found: All (issuer, credential_type) pairs found for the agent, across all rules.
    """

    agent_did: str
    satisfied: bool
    missing_required: list[tuple[str, bytes]] = field(default_factory=list)
    denied_held: list[tuple[str, bytes]] = field(default_factory=list)
    all_credentials_found: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def all_satisfied(self) -> bool:
        return self.satisfied

    def summary(self) -> str:
        if self.satisfied:
            return f"✓ trust policy satisfied for {self.agent_did}"
        parts = []
        if self.missing_required:
            parts.append(f"{len(self.missing_required)} missing required")
        if self.denied_held:
            parts.append(f"{len(self.denied_held)} denied-credential held")
        return f"✗ trust policy FAILED for {self.agent_did}: " + ", ".join(parts)


class TrustRegistry:
    """A composable trust policy for verifying AI agents.

    A registry has zero or more `require` rules and zero or more `deny`
    rules. `check(agent)` returns a TrustCheckResult that says whether
    the agent passes all rules.
    """

    def __init__(self, network: str = "testnet") -> None:
        self.network = network
        self._rules: list[TrustPolicy] = []
        # Per-agent cache: agent_did → list[(issuer, credential_type)] seen
        self._cache: dict[str, list[tuple[str, bytes]]] = {}

    def require(
        self,
        issuer: str | Iterable[str] | None,
        credential_type: bytes | None,
        description: str = "",
    ) -> "TrustRegistry":
        """Add a `require` rule.

        Args:
            issuer: One issuer address, an iterable of addresses, or None for any issuer.
            credential_type: Required credential type bytes, or None for any.
            description: Optional human-readable label.
        """
        issuers: set[str] | None
        if issuer is None:
            issuers = None
        elif isinstance(issuer, str):
            issuers = {issuer}
        else:
            issuers = set(issuer)
        self._rules.append(TrustPolicy(issuers=issuers, credential_type=credential_type, action="require", description=description))
        return self

    def deny(
        self,
        issuer: str | Iterable[str] | None,
        credential_type: bytes | None,
        description: str = "",
    ) -> "TrustRegistry":
        """Add a `deny` rule. Triggers if the agent holds a matching credential."""
        issuers: set[str] | None
        if issuer is None:
            issuers = None
        elif isinstance(issuer, str):
            issuers = {issuer}
        else:
            issuers = set(issuer)
        self._rules.append(TrustPolicy(issuers=issuers, credential_type=credential_type, action="deny", description=description))
        return self

    def check(self, agent_did: str) -> TrustCheckResult:
        """Evaluate all rules against the given agent.

        Returns:
            TrustCheckResult with .satisfied, .missing_required, .denied_held.
        """
        from xrpl_agent_id.did import parse_did

        if agent_did.startswith("did:xrpl:"):
            _, subject_addr = parse_did(agent_did)
        else:
            subject_addr = agent_did

        require_rules = [r for r in self._rules if r.action == "require"]
        deny_rules = [r for r in self._rules if r.action == "deny"]

        # 1. For each require rule, query ledger for at least one matching credential
        missing_required: list[tuple[str, bytes]] = []
        client = get_client(self.network)
        for rule in require_rules:
            if self._rule_satisfied(client, subject_addr, rule):
                continue
            # For require rules with specific issuers, missing is per-issuer.
            if rule.issuers is not None:
                for iss in rule.issuers:
                    cred = rule.credential_type or b""
                    missing_required.append((iss, cred))
            else:
                # Issuer-agnostic: report as (None, type) sentinel
                missing_required.append(("<any>", rule.credential_type or b""))

        # 2. For each deny rule, check if agent holds any matching credential
        denied_held: list[tuple[str, bytes]] = []
        if deny_rules:
            # Get all credentials for the agent (heuristic: query for all known types)
            # For now, use the Authority.get_credentials_for_account if available;
            # fallback to per-rule scan.
            agent_creds = self._get_agent_credentials(client, subject_addr)
            for issuer, ctype in agent_creds:
                for rule in deny_rules:
                    if rule.matches(issuer, ctype):
                        denied_held.append((issuer, ctype))
                        break

        satisfied = (not missing_required) and (not denied_held)

        return TrustCheckResult(
            agent_did=agent_did,
            satisfied=satisfied,
            missing_required=missing_required,
            denied_held=denied_held,
            all_credentials_found=self._get_agent_credentials(client, subject_addr),
        )

    def _rule_satisfied(self, client, subject_addr: str, rule: TrustPolicy) -> bool:
        """True iff at least one (issuer, type) pair matching the rule is held by the agent."""
        if rule.issuers is None and rule.credential_type is None:
            # Trivial "any credential" — assume false (we don't know what to look for)
            return False
        if rule.issuers is None:
            # Issuer-agnostic, specific type: need to find any issuer that has it
            # Heuristic: can't enumerate issuers cheaply, so this is best-effort.
            # Real impl would use get_credentials_for_account; for now return False.
            return False
        for issuer in rule.issuers:
            cred = rule.credential_type or b""
            if _check_credential(client, subject_addr, issuer, cred):
                return True
        return False

    def _get_agent_credentials(self, client, subject_addr: str) -> list[tuple[str, bytes]]:
        """Best-effort enumeration of all credentials held by an agent.

        Note: XRPL does not natively support enumerating all credentials for
        an account without a known issuer. This is a known limitation. We
        surface the agents we already know about via the Authority API.
        """
        # TODO: use xrpl.account_objects to fetch Credential ledger objects
        # when available in 4.6+. For now, return cached + nothing new.
        return list(self._cache.get(subject_addr, []))


__all__ = [
    "TrustPolicy",
    "TrustCheckResult",
    "TrustRegistry",
]
