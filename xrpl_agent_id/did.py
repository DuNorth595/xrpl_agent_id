# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""did:xrpl — DID method implementation per XLS-40d.

Method spec: https://github.com/XRPLF/XRPL-Standards/blob/master/XLS-0040-decentralized-identity/README.md

Scheme:    did:xrpl:<network-id>:<xrpl-specific-idstring>
Example:   did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh

Where <network-id> is the XRPL network ID (1 for mainnet, 2 for testnet)
and <xrpl-specific-idstring> is an AccountID (classic address rXXX) or
the hex of a master public key.

Resolution: parse DID → call xrpl.ledger_entry with `did` = AccountID →
            get back DIDDocument (hex blob, max 256 bytes) + Data + URI →
            decode hex → JSON DID Document.

This module handles parsing and resolution. The DID Document itself is a
plain dataclass; serialization lives in identity.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Network ID mapping per XLS-37 + XLS-40d examples.
# Mainnet ID 1 confirmed in XLS-40d README: `did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh`.
# (The legacy NetworkID value 0 means "any network" and is reserved — NOT a chain ID.)
NETWORK_IDS = {
    "mainnet": 1,
    "testnet": 2,
    "devnet": 3,
    "amm-devnet": 25,
    "sidechain": 222,
}

# did:xrpl:<network-id>:<idstring>
_DID_PATTERN = re.compile(r"^did:xrpl:(\d+):([A-Za-z0-9]+)$")


@dataclass
class DIDDocument:
    """W3C DID Document for an XRPL account.

    On-ledger, this is stored as a hex-encoded JSON blob in DIDSet.DIDDocument
    (max 256 bytes). Anything larger goes behind URI (HTTPS or IPFS).

    XLS-40d uses pre-DID-Core-1.0 vocabulary (publicKey, EcdsaKoblitzPublicKey).
    Modern DID Core uses verificationMethod. This library accepts both for
    forward-compat.
    """

    id: str  # the DID itself, e.g. did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh
    verification_method: list[dict] = field(default_factory=list)
    authentication: list[str] = field(default_factory=list)
    service: list[dict] = field(default_factory=list)
    also_known_as: list[str] = field(default_factory=list)
    controller: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # full JSON for forward-compat

    @classmethod
    def from_json(cls, doc: dict) -> "DIDDocument":
        """Parse a DID Document from its JSON form (post-hex-decode)."""
        return cls(
            id=doc["id"],
            verification_method=doc.get("verificationMethod", doc.get("publicKey", [])),
            authentication=doc.get("authentication", []),
            service=doc.get("service", []),
            also_known_as=doc.get("alsoKnownAs", []),
            controller=doc.get("controller", []),
            raw=doc,
        )

    def to_json(self) -> dict:
        """Serialize to W3C DID Core 1.0 JSON form.

        Always emits an array @context (decID used a bare string — rejected
        by strict resolvers). verificationMethod entries are normalized so
        callers can pass either W3C-style or XLS-40d-legacy field names and
        we emit the canonical W3C shape.
        """
        # Normalize verification methods to W3C DID Core 1.0 shape.
        # Accept: {id, type, controller, publicKeyHex, publicKeyMultibase}
        # Emit:  {id, type, controller, publicKeyMultibase}
        normalized_vm = []
        for vm in self.verification_method:
            entry = {
                "id": vm["id"],
                "type": _normalize_vm_type(vm.get("type")),
                "controller": vm.get("controller", self.id),
            }
            # Prefer publicKeyMultibase; fall back to constructing one from
            # publicKeyHex by prepending the ed25519-pub multicodec (0xed01).
            if "publicKeyMultibase" in vm:
                entry["publicKeyMultibase"] = vm["publicKeyMultibase"]
            elif "publicKeyHex" in vm:
                # XRPL master public keys are 33 bytes compressed secp256k1
                # (prefix 0x02 or 0x03). For secp256k1-pub multicodec we
                # prefix 0xe7 0x01. See multicodec table.
                # Note: decID used bare 'z' + base58 — that's the gotcha
                # the assessment flagged. We emit the proper multicodec.
                hex_key = vm["publicKeyHex"]
                if hex_key.startswith(("02", "03")):
                    multicodec = "e701"
                else:
                    # Ed25519 keys are 32 bytes, multicodec 0xed 0x01
                    multicodec = "ed01"
                # Base58btc encode the multicodec + raw key bytes
                entry["publicKeyMultibase"] = _multibase_base58btc(multicodec, hex_key)
            normalized_vm.append(entry)

        # Always emit @context as an array per W3C DID Core 1.0 §"context"
        return {
            "@context": [
                "https://www.w3.org/ns/did/v1",
                "https://w3id.org/security/multikey/v1",
            ],
            "id": self.id,
            "verificationMethod": normalized_vm,
            "authentication": self.authentication,
            "service": self.service,
            "alsoKnownAs": self.also_known_as,
            "controller": self.controller,
        }


