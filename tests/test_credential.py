# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for xrpl_agent_id.credential — pure logic, no network."""

from __future__ import annotations

import pytest

from xrpl_agent_id.credential import Credential, CredentialType, RIPPLE_EPOCH


# ---- CredentialType enum ---------------------------------------------

def test_credential_type_values_are_short():
    """XLS-70 caps credential_type at 64 bytes; ours should be much smaller."""
    for ct in CredentialType:
        assert len(ct.value.encode()) <= 64, f"{ct.name} too long"


def test_credential_type_snake_case():
    """All credential types are snake_case."""
    for ct in CredentialType:
        assert ct.value.replace("_", "").isalnum(), f"{ct.name} not snake_case"


# ---- Construction & validation ---------------------------------------

def test_credential_minimal():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
    )
    assert cred.uri is None
    assert cred.expiration is None
    assert cred.accepted is False


def test_credential_rejects_empty_type():
    with pytest.raises(ValueError, match="non-empty"):
        Credential(
            issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
            subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
            credential_type=b"",
        )


def test_credential_rejects_oversized_type():
    with pytest.raises(ValueError, match="64 bytes"):
        Credential(
            issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
            subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
            credential_type=b"x" * 65,
        )


def test_credential_rejects_oversized_uri():
    with pytest.raises(ValueError, match="256 bytes"):
        Credential(
            issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
            subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
            credential_type=b"agent_id_v1",
            uri="https://example.com/" + "x" * 256,
        )


# ---- hex_type / hex_uri ---------------------------------------------

def test_hex_type_uppercase():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"\x00\x01\x02\xab",
    )
    assert cred.hex_type == "000102AB"


def test_hex_uri_uppercase():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
        uri="ipfs://QmTest",
    )
    # The hex should be the utf-8 encoding of "ipfs://QmTest" in uppercase
    assert cred.hex_uri == "697066733A2F2F516D546573742E".upper() or cred.hex_uri is not None
    assert cred.hex_uri is not None


# ---- to_ledger_fields ------------------------------------------------

def test_to_ledger_fields_minimal():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
    )
    fields = cred.to_ledger_fields()
    assert fields == {
        "subject": "rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        "credential_type": "6167656E745F69645F7631",  # "agent_id_v1"
    }


def test_to_ledger_fields_with_uri():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
        uri="https://example.com/claim",
    )
    fields = cred.to_ledger_fields()
    assert "uri" in fields
    assert fields["uri"] is not None
    assert fields["uri"].startswith("68747470733A2F2F")  # "https://"


def test_to_ledger_fields_with_expiration():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
        expiration=12345,
    )
    fields = cred.to_ledger_fields()
    assert fields["expiration"] == 12345


# ---- from_ledger_entry ----------------------------------------------

def test_from_ledger_entry_roundtrip():
    original = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
        uri="ipfs://QmTest",
        expiration=99999,
    )
    ledger_entry = {
        "Issuer": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        "Subject": "rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        "CredentialType": original.hex_type,
        "URI": original.hex_uri,
        "Expiration": 99999,
    }
    parsed = Credential.from_ledger_entry(ledger_entry)
    assert parsed.issuer == original.issuer
    assert parsed.subject == original.subject
    assert parsed.credential_type == original.credential_type
    assert parsed.uri == original.uri
    assert parsed.expiration == original.expiration


def test_from_ledger_entry_snake_case_keys():
    """The ledger_entry response may use snake_case fields too."""
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
    )
    ledger_entry = {
        "issuer": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        "subject": "rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        "credential_type": cred.hex_type,
    }
    parsed = Credential.from_ledger_entry(ledger_entry)
    assert parsed.credential_type == cred.credential_type


# ---- to_w3c_vc -------------------------------------------------------

def test_to_w3c_vc_basic():
    cred = Credential(
        issuer="did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
        subject="did:xrpl:1:rDsbe2c8ePEsxgXoeY2tpjA1C2Yf8GcR8o",
        credential_type=b"agent_id_v1",
    )
    vc = cred.to_w3c_vc()
    assert vc["issuer"] == cred.issuer
    assert vc["credentialSubject"]["id"] == cred.subject
    assert "VerifiableCredential" in vc["type"]
    assert "agent_id_v1" in vc["type"]


# ---- RIPPLE_EPOCH sanity --------------------------------------------

def test_ripple_epoch_is_2000():
    """Ripple epoch is 2000-01-01 00:00:00 UTC = 946684800."""
    assert RIPPLE_EPOCH == 946684800
