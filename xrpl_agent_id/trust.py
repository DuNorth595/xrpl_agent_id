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


def _normalize_credential_type(
    credential_type: "bytes | str | None",
) -> "bytes | None":
    """Accept a credential type as bytes or str, store it as bytes.

    `xrpl.models` uses bytes for credential types, so bytes is the
    canonical form and what comes back out of a check. But every caller
    writes a URI, and `b"https://..."` is a papercut at every call site.

    Both are accepted. A str is encoded as UTF-8. bytes are passed through
    unchanged, so existing code is unaffected.
    """
    if credential_type is None:
        return None
    if isinstance(credential_type, str):
        return credential_type.encode("utf-8")
    if isinstance(credential_type, bytes):
        return credential_type
    raise TypeError(
        "credential_type must be bytes, str, or None; got "
        f"{type(credential_type).__name__}"
    )


@dataclass
class TrustPolicy:
    """A rule in a TrustRegistry.

    Attributes:
        issuers: Set of allowed issuer addresses (None means "any").
        credential_type: Required credential type (None means "any").
            Accepts bytes or str; stored as bytes.
        action: 'require' (must have) or 'deny' (must NOT have).
        description: Human-readable label for logging.
    """

    issuers: set[str] | None
    credential_type: "bytes | str | None" = None
    action: str = "require"  # 'require' | 'deny' | 'ban'
    description: str = ""

    def __post_init__(self) -> None:
        # Normalized once, at construction. After this the value is always
        # bytes or None, whatever was passed in.
        self.credential_type = _normalize_credential_type(self.credential_type)
        if self.credential_type is None:
            self._credential_type_bytes = None
        else:
            self._credential_type_bytes = self.credential_type

    @property
    def credential_type_bytes(self) -> "bytes | None":
        """The credential type as bytes, whatever was passed in."""
        return self._credential_type_bytes

    def matches(self, issuer: str, credential_type: bytes) -> bool:
        """True iff this policy's issuer/type pair matches the input.

        A `ban` rule with no credential type matches on the issuer alone --
        "banned regardless of what credentials they hold".
        """
        if self.action == "ban" and self.credential_type_bytes is None:
            if self.issuers is not None and issuer not in self.issuers:
                return False
            return True
        if self.credential_type_bytes is not None and credential_type != self.credential_type_bytes:
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
    banned: bool = False
    ban_reason: str | None = None

    @property
    def all_satisfied(self) -> bool:
        return self.satisfied

    def summary(self) -> str:
        if self.satisfied:
            return f"✓ trust policy satisfied for {self.agent_did}"
        if self.banned:
            reason = f": {self.ban_reason}" if self.ban_reason else ""
            return f"✗ trust policy FAILED for {self.agent_did}: BANNED{reason}"
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
        credential_type: "bytes | str | None",
        description: str = "",
    ) -> "TrustRegistry":
        """Add a `require` rule.

        Args:
            issuer: One issuer address, an iterable of addresses, or None for any issuer.
            credential_type: Required credential type, or None for any. Accepts
                bytes or str; stored as bytes.
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
        credential_type: "bytes | str | None",
        description: str = "",
    ) -> "TrustRegistry":
        """Add a `deny` rule. Triggers if the agent holds a matching credential.

        Args:
            issuer: One issuer address, an iterable of addresses, or None for any issuer.
            credential_type: The credential type that triggers the denial, or
                None for any type. Accepts bytes or str; stored as bytes.
            description: Optional human-readable label.
        """
        issuers: set[str] | None
        if issuer is None:
            issuers = None
        elif isinstance(issuer, str):
            issuers = {issuer}
        else:
            issuers = set(issuer)
        self._rules.append(TrustPolicy(issuers=issuers, credential_type=credential_type, action="deny", description=description))
        return self

    def ban(
        self,
        subject: str | Iterable[str] | None,
        description: str = "",
    ) -> "TrustRegistry":
        """Add a `ban` rule: deny the agent outright.

        A ban is different from a `deny` rule. A `deny` rule says "this
        agent may not hold a credential of this type" -- it is about
        credentials. A ban says "this agent may not act, whatever it
        holds" -- it is about the agent.

        Args:
            subject: One agent address or DID, an iterable of them, or None
                to match any agent (use with care).
            description: Optional human-readable label.
        """
        addresses: set[str] | None
        if subject is None:
            addresses = None
        elif isinstance(subject, str):
            addresses = {subject}
        else:
            addresses = set(subject)

        self._rules.append(
            TrustPolicy(
                issuers=addresses,
                credential_type=None,
                action="ban",
                description=description,
            )
        )
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
        ban_rules = [r for r in self._rules if r.action == "ban"]
        client = get_client(self.network)

        # 0. Bans are evaluated first and are unconditional. A banned agent
        #    is denied whatever it holds, so there is no point checking
        #    anything else.
        for rule in ban_rules:
            if rule.issuers is None or subject_addr in rule.issuers:
                return TrustCheckResult(
                    agent_did=agent_did,
                    satisfied=False,
                    missing_required=[],
                    denied_held=[],
                    all_credentials_found=self._get_agent_credentials(
                        client, subject_addr
                    ),
                    banned=True,
                    ban_reason=rule.description or "agent is banned",
                )

        # 1. For each require rule, query ledger for at least one matching credential
        missing_required: list[tuple[str, bytes]] = []
        for rule in require_rules:
            if self._rule_satisfied(client, subject_addr, rule):
                continue
            # The rule is unsatisfied. Report it once, not once per issuer.
            #
            # A rule with a set of issuers is satisfied by a credential from
            # ANY issuer in that set, so listing each issuer separately
            # would read as though all of them were individually required.
            # The first issuer in the set is used as the representative for
            # the tuple, and the full set is available on the rule itself.
            if rule.issuers is not None:
                cred = rule.credential_type_bytes or b""
                representative = sorted(rule.issuers)[0] if rule.issuers else "<any>"
                missing_required.append((representative, cred))
            else:
                # Issuer-agnostic: report as (None, type) sentinel
                missing_required.append(("<any>", rule.credential_type_bytes or b""))

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
        """True iff at least one (issuer, type) pair matching the rule is held by the agent.

        For a rule with a specific issuer set, this asks the ledger whether
        the agent holds a matching credential from any issuer in the set.

        For an issuer-agnostic rule (`issuer=None`), the credentials the
        agent actually holds are enumerated from the ledger and matched
        against the rule. That enumeration is the same authoritative read
        used for `deny` rules, so an issuer-agnostic `require` is answered
        by the ledger rather than by a guess.
        """
        if rule.issuers is None:
            if rule.credential_type_bytes is None:
                # "any credential at all" -- satisfied if the agent holds
                # any credential whatsoever.
                return bool(self._get_agent_credentials(client, subject_addr))
            for _issuer, ctype in self._get_agent_credentials(client, subject_addr):
                if ctype == rule.credential_type_bytes:
                    return True
            return False
        for issuer in rule.issuers:
            cred = rule.credential_type_bytes or b""
            if _check_credential(client, subject_addr, issuer, cred):
                return True
        return False

    def _get_agent_credentials(self, client, subject_addr: str) -> list[tuple[str, bytes]]:
        """Enumerate all credentials held by an agent via the ledger.

        Uses AgentRegistry (xrpl.account_objects type=credential) under the
        hood, so this is now an authoritative read instead of a cache lookup.
        The result is returned as (issuer_address, credential_type_bytes)
        tuples to match the existing TrustPolicy.matches() signature.
        """
        from xrpl_agent_id.did import parse_did
        from xrpl_agent_id.registry import AgentRegistry

        registry = AgentRegistry(network=self.network)
        record = registry.resolve(subject_addr)
        # Convert each issuer DID back to its classic address so the rest of
        # trust.py can match on address form (matches existing TrustPolicy).
        result: list[tuple[str, bytes]] = []
        for c in record.credentials:
            try:
                _, issuer_addr = parse_did(c.issuer)
            except Exception:
                continue
            result.append((issuer_addr, c.credential_type))
        self._cache[subject_addr] = result
        return list(result)


__all__ = [
    "TrustPolicy",
    "TrustCheckResult",
    "TrustRegistry",
]
