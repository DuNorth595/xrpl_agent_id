# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""BannedAgentRegistry — organization-level deny list for agents and controllers.

This is the *org-side* deny list, separate from the on-chain CredentialDelete
revocation model. Where CredentialDelete marks a single (issuer, subject,
type) triple as revoked, this registry marks whole addresses (or whole
controllers) as banned regardless of what credentials they hold.

Two kinds of ban:

  - address ban: ban a specific XRPL address. Applies if that address is the
    agent itself OR any of its controllers (signers on a multi-sig account).
  - controller ban: ban by a controller address. If ANY controller of an
    agent is on the controller ban list, the agent is denied.

Both kinds carry the same metadata (reason, added_by, added_at, optional
expires_at, reference). Expirations are checked at lookup time.

Persistence: JSON file at `banned_agents.json` next to the script by default,
but the path is configurable. Atomic writes via tempfile + rename.

Usage:

    from xrpl_agent_id.banned import BannedAgentRegistry, Ban

    bans = BannedAgentRegistry(network="testnet", path="banned.json")
    bans.add(Ban(address="rBadAgent...", reason="credential stuffing 2026-09-15",
                 added_by="secops@S_DevLabs", reference="INC-1043"))
    if bans.is_banned("rBadAgent..."):
        ...
    if bans.is_controller_banned(["rA", "rB", "rBadController..."]):
        ...
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Ban:
    """A single entry in the deny list.

    Attributes:
        address: The banned XRPL address (r...). Exactly one of (address,
            controller_address) is required when constructing via `add`.
        controller_address: Alias of `address` — kept as a separate field for
            clarity at the call site (so it's obvious why you're banning).
        reason: Human-readable reason, ≤ 512 chars.
        added_by: Operator/system that added the ban.
        added_at: ISO 8601 UTC timestamp; auto-set by BannedAgentRegistry.add().
        expires_at: Optional ISO 8601 UTC timestamp; if set, the ban is
            inactive past this time.
        reference: Optional case ID / ticket number / on-chain tx hash.
    """

    address: str = ""
    controller_address: str = ""
    reason: str = ""
    added_by: str = ""
    added_at: str = ""
    expires_at: str | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not self.address and not self.controller_address:
            raise ValueError("Ban must specify address or controller_address")
        # If both are set and DIFFERENT, that's a real conflict — caller error.
        # If both are set and EQUAL, that's the post-add normalized form (we fill
        # the missing one). Tolerate that case so JSON reload works.
        if self.address and self.controller_address and self.address != self.controller_address:
            raise ValueError(
                "Ban must specify exactly one of address or controller_address"
            )
        if not self.reason:
            raise ValueError("Ban.reason is required")
        if len(self.reason) > 512:
            raise ValueError("Ban.reason exceeds 512 chars")
        # Normalize: if only controller_address was set, also fill address.
        if not self.address:
            self.address = self.controller_address
        if not self.controller_address:
            self.controller_address = self.address

    def is_expired(self, now: datetime | None = None) -> bool:
        """True if expires_at is set and is in the past relative to `now`."""
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now >= expiry

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Ban":
        # Backward-compatible: accept "address" as the canonical field
        return cls(
            address=d.get("address", ""),
            controller_address=d.get("controller_address", ""),
            reason=d.get("reason", ""),
            added_by=d.get("added_by", ""),
            added_at=d.get("added_at", ""),
            expires_at=d.get("expires_at"),
            reference=d.get("reference", ""),
        )


class BannedAgentRegistry:
    """An in-memory + JSON-backed deny list for XRPL agent addresses.

    Thread-safety: not safe. If you need concurrent writes, wrap add/remove
    in an external lock. Reads via is_banned / is_controller_banned are safe
    to call concurrently with each other but NOT while add/remove is in flight.
    """

    def __init__(self, network: str = "testnet", path: str | Path | None = None) -> None:
        self.network = network
        self.path = Path(path) if path else Path("banned_agents.json")
        self._bans: dict[str, Ban] = {}
        self._loaded = False

    # ---- persistence ----------------------------------------------------

    def load(self) -> None:
        """Read bans from disk. Idempotent."""
        if not self.path.exists():
            self._loaded = True
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # File format: {"version": 1, "bans": [Ban.to_dict(), ...]}
        bans_raw = data.get("bans", []) if isinstance(data, dict) else data
        self._bans = {b["address"]: Ban.from_dict(b) for b in bans_raw}
        self._loaded = True

    def save(self) -> None:
        """Atomically write bans to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "network": self.network,
            "saved_at": _utcnow_iso(),
            "bans": [b.to_dict() for b in self._bans.values()],
        }
        # Atomic write: tempfile in same dir, then rename.
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(self.path.parent), encoding="utf-8"
        ) as tmp:
            tmp.write(json.dumps(payload, indent=2, sort_keys=True))
            tmp_path = tmp.name
        os.replace(tmp_path, self.path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ---- mutation --------------------------------------------------------

    def add(self, ban: Ban, *, persist: bool = True) -> Ban:
        """Add a ban. Auto-sets added_at if not already set.

        If a ban already exists for this address, it is replaced (the latest
        reason wins). Callers wanting to extend an existing ban should read
        it first and update the existing entry instead.
        """
        self._ensure_loaded()
        if not ban.added_at:
            ban.added_at = _utcnow_iso()
        self._bans[ban.address] = ban
        if persist:
            self.save()
        return ban

    def remove(self, address: str, *, persist: bool = True) -> bool:
        """Remove a ban. Returns True if a ban was removed."""
        self._ensure_loaded()
        existed = address in self._bans
        self._bans.pop(address, None)
        if persist and existed:
            self.save()
        return existed

    # ---- lookup ----------------------------------------------------------

    def is_banned(self, address: str) -> bool:
        """True if `address` has an active (non-expired) ban."""
        self._ensure_loaded()
        ban = self._bans.get(address)
        if ban is None:
            return False
        return not ban.is_expired()

    def is_controller_banned(self, controllers: Iterable[str]) -> bool:
        """True if ANY of the given controllers has an active ban."""
        self._ensure_loaded()
        for c in controllers:
            if self.is_banned(c):
                return True
        return False

    def get(self, address: str) -> Ban | None:
        """Return the active ban for `address`, or None."""
        self._ensure_loaded()
        ban = self._bans.get(address)
        if ban is None or ban.is_expired():
            return None
        return ban

    def all_active(self) -> list[Ban]:
        """Return all currently-active bans (non-expired)."""
        self._ensure_loaded()
        return [b for b in self._bans.values() if not b.is_expired()]

    def all_bans(self) -> list[Ban]:
        """Return ALL bans including expired ones (for audit / cleanup)."""
        self._ensure_loaded()
        return list(self._bans.values())

    def __len__(self) -> int:
        return len(self.all_active())

    def __contains__(self, address: str) -> bool:
        return self.is_banned(address)


__all__ = ["Ban", "BannedAgentRegistry"]
