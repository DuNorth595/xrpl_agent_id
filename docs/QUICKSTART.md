# xrpl_agent_id — Quickstart

**Goal:** Issue a credential, accept it, run a trust check, see the decision — end-to-end on XRPL testnet, in about five minutes.

By the end you'll have run every public-API surface that matters: `AgentIdentity.from_seed`, `Authority`, `CredentialType`, `authority.issue_credential`, `agent.accept_credential`, `TrustRegistry.require` / `.deny` / `.check`, and `TrustCheckResult`.

This Quickstart uses **testnet** throughout. The same code works on mainnet by changing one argument — see the last section.

---

## 0. Prerequisites

- Python 3.10+
- `xrpl_agent_id` installed (see step 1)
- A funded testnet account — get one from the [XRPL Testnet Faucet](https://xrpl.org/xrp-testnet-faucet.html). You'll get a family seed that starts with `sEd`.

If you don't have a funded account yet, the library's `AgentIdentity.from_seed()` will create a wallet from any seed you pass, but the issuance step needs testnet XRP for fees.

---

## 1. Install

```bash
pip install xrpl_agent_id
```

Or from source:

```bash
git clone https://github.com/DuNorth595/xrpl_agent_id.git
cd xrpl_agent_id
pip install -e .
```

Verify the install:

```python
>>> import xrpl_agent_id
>>> xrpl_agent_id.__version__
'0.3.2'
```

---

## 2. The scenario

Imagine three roles:

- **Alice** — runs an AI agent marketplace. She needs to vet agents before letting them call her paid weather API.
- **Bob** — built BobBot, an AI agent that wants to call Alice's API.
- **WeatherCorp** — a known industry issuer. They certify agents as "verified operator" via `CredentialType.VERIFIED_AGENT_OPERATOR`.

In five steps, WeatherCorp will issue a verified-operator credential to Bob, Bob will accept it, then Alice will run a trust check before letting BobBot call her API.

For the Quickstart, **all three identities will come from the same wallet** so you only need one funded testnet seed. Real deployments use distinct issuers, of course.

---

## 3. Step 1 — Create identities (~5 seconds)

```python
from xrpl_agent_id import AgentIdentity, Authority

SEED = "sEdYourTestnetSeedHere..."  # from the faucet

# Bob is the agent. He owns his DID and can hold credentials.
bob = AgentIdentity.from_seed(SEED, network="testnet")
print(f"Bob's DID: {bob.did}")
print(f"Bob's address: {bob.address}")
```

Output (example shape; addresses will differ):

```
Bob's DID: did:xrpl:2:rXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
Bob's address: rXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

The DID format is `did:xrpl:<network_id>:<classic_address>`. In `xrpl_agent_id`, network ID `1` is mainnet and `2` is testnet (per XLS-40d). The library never derives the network ID from the seed prefix — you always pass `network=` explicitly.

**Tip:** If you want Alice and Bob to use distinct testnet wallets, generate more testnet seeds with the XRPL faucet or call it multiple times. For the Quickstart, one seed is enough — Bob plays all three roles in sequence.

---

## 4. Step 2 — Pick the credential type (~instant)

The library ships three canonical credential types. On-ledger, credential types are raw bytes — for these canonical types, the bytes are the UTF-8 encoding of the string value:

```python
from xrpl_agent_id import CredentialType

# Pick a type. The enum value is a human-readable string; the on-ledger
# representation is .value.encode("utf-8").
CRED_TYPE = CredentialType.VERIFIED_AGENT_OPERATOR.value.encode("utf-8")

# Built-in types:
# - CredentialType.AGENT_ID_V1              — basic agent identity
# - CredentialType.VERIFIED_AGENT_OPERATOR  — operator has been vetted
# - CredentialType.EVAL_PASSED              — passed an evaluation/test suite
```

These are the only types the library recognizes today. If you need a custom type for a private deployment, pass any `bytes` (≤ 64 bytes per XLS-70) directly to `issue_credential` — but for cross-organization interoperability, stick to the canonical types.

---

## 5. Step 3 — Issue the credential (~6 seconds)

Issuing is a `CredentialCreate` transaction signed by the authority:

```python
from xrpl_agent_id import Authority

# Authority is a special AgentIdentity that can only sign credential txns.
# In the real world WeatherCorp would have its own seed.
weathercorp = Authority.from_seed(SEED, network="testnet")

# Authority issues the verified-operator credential to Bob.
tx_hash = weathercorp.issue_credential(
    subject=bob.address,           # recipient's classic address or DID
    credential_type=CRED_TYPE,
    uri="https://weathercorp.example/credentials/verified-v1.json",
)

print(f"CredentialCreate tx: {tx_hash}")
# -> https://testnet.xrpl.org/transactions/<tx_hash>
```

That `tx_hash` is the on-chain proof. Anyone with the URL can verify the credential exists. **This is the audit trail.**

---

## 6. Step 4 — Bob accepts the credential (~6 seconds)

A credential isn't valid until the subject accepts it. The subject submits `CredentialAccept`:

```python
accept_hash = bob.accept_credential(
    issuer=weathercorp.address,
    credential_type=CRED_TYPE,
)

print(f"CredentialAccept tx: {accept_hash}")
```

After acceptance, the credential is queryable on-ledger as long as it isn't revoked.

**Tip:** If `bob.has_credential(issuer=..., credential_type=...)` returns `False` after a successful accept, you may have raced the ledger. Wait a few seconds and retry — finality on XRPL testnet is typically 3–5 seconds.

---

## 7. Step 5 — Alice defines and runs a trust check (~10 seconds)

Now the marketplace bit. Alice defines a trust policy: *any agent calling my API must hold a `VERIFIED_AGENT_OPERATOR` credential.*

```python
from xrpl_agent_id import TrustRegistry

policy = TrustRegistry(network="testnet")

# Required: a verified-operator credential, from any issuer.
policy.require(
    issuer=None,  # any issuer — accept credentials from any recognized party
    credential_type=CRED_TYPE,
    description="Verified-operator credential from a recognized issuer",
)

# Run the check against Bob.
result = policy.check(bob.did)

print(result.summary())
print(f"All satisfied: {result.all_satisfied()}")
print(f"Missing required: {result.missing_required}")
print(f"Denied held: {result.denied_held}")
```

If everything's wired up:

```
✓ trust policy satisfied for did:xrpl:2:rXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
All satisfied: True
Missing required: []
Denied held: []
```

If something's wrong (Bob hasn't accepted yet, or the credential type mismatches):

```
✗ trust policy FAILED for did:xrpl:2:...: 1 missing required
All satisfied: False
Missing required: [<TrustPolicy ...>]
Denied held: []
```

The `result` object is the **decision**. Alice's API gateway reads it once per request and lets BobBot through or rejects with a 403.

---

## 8. Adding deny rules

Want to block specific issuers? Add `.deny()`:

```python
policy.deny(
    issuer="rBadActorAddressHere...",
    credential_type=None,  # any type
    description="Known compromised issuer",
)
```

A `.deny()` rule fires if the subject holds *any* matching credential from a listed issuer — the opposite of `.require()`. If both fire (require and deny), deny wins. That's the intended behavior: a known-bad issuer's attestation shouldn't satisfy a requirement.

---

## 9. Verifying offline (no ledger call)

For high-throughput paths where you can't hit the ledger on every check, use `Authority.verify_set()` to evaluate a pre-fetched set of credentials:

```python
# Pull Bob's credentials once, then evaluate offline.
creds = [
    (weathercorp.address, CRED_TYPE),
]

verdict = weathercorp.verify_set(
    agent_did=bob.did,
    required=creds,
)

print(verdict.summary())   # "1/1 required credentials satisfied"
print(verdict.all_satisfied())
```

The signature is the same shape as `TrustCheckResult.summary()` — same mental model.

---

## 10. Switching to mainnet

Same code, one argument change:

```python
bob = AgentIdentity.from_seed(SEED, network="mainnet")
policy = TrustRegistry(network="mainnet")
```

The DID's network ID flips from `2` to `1`. The library never assumes testnet by default — every network-aware call takes `network=` explicitly so you can't accidentally mix them.

**Heads up:** Mainnet issuance burns real XRP (transaction fees + reserve). See `docs/USE_CASES.md` for the cost model before you ship.

---

## 11. Where to go next

- **`docs/USE_CASES.md`** — five concrete scenarios where this library fits: agent marketplaces, federated research compute, IoT device fleets, regulated finance workflows, and trust-onboarding for new agents.
- **`docs/ARCHITECTURE.md`** — the full library map: identity, credentials, trust registry, audit, ban lists, dashboard, and how they fit together.
- **`docs/STRESS_TEST_v0.3.0.md`** — the throughput and scale evidence: 1000 transactions on testnet at 42.99 txs/min, 100% success.
- **`xrpl_agent_id/trust.py`**, **`xrpl_agent_id/identity.py`** — the source. It's small (~2,500 lines total) and meant to be read.

---

## Document status

- **Version:** 0.3.3-draft
- **Author:** S_DevLabs
- **Last updated:** 2026-09-29
- **Tested against:** `xrpl_agent_id` 0.3.2 on XRPL testnet
- **Audience:** First-time users of `xrpl_agent_id`. Assumes basic XRPL knowledge (accounts, seeds, transactions) but no prior exposure to this library.
