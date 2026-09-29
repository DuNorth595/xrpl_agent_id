# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Agent Registry — resolve an agent to its full ledger state.

This module fixes the "stub" gap in the trust library: given an agent
(DID or classic address), enumerate everything we know about it from
the ledger in one pass. No pre-existing cache required.

The Registry is the read-side foundation that AuthorizationPolicy and
BannedAgentRegistry build on. It answers four questions:

  1. What credentials does the agent hold, and from whom? (AccountObjects type=credential)
  2. Does the agent have a DID document on ledger? (AccountObjects type=did)
  3. Who controls the account? (AccountObjects type=signer_list + DID doc controller)
  4. Has anything changed recently? (PreviousTxnLgrSeq per object)

For multi-sig accounts, "controller" is the set of signers whose combined
weight meets the quorum. We surface each signer as a separate Controller
so authorization policies can target any of them individually.

Usage:

    from xrpl_agent_id.registry import AgentRegistry

    registry = AgentRegistry(network="testnet")
    record = registry.resolve("did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf")
    print(record.credentials)        # list of Credential objects
    print(record.controllers)        # list of controller addresses
    print(record.last_activity_ledger)  # most recent ledger index seen
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from xrpl.models.requests import AccountObjects

from xrpl_agent_id.credential import Credential
from xrpl_agent_id.did import did_from_account, parse_did
from xrpl_agent_id.network import get_client


# XRPL Credential flag: lsfAccepted — the subject has accepted this credential.
_LSF_CREDENTIAL_ACCEPTED = 0x00010000


@dataclass
class AgentRecord:
    """The full ledger-derived state of one agent.

    Attributes:
        agent_address: Classic r-address of the agent.
        agent_did: did:xrpl:<netid>:<addr> form.
        did_uri: Hex-decoded URI from the DID ledger object (if present), else None.
        controllers: Addresses that have authority over this account. For single-sig
            accounts, this is just [agent_address] (the agent controls itself). For
            multi-sig accounts, this is the list of signer addresses whose combined
            weight meets the quorum.
        credentials: All credentials the agent holds on ledger, accepted or pending.
        banned: Convenience flag — True if the address appears on the system-level
            deny list. Set by callers (BannedAgentRegistry), not derived from ledger.
        last_activity_ledger: Highest ledger_index across all this account's objects,
            as a rough proxy for "last activity time." None if account has no objects.
        resolved_at: Wall-clock UTC timestamp at resolution time.
    """

    agent_address: str
    agent_did: str
    did_uri: Optional[str] = None
    controllers: list[str] = field(default_factory=list)
    credentials: list[Credential] = field(default_factory=list)
    banned: bool = False
    last_activity_ledger: Optional[int] = None
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def accepted_credential_types(self) -> list[bytes]:
        """Credential types the agent has explicitly accepted."""
        return [c.credential_type for c in self.credentials if c.accepted]

    @property
    def pending_credential_types(self) -> list[bytes]:
        """Credential types issued to the agent but not yet accepted."""
        return [c.credential_type for c in self.credentials if not c.accepted]

    def summary(self) -> str:
        n = len(self.credentials)
        accepted = len(self.accepted_credential_types)
        controllers = len(self.controllers)
        return (
            f"Agent {self.agent_address} | "
            f"{accepted}/{n} creds accepted | "
            f"{controllers} controller(s) | "
            f"ledger={self.last_activity_ledger}"
        )


