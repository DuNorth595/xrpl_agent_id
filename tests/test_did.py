# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the did:xrpl DID method parser.

These tests cover parsing + address <-> DID conversion only. Resolution
(wire-level XRPL calls) needs a testnet fixture and is tested separately.
"""

import pytest

from xrpl_agent_id.did import did_from_account, parse_did, DIDDocument


def test_did_from_account_mainnet():
    addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
    assert did_from_account(addr, "mainnet") == f"did:xrpl:1:{addr}"


def test_did_from_account_testnet():
    addr = "rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH"
    assert did_from_account(addr, "testnet") == f"did:xrpl:2:{addr}"


def test_did_from_account_unknown_network():
    with pytest.raises(ValueError, match="Unknown network"):
        did_from_account("rXXX", "fakenet")


def test_parse_did_valid():
    net, addr = parse_did("did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")
    assert net == 1
    assert addr == "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"


def test_parse_did_invalid_scheme():
    with pytest.raises(ValueError, match="Not a valid did:xrpl"):
        parse_did("did:eth:0xabc")


def test_parse_did_malformed():
    with pytest.raises(ValueError):
        parse_did("did:xrpl:notanumber:rXXX")


def test_did_document_from_json_w3c():
    doc_json = {
        "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        "verificationMethod": [
            {
                "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#master",
                "type": "EcdsaSecp256k1VerificationKey2019",
                "controller": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "publicKeyHex": "03930da11a7ab8f9a5e2e5ad1c0e46baa3a9c8e3e7e8e3e7e8e3e7e8e3e7e8e3e7",
            }
        ],
        "authentication": ["did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#master"],
    }
    doc = DIDDocument.from_json(doc_json)
    assert doc.id == doc_json["id"]
    assert len(doc.verification_method) == 1
    assert doc.verification_method[0]["type"] == "EcdsaSecp256k1VerificationKey2019"


def test_did_document_from_json_xls40_legacy():
    """XLS-40d samples use 'publicKey' instead of 'verificationMethod'. Accept both."""
    doc_json = {
        "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        "publicKey": [
            {
                "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#master",
                "type": "EcdsaKoblitzPublicKey",
                "controller": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "publicKeyHex": "03930da11a7ab8f9a5e2e5ad1c0e46baa3a9c8e3e7e8e3e7e8e3e7e8e3e7e8e3e7",
            }
        ],
    }
    doc = DIDDocument.from_json(doc_json)
    # legacy field name should map to verificationMethod transparently
    assert len(doc.verification_method) == 1
    assert doc.verification_method[0]["type"] == "EcdsaKoblitzPublicKey"


def test_to_json_emits_array_context():
    """W3C DID Core 1.0 requires @context as an array, not a bare string.
    decID got this wrong; we don't."""
    doc = DIDDocument(id="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")
    out = doc.to_json()
    assert isinstance(out["@context"], list)
    assert "https://www.w3.org/ns/did/v1" in out["@context"]


def test_to_json_normalizes_legacy_types_to_multikey():
    """XLS-40d samples + decID both use deprecated verification method types.
    We normalize to Multikey on output."""
    doc = DIDDocument(
        id="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        verification_method=[
            {
                "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#master",
                "type": "EcdsaKoblitzPublicKey",  # XLS-40d legacy
                "controller": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "publicKeyHex": "03" + "ab" * 32,  # 33 bytes compressed secp256k1
            }
        ],
    )
    out = doc.to_json()
    assert out["verificationMethod"][0]["type"] == "Multikey"
    # secp256k1-pub multicodec is 0xe701, encoded as base58btc with 'z' prefix
    assert out["verificationMethod"][0]["publicKeyMultibase"].startswith("z")
    # Length sanity: 33 raw bytes + 2-byte multicodec ≈ 35 bytes, base58 ≈ 47 chars
    assert 40 < len(out["verificationMethod"][0]["publicKeyMultibase"]) < 60


def test_to_json_preserves_canonical_multibase():
    """If caller already provides publicKeyMultibase, don't rewrite it."""
    existing = "z6MkiTBh2q6KB3VLMXt5h1Ap4n6eVv2W8Y6W4W3eXrXgFJX9"
    doc = DIDDocument(
        id="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        verification_method=[
            {
                "id": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#master",
                "type": "Multikey",
                "controller": "did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                "publicKeyMultibase": existing,
            }
        ],
    )
    out = doc.to_json()
    assert out["verificationMethod"][0]["publicKeyMultibase"] == existing


def test_did_document_size_warning():
    """XRPL DIDSet.DIDDocument field is 256 bytes max. Full docs blow past this."""
    import json as _json
    doc = DIDDocument(id="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh")
    # Many services — typical real-world doc
    for i in range(10):
        doc.service.append(
            {"id": f"did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh#svc-{i}",
             "type": "LinkedDomains",
             "serviceEndpoint": f"https://agent-{i}.example.com"}
        )
    serialized = _json.dumps(doc.to_json(), separators=(",", ":"))
    # Just demonstrate it exceeds the 256-byte on-ledger cap — callers
    # must put rich docs behind the URI field, not DIDDocument.
    assert len(serialized.encode("utf-8")) > 256
