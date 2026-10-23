# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the AgentRegistry layer.

All tests are offline — they mock the XRPL JSON-RPC client so no network
is hit. Live verification is done in tests/test_integration_ledger_live.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from xrpl_agent_id.registry import (
    _LSF_CREDENTIAL_ACCEPTED,
    AgentRecord,
    AgentRegistry,
    Credential,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cred_obj(issuer: str, subject: str, cred_type_hex: str, accepted: bool, uri_hex: str = "") -> dict:
    """Build a fake XRPL Credential ledger object."""
    flags = _LSF_CREDENTIAL_ACCEPTED if accepted else 0
    obj = {
        "LedgerEntryType": "Credential",
        "Flags": flags,
        "Issuer": issuer,
        "IssuerNode": "0",
        "Subject": subject,
        "SubjectNode": "0",
        "CredentialType": cred_type_hex,
        "PreviousTxnID": "0" * 64,
        "PreviousTxnLgrSeq": 21123060,
        "index": "F" * 64,
    }
    if uri_hex:
        obj["URI"] = uri_hex
    return obj


def _did_obj(account: str, uri_hex: str, ledger: int = 21123064) -> dict:
    return {
        "LedgerEntryType": "DID",
        "Account": account,
        "Flags": 0,
        "OwnerNode": "0",
        "URI": uri_hex,
        "PreviousTxnID": "0" * 64,
        "PreviousTxnLgrSeq": ledger,
        "index": "E" * 64,
    }


def _signerlist_obj(signers: list[tuple[str, int]], quorum: int = 2) -> dict:
    return {
        "LedgerEntryType": "SignerList",
        "Flags": 0,
        "OwnerNode": "0",
        "SignerQuorum": quorum,
        "SignerEntries": [
            {"SignerEntry": {"Account": addr, "SignerWeight": w}}
            for addr, w in signers
        ],
        "PreviousTxnID": "0" * 64,
        "PreviousTxnLgrSeq": 21123070,
        "index": "D" * 64,
    }


def _make_client(credential_objs=None, did_objs=None, signer_objs=None):
    """Build a mock XRPL JsonRpcClient that returns the given objects by type."""
    by_type = {
        "credential": credential_objs or [],
        "did": did_objs or [],
        "signer_list": signer_objs or [],
    }
    client = MagicMock()
    def fake_request(req):
        r = MagicMock()
        r.result = {
            "account": req.account,
            "ledger_index": 21123064,
            "account_objects": by_type.get(req.type, []),
        }
        return r
    client.request.side_effect = fake_request
    return client


# ---------------------------------------------------------------------------
# AgentRecord
# ---------------------------------------------------------------------------

class TestAgentRecord:
    def _record(self, creds=None, controllers=None) -> AgentRecord:
        return AgentRecord(
            agent_address="rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty",
            agent_did="did:xrpl:2:rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty",
            controllers=controllers or ["rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty"],
            credentials=creds or [],
        )

    def test_summary_includes_address_and_credit_count(self):
        rec = self._record()
        assert "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty" in rec.summary()
        assert "0/0" in rec.summary()

    def test_accepted_and_pending_split(self):
        c1 = MagicMock()
        c1.credential_type = b"KYC"
        c1.accepted = True
        c2 = MagicMock()
        c2.credential_type = b"EVAL"
        c2.accepted = False
        rec = self._record(creds=[c1, c2])
        assert rec.accepted_credential_types == [b"KYC"]
        assert rec.pending_credential_types == [b"EVAL"]

    def test_summary_with_multisig_controllers(self):
        rec = self._record(controllers=["rA", "rB", "rC"])
        assert "3 controller(s)" in rec.summary()


# ---------------------------------------------------------------------------
# AgentRegistry — single-sig account
# ---------------------------------------------------------------------------

class TestAgentRegistrySingleSig:
    def test_resolve_classic_address(self):
        # Subject with one accepted credential from issuer rISS.
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        client = _make_client(credential_objs=[cred])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            rec = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.agent_address == "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty"
        assert rec.agent_did == "did:xrpl:2:rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty"
        assert rec.controllers == ["rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty"]  # single-sig → self-controller
        assert len(rec.credentials) == 1
        assert rec.credentials[0].credential_type == b"KYC"
        assert rec.credentials[0].accepted is True
        assert rec.credentials[0].issuer == "did:xrpl:2:rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v"

    def test_resolve_did_form(self):
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        client = _make_client(credential_objs=[cred])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            rec = reg.resolve("did:xrpl:2:rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.agent_address == "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty"

    def test_no_credentials(self):
        client = _make_client()
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.credentials == []
        assert rec.accepted_credential_types == []

    def test_pending_credential_not_in_accepted(self):
        cred_accepted = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        cred_pending = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4556414C", accepted=False)  # b"EVAL"
        client = _make_client(credential_objs=[cred_accepted, cred_pending])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert len(rec.credentials) == 2
        assert b"KYC" in rec.accepted_credential_types
        assert b"EVAL" in rec.pending_credential_types

    def test_did_uri_hex_decoded(self):
        # "https://example.com/did" in hex
        uri_hex = "68747470733a2f2f6578616d706c652e636f6d2f646964"
        did = _did_obj("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", uri_hex)
        client = _make_client(did_objs=[did])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.did_uri == "https://example.com/did"

    def test_invalid_identifier_raises(self):
        with pytest.raises(ValueError, match="must be a did:xrpl"):
            AgentRegistry(network="testnet").resolve("not-a-did")


# ---------------------------------------------------------------------------
# AgentRegistry — multi-sig account
# ---------------------------------------------------------------------------

class TestAgentRegistryMultiSig:
    def test_multi_sig_controllers_listed(self):
        sl = _signerlist_obj([("rn8aFRNJGkSvLQmzhPQxV2eeFXJaWvunTL", 1), ("rMNE4FcNgx7VJqyCkk4hYs5MiLoXy6maXo", 1)], quorum=2)
        client = _make_client(signer_objs=[sl])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.controllers == ["rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "rn8aFRNJGkSvLQmzhPQxV2eeFXJaWvunTL", "rMNE4FcNgx7VJqyCkk4hYs5MiLoXy6maXo"]

    def test_controllers_deduplicated(self):
        sl = _signerlist_obj([("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", 1), ("rMNE4FcNgx7VJqyCkk4hYs5MiLoXy6maXo", 1)], quorum=1)
        client = _make_client(signer_objs=[sl])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        # rSUBJ appears once even though listed as a signer too.
        assert rec.controllers.count("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty") == 1


# ---------------------------------------------------------------------------
# AgentRegistry — cache behavior
# ---------------------------------------------------------------------------

class TestAgentRegistryCache:
    def test_cache_hits_within_instance(self):
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        client = _make_client(credential_objs=[cred])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            rec1 = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            rec2 = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            assert rec1 is rec2  # identity check
            # Only one batch of queries should have hit the mock client.
            call_count_first = client.request.call_count
            rec3 = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            assert rec3 is rec1
            assert client.request.call_count == call_count_first

    def test_use_cache_false_forces_reresolve(self):
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        client = _make_client(credential_objs=[cred])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            rec1 = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            rec2 = reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", use_cache=False)
            assert rec1 is not rec2
            assert rec1.agent_address == rec2.agent_address

    def test_invalidate_drops_one_entry(self):
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        client = _make_client(credential_objs=[cred])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            reg.invalidate("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            assert "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty" not in reg._cache

    def test_clear_cache_drops_all(self):
        client = _make_client()
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet")
            reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            reg.resolve("rPFYgaphWwuCQqZDRF3CvkQVikwvReDqV6")
            reg.clear_cache()
            assert reg._cache == {}

    def test_cache_disabled_does_not_store(self):
        client = _make_client()
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            reg = AgentRegistry(network="testnet", cache=False)
            reg.resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
            assert reg._cache == {}


# ---------------------------------------------------------------------------
# last_activity_ledger
# ---------------------------------------------------------------------------

class TestLastActivity:
    def test_uses_max_ledger_index(self):
        cred = _cred_obj("rwMwYv3824fB5K7dJiXTRtr9ix8H6b5N6v", "rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "4B5943", accepted=True)
        cred["PreviousTxnLgrSeq"] = 21123060
        did = _did_obj("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty", "68747470733a2f2f6578616d706c652e636f6d", ledger=21123999)
        client = _make_client(credential_objs=[cred], did_objs=[did])
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.last_activity_ledger == 21123999

    def test_no_objects_no_ledger(self):
        client = _make_client()
        with patch("xrpl_agent_id.registry.get_client", return_value=client):
            rec = AgentRegistry(network="testnet").resolve("rHk9ELHHvU8sXrWhwvaktq4QJ8sXya2aty")
        assert rec.last_activity_ledger is None