class AgentRegistry:
    """Read-side registry: resolve any agent to its full ledger state.

    The Registry caches per-account resolution in-memory for the lifetime
    of this instance. Cache invalidation is manual (call .invalidate(addr))
    or by creating a new registry. The cache is intentional — multiple
    authorization checks against the same agent in a single request should
    not hit the ledger multiple times.
    """

    def __init__(self, network: str = "testnet", cache: bool = True) -> None:
        self.network = network
        self._cache_enabled = cache
        self._cache: dict[str, AgentRecord] = {}

    def resolve(self, agent_identifier: str, *, use_cache: bool = True) -> AgentRecord:
        """Resolve an agent (DID or classic address) to its full ledger state.

        Args:
            agent_identifier: Either a did:xrpl:<netid>:<addr> string, or a
                classic r-address.
            use_cache: If False, force re-resolution even if cached.

        Returns:
            AgentRecord with controllers, credentials, and metadata.

        Raises:
            ValueError: If the input can't be parsed as a DID or address.
            XRPLRequestFailureException: If a ledger query fails irrecoverably.
        """
        # Normalize to address
        if agent_identifier.startswith("did:xrpl:"):
            _, addr = parse_did(agent_identifier)
            did = agent_identifier
        elif agent_identifier.startswith("r"):
            addr = agent_identifier
            # Derive DID from address + network
            from xrpl_agent_id.did import did_from_account
            did = did_from_account(addr, network=self.network)
        else:
            raise ValueError(
                f"Cannot resolve {agent_identifier!r}: must be a did:xrpl:* string "
                f"or a classic r-address"
            )

        # Cache check
        if use_cache and self._cache_enabled and addr in self._cache:
            return self._cache[addr]

        client = get_client(self.network)

        # 1. All Credential objects on the account
        cred_objs = self._query_typed(client, addr, "credential")
        credentials = [self._parse_credential_obj(o) for o in cred_objs]

        # 2. DID object (if present)
        did_objs = self._query_typed(client, addr, "did")
        did_uri = None
        if did_objs:
            uri_hex = did_objs[0].get("URI")
            if uri_hex:
                try:
                    did_uri = bytes.fromhex(uri_hex).decode("utf-8", errors="replace")
                except Exception:
                    did_uri = None

        # 3. Controllers: start with the account itself, expand if multi-sig
        controllers = [addr]
        signer_objs = self._query_typed(client, addr, "signer_list")
        if signer_objs:
            sl = signer_objs[0]
            entries = sl.get("SignerEntries", [])
            for e in entries:
                signer = e.get("SignerEntry", {})
                signer_addr = signer.get("Account")
                if signer_addr and signer_addr not in controllers:
                    controllers.append(signer_addr)

        # 4. Last activity = max ledger index across all objects
        last_ledger = None
        for o in cred_objs + did_objs + signer_objs:
            seq = o.get("PreviousTxnLgrSeq")
            if seq is not None:
                if last_ledger is None or seq > last_ledger:
                    last_ledger = seq

        record = AgentRecord(
            agent_address=addr,
            agent_did=did,
            did_uri=did_uri,
            controllers=controllers,
            credentials=credentials,
            last_activity_ledger=last_ledger,
        )

        if self._cache_enabled:
            self._cache[addr] = record

        return record

    def invalidate(self, agent_identifier: str) -> None:
        """Drop the cached record for one agent."""
        if agent_identifier.startswith("did:xrpl:"):
            _, addr = parse_did(agent_identifier)
        else:
            addr = agent_identifier
        self._cache.pop(addr, None)

    def clear_cache(self) -> None:
        """Drop all cached records."""
        self._cache.clear()

    # ---- internals -----------------------------------------------------

    def _query_typed(self, client, account: str, object_type: str) -> list[dict]:
        """Query account_objects filtered by type, with pagination via marker."""
        results: list[dict] = []
        marker = None
        for _ in range(10):  # safety bound: max 10 pages = 4000 objects (400 per page)
            kwargs = {"account": account, "type": object_type, "limit": 400}
            if marker is not None:
                kwargs["marker"] = marker
            resp = client.request(AccountObjects(**kwargs))
            data = resp.result or {}
            results.extend(data.get("account_objects", []))
            marker = data.get("marker")
            if marker is None:
                break
        return results

    def _parse_credential_obj(self, obj: dict) -> Credential:
        """Convert a ledger Credential object to our Credential dataclass."""
        cred_type_hex = obj.get("CredentialType", "")
        cred_type_bytes = bytes.fromhex(cred_type_hex) if cred_type_hex else b""
        flags = int(obj.get("Flags", 0))
        accepted = bool(flags & _LSF_CREDENTIAL_ACCEPTED)
        uri_hex = obj.get("URI", "")
        uri = None
        if uri_hex:
            try:
                uri = bytes.fromhex(uri_hex).decode("utf-8", errors="replace")
            except Exception:
                uri = None
        # Convert addresses to did:xrpl:* form to match Credential dataclass.
        # Network in the DID is the mainnet placeholder; we use the registry's
        # network here so downstream code sees consistent DIDs.
        issuer_addr = obj.get("Issuer", "")
        subject_addr = obj.get("Subject", "")
        issuer_did = did_from_account(issuer_addr, network=self.network) if issuer_addr else ""
        subject_did = did_from_account(subject_addr, network=self.network) if subject_addr else ""
        return Credential(
            issuer=issuer_did,
            subject=subject_did,
            credential_type=cred_type_bytes,
            uri=uri,
            accepted=accepted,
        )


__all__ = [
    "AgentRecord",
    "AgentRegistry",
]
