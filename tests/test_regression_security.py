# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Regression tests for two silent-pass security bugs.

Both shipped and both were invisible to the suite:

1. ``AgentRegistry._parse_credential_obj`` never carried the ledger's
   ``Expiration`` onto the ``Credential``. Because ``authorization.py`` gates on
   ``if cred.expiration is not None``, the ``CREDENTIAL_EXPIRED`` branch was
   unreachable for every credential parsed off the ledger -- the headline fix of
   v0.4.0 did not fire in the real path. The pre-existing tests in
   ``test_authorization.py`` build ``Credential`` objects directly, so they
   exercised the gate and never the wiring.

2. ``identity.has_credential()`` and ``authority._check_credential()`` tested only
   that a ledger node existed. XLS-70 leaves ``Flags`` at 0 until the subject
   signs, so an issuer-created credential nobody accepted counted as held and
   satisfied a trust requirement.

These tests assert on the *wiring*, which is what was broken.
"""
import time

import pytest

from xrpl_agent_id.authorization import ReasonCode
from xrpl_agent_id.credential import credential_is_accepted
from xrpl_agent_id.registry import AgentRegistry

RIPPLE_EPOCH = 946684800
NOW = int(time.time()) - RIPPLE_EPOCH
LSF_CREDENTIAL_ACCEPTED = 0x00010000

ISSUER = "rhY1XZ9ds6UNm5w2SHBna7AR3S7efFcFKX"
SUBJECT = "rJuAKVdfG3Wb5hy4L6iYVkaFWj1EyHcHGv"


@pytest.fixture
def registry():
    reg = AgentRegistry.__new__(AgentRegistry)   # skip __init__: no live network needed
    reg.network = "mainnet"
    return reg


def _ledger_obj(**over):
    obj = {
        "CredentialType": b"KYC".hex().upper(),
        "Issuer": ISSUER,
        "Subject": SUBJECT,
        "Flags": LSF_CREDENTIAL_ACCEPTED,
    }
    obj.update(over)
    return obj


class TestExpirationSurvivesRegistryParse:
    """Bug 1 -- the credential-expiry gate must be reachable."""

    def test_expired_expiration_is_carried_through(self, registry):
        past = NOW - 10_000
        cred = registry._parse_credential_obj(_ledger_obj(Expiration=str(past)))
        assert cred.expiration == past, (
            "registry dropped the ledger Expiration; CREDENTIAL_EXPIRED can never fire"
        )

    def test_future_expiration_is_carried_through(self, registry):
        future = NOW + 86_400
        cred = registry._parse_credential_obj(_ledger_obj(Expiration=str(future)))
        assert cred.expiration == future

    def test_zero_means_never_expires(self, registry):
        # XLS-70 treats Expiration == 0 as "no expiry", which must not read as
        # "expired at the epoch".
        cred = registry._parse_credential_obj(_ledger_obj(Expiration="0"))
        assert cred.expiration is None

    def test_absent_expiration_is_none(self, registry):
        cred = registry._parse_credential_obj(_ledger_obj())
        assert cred.expiration is None

    def test_unparseable_expiration_does_not_raise(self, registry):
        cred = registry._parse_credential_obj(_ledger_obj(Expiration="not-a-number"))
        assert cred.expiration is None


class TestAcceptanceIsRequired:
    """Bug 2 -- existence is not acceptance."""

    @pytest.mark.parametrize(
        "node,expected",
        [
            ({"Flags": 0}, False),
            ({"Flags": LSF_CREDENTIAL_ACCEPTED}, True),
            ({"Flags": LSF_CREDENTIAL_ACCEPTED | 0x80000000}, True),
            ({"flags": LSF_CREDENTIAL_ACCEPTED}, True),
            ({}, False),
            ({"Flags": "garbage"}, False),
            ({"Flags": None}, False),
            ("present", False),          # the old test fixture shape
            (None, False),
        ],
    )
    def test_credential_is_accepted(self, node, expected):
        assert credential_is_accepted(node) is expected

    def test_unaccepted_credential_is_not_accepted(self, registry):
        cred = registry._parse_credential_obj(_ledger_obj(Flags=0))
        assert cred.accepted is False

    def test_accepted_credential_is_accepted(self, registry):
        cred = registry._parse_credential_obj(_ledger_obj(Flags=LSF_CREDENTIAL_ACCEPTED))
        assert cred.accepted is True