def _normalize_vm_type(t: str | None) -> str:
    """Map XLS-40d legacy types + Ed25519VerificationKey2020 → Multikey.

    decID used 'Ed25519VerificationKey2020' (deprecated 2022). XLS-40d
    samples use 'EcdsaKoblitzPublicKey' (XRPL-flavoured). W3C current is
    'Multikey'. We default to Multikey when callers pass legacy types.
    """
    if t is None:
        return "Multikey"
    deprecated = {
        "Ed25519VerificationKey2020",
        "EcdsaKoblitzPublicKey",
        "EcdsaSecp256k1VerificationKey2019",
    }
    if t in deprecated:
        return "Multikey"
    return t


def _multibase_base58btc(multicodec_hex: str, raw_key_hex: str) -> str:
    """Multibase-encode bytes as 'z' + base58btc.

    multibase prefix 'z' = base58btc (Bitcoin alphabet).
    multicodec is a varint prepended to the key bytes.

    Minimal impl: works for the small multicodecs we use (0xed01, 0xe701).
    """
    import base64
    import binascii

    raw = binascii.unhexlify(raw_key_hex)
    mc = binascii.unhexlify(multicodec_hex)
    payload = mc + raw
    # base58btc alphabet (Bitcoin): 123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
    b58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    # Convert to int then base58
    n = int.from_bytes(payload, "big")
    out = b""
    while n > 0:
        n, rem = divmod(n, 58)
        out = b58[rem:rem+1] + out
    # Preserve leading zero bytes (none in our case but be safe)
    for byte in payload:
        if byte == 0:
            out = b"1" + out
        else:
            break
    return "z" + out.decode("ascii")


def did_from_account(address: str, network: str = "mainnet") -> str:
    """Build a did:xrpl DID from a classic address.

    >>> did_from_account("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "mainnet")
    'did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh'
    """
    net_id = NETWORK_IDS.get(network)
    if net_id is None:
        raise ValueError(f"Unknown network: {network}. Known: {list(NETWORK_IDS)}")
    return f"did:xrpl:{net_id}:{address}"


def parse_did(did: str) -> tuple[int, str]:
    """Parse a did:xrpl into (network_id, idstring).

    Raises ValueError on malformed input.
    """
    m = _DID_PATTERN.match(did)
    if not m:
        raise ValueError(f"Not a valid did:xrpl: {did!r}")
    return int(m.group(1)), m.group(2)


def resolve_did(did: str, client: Any = None) -> DIDDocument:
    """Resolve a did:xrpl to its DID Document via xrpl.ledger_entry.

    Requires a connected xrpl JsonRpcClient. Falls back to testnet if no client.

    NOTE: implementation here is a stub. The real resolution flow is:
        1. parse_did(did) → (network_id, account)
        2. client.request(LedgerEntry, {"did": account})
        3. response["node"]["DIDDocument"] is hex-encoded JSON
        4. decode hex → json.loads → DIDDocument.from_json(...)
        5. If DIDDocument is empty/None, fall back to implicit doc:
           synthesize from account's master public key
    """
    raise NotImplementedError(
        f"resolve_did({did!r}) — wire this up once ledger client is available"
    )
