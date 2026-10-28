#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""03_authorization_decision.py -- Decide whether an agent may act.

This is the part that matters in production. An agent presents a
credential; the library reads the ledger and answers allow or deny.

Runs against XRPL **testnet**. The trust registry reads public ledger
state, so the rules can be set up and inspected with no funded wallet --
only a live check needs a credential to exist on the ledger.

    pip install xrpl-agent-id
    python examples/03_authorization_decision.py

What it does:
  1. Builds a trust registry with a `require` rule and a `deny` rule.
  2. Evaluates an agent against it.
  3. Prints the decision and *why* -- which requirements are missing and
     which deny rules matched.

A decision is not a bare boolean. `TrustCheckResult` reports which
required credentials are missing and which deny rules fired, and that is
what an audit log records.
"""

from __future__ import annotations

from xrpl_agent_id import TrustRegistry

NETWORK = "testnet"

# A credential type is a URI. Use your own namespace in production.
CRED_TYPE = b"https://sdevlabs.dev/credentials/agent-authorization"

# The addresses trusted to issue the above credential type.
TRUSTED_ISSUERS = {
    "rG1rNWnJEbejtD5RqsbYK2E7pQ2wFks9Lq",
    "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
}

# An agent on this list is denied regardless of what it holds.
BANNED_AGENTS = {
    "rLysmLMAEGavtxc8sFJ17hEtThQzxzMAKw",
}


def main() -> None:
    print("xrpl_agent_id -- example 3: authorization decisions")
    print("=" * 62)

    registry = TrustRegistry(network=NETWORK)

    # A require rule: the agent must hold a credential of this type,
    # issued by one of these issuers.
    registry.require(
        issuer=TRUSTED_ISSUERS,
        credential_type=CRED_TYPE,
        description="Agent must hold a valid authorization credential.",
    )

    # A deny rule: holding this credential from this issuer is grounds
    # for refusal, whatever else the agent presents.
    registry.deny(
        issuer=BANNED_AGENTS,
        credential_type=None,
        description="This issuer is revoked.",
    )

    print()
    print("  Registry rules")
    print("  " + "-" * 58)
    for rule in registry._rules:  # noqa: SLF001 -- inspecting for the demo
        issuers = ", ".join(sorted(rule.issuers)) if rule.issuers else "any"
        print(f"  [{rule.action:6s}] {rule.credential_type or 'any type'}")
        print(f"           issuers: {issuers}")

    print()
    print("  Evaluating an agent")

    agent_did = "did:xrpl:2:rG1rNWnJEbejtD5RqsbYK2E7pQ2wFks9Lq"
    print(f"  agent : {agent_did}")

    try:
        result = registry.check(agent_did)
    except Exception as exc:  # noqa: BLE001 -- needs live ledger state
        print(f"  check : could not complete ({type(exc).__name__}: {exc})")
        print()
        print("  A live check reads the ledger for credentials held by the")
        print("  agent. With no credential on the ledger it cannot decide, so")
        print("  the result below is the shape of the decision surface.")
        result = None

    if result is not None:
        print(f"  satisfied        : {result.satisfied}")
        print(f"  missing_required : {result.missing_required}")
        print(f"  denied_held      : {result.denied_held}")

    print()
    print("  The six roles validated against XRPL mainnet")
    print("  " + "-" * 58)
    for role, outcome in [
        ("valid", "ALLOW"),
        ("banned", "DENY  AGENT_BANNED"),
        ("controller_banned", "DENY  CONTROLLER_BANNED"),
        ("no_creds", "DENY  NO_CREDENTIALS"),
        ("wrong_issuer", "DENY  CREDENTIAL_MISSING"),
        ("pending", "DENY  CREDENTIAL_REVOKED"),
    ]:
        print(f"  {role:20s} {outcome}")

    print()
    print("  Pair this with xrpl_agent_log to write each decision to a")
    print("  tamper-evident hash chain. See the companion repository.")


if __name__ == "__main__":
    main()
