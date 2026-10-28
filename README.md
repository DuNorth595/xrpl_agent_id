# xrpl_agent_id

Give an AI agent a verifiable, on-chain identity on the XRP Ledger — a `did:xrpl`
identifier, a W3C DID Document, and XLS-70 credential objects you can issue,
accept, and verify. One Python package, no extra infrastructure.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![xrpl-py 5.0+](https://img.shields.io/badge/xrpl--py-5.0+-black.svg)](https://pypi.org/project/xrpl-py/)
[![SPDX: REUSE](https://img.shields.io/badge/SPDX-REUSE--compliant-brightgreen)](https://reuse.software/)
[![CI](https://github.com/DuNorth595/xrpl_agent_id/actions/workflows/ci.yml/badge.svg)](https://github.com/DuNorth595/xrpl_agent_id/actions/workflows/ci.yml)

<p align="center">
  <img src="docs/social-preview.jpg" alt="xrpl_agent_id — social preview" width="600">
</p>

---

## What this is

A small Python library that gives an AI agent an XRPL-native identity:

| Piece | What it is |
|---|---|
| `did:xrpl:<network-id>:<address>` | A W3C DID that resolves from the public ledger — no registrar, no domain, no renewal |
| DID Document | W3C DID Core 1.0 output (`verificationMethod` + `Multikey`), resolved via `ledger_entry` |
| XLS-70 credential | An issuer-attested credential object stored on-ledger, verifiable by anyone |
| Authorization policy | An allow/deny decision with 8 stable reason codes, evaluated against an agent's credentials |
| Audit record | Every decision written to SQLite, optionally mirrored to the XRPL |

**Not** a key-management system, wallet, privacy layer, or replacement for
OIDC/SAML. Credentials on XRPL are public. Bring your own keys — use
`xrpl-py`'s `Wallet`.

**Status:** v0.5.2, MIT, mainnet-validated. The credential lifecycle was
verified against XRPL mainnet on 2026-10-28 — see
[Mainnet readiness](#mainnet-readiness).

---

## Installation and supported versions

Not published to PyPI. Install from source:

```bash
git clone https://github.com/DuNorth595/xrpl_agent_id
cd xrpl_agent_id
pip install -e ".[dev]"
```

Requires **Python 3.10+**. The `xrpl-py` dependency is pinned to
`>=5.0,<6` — the upper bound is deliberate, so the package cannot resolve
against two major versions of its only dependency.

| | Supported |
|---|---|
| Python | 3.10, 3.11, 3.12, 3.13 (CI matrix) |
| `xrpl-py` | 5.0 – 5.x |
| Networks | XRPL mainnet, testnet, and devnet |

The package ships type annotations and a PEP 561 `py.typed` marker, so
mypy and pyright use them.

---

## Quick start

```python
from xrpl_agent_id import AgentIdentity, Authority

# Issuer: a service that vouches for agents (an eval registry, an operator, etc.)
issuer = Authority.from_seed("sEd...", network="testnet")

# Subject: the AI agent being verified
agent = AgentIdentity.from_seed("sEd...", network="testnet")

# Issuer attests: "this agent passed eval X"
issuer.issue_credential(
    subject=agent.did,
    credential_type=b"agent-eval-passed-v1",
    uri="ipfs://Qm...",  # full claim details off-chain
)

# Agent accepts it — its own signature, on its own account
agent.accept_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)

# Anyone can verify — no keys, no registry, just the public ledger
assert agent.has_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)
```

---


## Runnable examples

Three scripts in [`examples/`](examples/). Each prints what it did and what
to do next.

```bash
# not on PyPI yet -- install from GitHub
pip install "xrpl-agent-id @ git+https://github.com/DuNorth595/xrpl_agent_id.git@v0.5.2"
python examples/01_create_did.py
```

| Script | What it shows | Needs a funded wallet? |
|---|---|---|
| [`01_create_did.py`](examples/01_create_did.py) | Derive a `did:xrpl` and resolve it | No |
| [`02_publish_did_set.py`](examples/02_publish_did_set.py) | Publish a DIDSet, then resolve the DID | Yes |
| [`03_authorization_decision.py`](examples/03_authorization_decision.py) | Allow/deny decisions with reason codes | No |

`01` and `03` run immediately against XRPL testnet with no setup.

## Use it from an AI agent (MCP)

[`xrpl_did_mcp`](https://github.com/DuNorth595/xrpl_did_mcp) exposes these
operations to any MCP client — Claude Desktop, Claude Code, or anything else
that speaks the protocol.

```bash
# not on PyPI yet -- install from GitHub
pip install "xrpl-did-mcp @ git+https://github.com/DuNorth595/xrpl_did_mcp.git"
```

```json
{
  "mcpServers": {
    "xrpl-did": {
      "command": "xrpl-did-mcp",
      "env": { "XRPL_MCP_NETWORK": "testnet" }
    }
  }
}
```

Five tools: `derive_did`, `resolve_did`, `check_authorization`,
`ledger_lookup`, and `publish_did_set`. Four are read-only.

## Usage

Three things you do with this library. All three are two ledger
transactions or fewer, and all three are verifiable by anyone with a
public `ledger_entry` call.

### 1. Issue a credential

An issuer attests something about an agent. The credential is an
on-ledger XLS-70 object.

```python
from xrpl_agent_id import Authority, AgentIdentity

issuer = Authority.from_seed(issuer_seed, network="mainnet")
agent = AgentIdentity.from_seed(agent_seed, network="mainnet")

issuer.issue_credential(
    subject=agent.address,                      # the agent's r-address
    credential_type=b"agent-eval-passed-v1",
    uri="ipfs://Qm...",                         # full claim details, off-chain
)
```

`issue_credential()` submits a `CredentialCreate` and returns the
`Credential` object. The issuer holds the credential's 0.2 XRP owner
reserve until the subject accepts it.

**The cheapest path.** `issue_credential_cheapest()` skips the optional
DID object — XLS-70 makes a DID *recommended*, not required — for one
transaction instead of two, and 1.2 XRP of lockup instead of 1.4. Use it
unless you specifically need a `DIDSet`.

```python
issued = issuer.issue_credential_cheapest(
    subject=agent.address,
    credential_type=b"agent-eval-passed-v1",
)
```

### 2. Accept and verify

The subject accepts on its own account — its own signature, its own
consent — and then anyone can verify against the public ledger.

```python
agent.accept_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)

# Verify from the subject's side
assert agent.has_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)

# Or verify from anyone else's side — no keys, no registry, no API key
from xrpl_agent_id import resolve_did
doc = resolve_did(agent.did, network="mainnet")
```

`has_credential()` reads the ledger and checks the `lsfAccepted` flag. An
unaccepted credential does not count as held — XLS-70 says a credential
"should not be considered valid until it has been accepted," and the
library enforces that rather than assuming it.

### 3. Check whether an agent is allowed to do something

An identity says *who*. The authorization layer says *what it may do*.

```python
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext
from xrpl_agent_id.banned import Ban, BannedAgentRegistry

bans = BannedAgentRegistry(network="mainnet")
bans.add(Ban(address="rPzMYxDubFsbSh7bS9evarrh9Y5U1n9v13", reason="abuse report #4421", added_by="ops"))

policy = AuthorizationPolicy(network="mainnet", bans=bans)
policy.require_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)

decision = policy.evaluate(agent.address, context=RequestContext(resource="/api/transfer"))
if not decision.allow:
    for reason in decision.reasons:
        print(reason.code.name, reason.detail)
```

**This is a read.** `evaluate()` resolves the agent's ledger state and
evaluates it against the policy — it submits no transaction and costs
nothing. The 8 reason codes are `OK`, `AGENT_BANNED`, `CONTROLLER_BANNED`,
`NO_CREDENTIALS`, `CREDENTIAL_MISSING`, `CREDENTIAL_REVOKED`,
`CREDENTIAL_TYPE_DENIED`, and `CREDENTIAL_EXPIRED`.

### 4. Sign without holding the key

`WalletProvider` is the interface for a KMS, HSM, or hardware wallet that
never reveals the private key.

```python
from xrpl_agent_id import AgentIdentity, ExternalSignerProvider

def sign_with_kms(message_hex: str) -> str:
    """Sign the XRPL signing bytes with a KMS, HSM, or hardware wallet.

    A KMS signs a DIGEST rather than a pre-hashed message, so hash the
    bytes the way your vendor's client expects. AWS KMS does not support
    Ed25519 -- use a secp256k1 key there and expect an `EC` public-key
    prefix rather than `ED`.
    """
    digest = hashlib.sha256(bytes.fromhex(message_hex)).digest()
    return kms_client.sign(
        KeyId=key_id, Message=digest, MessageType="DIGEST",
    )["Signature"].hex()

provider = ExternalSignerProvider(
    classic_address="rYourAccountAddress",   # the KMS-backed account
    public_key="EC...",                      # compressed XRPL public key
    signer=sign_with_kms,
)

agent = AgentIdentity(provider=provider, network="mainnet")
print(agent.did)   # did:xrpl:1:rYourAccountAddress
```

The provider signs the transaction; the library handles the rest. Because
a provider signs *before* submission, the library re-autofills and
re-signs on every retry — the stock `submit_and_wait` retry is not safe
for a pre-signed transaction.

`WalletProvider` is structural: three members — `classic_address`,
`public_key`, `sign(tx)` — and any object with them satisfies it, so the
same shape can be implemented in another language or behind any signer.

### 5. Optionally mirror decisions on-chain

```python
from xrpl_agent_id.audit import AuditLog, XRPLMirror

audit = AuditLog(
    db_path="audit.db",
    xrpl_mirror=XRPLMirror.from_seed(mirror_seed, network="mainnet"),
    mirror_on_deny_only=True,          # default
)
audit.record(decision)
```

Every decision goes to SQLite. The on-chain mirror is **optional and off
by default** — with `xrpl_mirror=None` nothing touches the ledger. When
you do enable it, `mirror_on_deny_only=True` means only denials are
anchored, the mirror runs on **its own key** (so its cost is yours, not
the counterparty's), and `anchor_batch()` puts up to 1,000 decisions in
one transaction instead of one per decision.

---

## The primitives it wraps

XRPL shipped the protocol-level pieces between 2022 and 2025. What was missing
was a maintained, well-tested Python package that treats them as agent identity
rather than as raw ledger objects.

| Primitive | XRPL standard | What it gives an agent |
|---|---|---|
| `DIDSet` / `DIDDelete` | [XLS-40d](https://github.com/XRPLF/XRPL-Standards/tree/master/XLS-0040-decentralized-identity) | A `did:xrpl:1:rXXX…` identifier, on-ledger, no registrar |
| `CredentialCreate` / `Accept` / `Delete` | [XLS-70d](https://xls.xrpl.org/xls/XLS-0070-credentials.html) | Issuer-attested credential objects, stored on-ledger |
| `DepositPreauth` | XLS-70d extends it | Gates transactions on a *credential*, not just an address |
| `MPTokenIssuanceCreate` | XLS-33 | Optional reputation / capability tokens |

### A note on terminology

XLS-70 is an XRPL-native credential mechanism. It is **aligned with, but not
identical to**, the [W3C Verifiable Credentials Data Model](https://www.w3.org/TR/vc-data-model-2.0/).
XLS-70d §1.2 states plainly that its terms "do not directly match" the W3C VC
definitions — the on-ledger `Credential` object is a credential *mechanism*, not
a W3C VC JSON-LD document with a `proof` block.

The **DID Document side is genuinely W3C-compliant**: W3C DID Core 1.0 output
with `verificationMethod` and `Multikey`, array `@context`, correct multicodec
and base58btc multibase.

If you need full W3C VC Data Model documents, this library is the wrong tool.
If you need on-ledger credentials that XRPL-native applications can read
directly, it is the right one.

---

## Why XRPL rather than ERC-8004?

[ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) is the best-known agent
identity standard today — live on Ethereum mainnet, with hundreds of thousands
of registered agent identities *(registration count as of 2026-10-27)*. It is a
reasonable choice, and for some teams it is the right one. Here is the
substantive difference.

| | ERC-8004 | XLS-40d + XLS-70d |
|---|---|---|
| **Identity primitive** | An ERC-721 NFT in a registry contract | A `DIDSet` ledger object |
| **Transferable?** | Yes — it's an NFT, so it can be sold | No — key-controlled; only the holder can sign |
| **What carries the meaning?** | The registry contract's state | Attested credential objects issued by a named issuer |
| **Can a transaction be gated on it?** | Not natively | Yes — `DepositPreauth` with a credential descriptor |
| **Lifetime** | Tied to the registry contract's existence | Tied to the account and the ledger |
| **Revocation** | Registry-dependent | Issuer or subject deletes the `Credential` object |
| **Cost of the identity itself** | An NFT mint | 10 drops per transaction, plus 0.2 XRP owner reserve per ledger object |
| **Ecosystem** | Ethereum + 14 chains, large | XRPL mainnet, smaller |

The short version: **ERC-8004 gives you a transferable token. XRPL gives you a
non-transferable, key-controlled identity with issuer-attested credentials that
can gate transactions.** If your use case involves an agent that must prove it
holds a particular credential in order to transact, the `DepositPreauth`
extension is the capability ERC-8004 does not have.

Neither is universally better. Pick based on where your counterparties and your
settlement rails already live.

*(ERC-8004 registration counts above were read from public trackers on
2026-10-27 and will change.)*

---

## Comparing approaches

Other ways to give an agent identity on XRPL, and how this library relates to
them. This is an honest comparison, not a scoreboard — several of these are
good projects, and one is by the author of the very standard this library
implements.

> **Data as of 2026-10-27.** Star counts, forks, licenses, and last-push dates
> below were read from the GitHub API on that date and will drift. The
> *descriptions* — what each project actually does — are the durable part.

| Project | What it does | Language | License | Notes | Last push |
|---|---|---|---|---|---|
| **`xrpl_agent_id`** (this) | DID + XLS-70 credentials + authorization policy + audit | Python | MIT | Maintained Python implementation; credential-gating and trust layer | active |
| [`mvadari/xrpl-did`](https://github.com/mvadari/xrpl-did) | `did-resolver` driver for `did:xrpl`, plus `did-jwt` signing, on npm | TypeScript | none declared | **By the XRPLF standards maintainer.** Resolver only — no credential, agent, or trust layer | 2025-08-18 |
| [`xrpl-identity-mcp`](https://github.com/jarod-vyent/xrpl-identity-mcp) | MCP server: DIDs, XLS-70 credentials, signer lists; never custodies keys | TypeScript | MIT | A shipped MCP server — a tool surface over the primitives, not a library | 2026-07-08 |
| [`decID`](https://github.com/RomThpt/decID) | Decentralized identity on XRPL with DIDs and cryptographic proofs | JavaScript | MIT | Hackathon project with a frontend; no tests or CI. See [DID Document format](#did-document-format) | 2025-01-10 |
| [`LJ-XRPL/xrpl-agent-id`](https://github.com/LJ-XRPL/xrpl-agent-id) | 3-tier agent verification using XLS-40d DIDs and XLS-70d credentials | Python | MIT | Closest in stated intent; no commits in 9 months | 2026-01-31 |
| [`elpis-protocol`](https://github.com/efiniti-ai/elpis-protocol) | Proxy-injected `X-Elpis-DID` headers, DID + MPT anchoring, Express middleware | TypeScript | Apache-2.0 | Different layer — network-level attestation middleware, not a library | 2026-04-17 |
| [`agent-intent/verifiable-intent`](https://github.com/agent-intent/verifiable-intent) | Spec for cryptographic agent authorization — SD-JWT L1→L3 delegation chains | Python | Apache-2.0 | **The most-adopted effort in this space (102★, 21 forks).** Chain-agnostic; live via t54 as "x402 Secure" | 2026-04-20 |
| [XRPLF discussion #572](https://github.com/XRPLF/XRPL-Standards/discussions/572) | Proposed "Agent Identity" amendment | — | — | **Rejected in review** as mostly a repackaging of XLS-40/70/75 | 2024 |

### What the table actually says

- **Nobody else ships a maintained Python library for XRPL-native agent
  identity.** The closest alternatives are a resolver driver (TypeScript, 14
  months stale), a narrow MCP server, a frontend demo, or a network-layer
  middleware.
- **The most-adopted work in the broader space is chain-agnostic**, not
  XRPL-native. `verifiable-intent` defines an authorization *model* that any
  ledger can carry — and it ships live on XRPL through t54, so the two
  approaches are complements rather than rivals.
- **XRPLF declined to make this a protocol feature.** That is the clearest
  signal available on where this belongs: an off-chain library, not an
  amendment.

---

## Authorization policy and audit

Identity on its own doesn't answer "may this agent do this?" — that is a
separate layer, and it is where most agent-identity work stops.

```python
from xrpl_agent_id.authorization import AuthorizationPolicy, RequestContext
from xrpl_agent_id.banned import Ban, BannedAgentRegistry
from xrpl_agent_id.audit import AuditLog, XRPLMirror

bans = BannedAgentRegistry()
bans.add(Ban(address="rPzMYxDubFsbSh7bS9evarrh9Y5U1n9v13", reason="abuse report #4421", added_by="ops"))

policy = AuthorizationPolicy(bans=bans)
policy.require_credential(
    issuer="rTBDupBmeDkA9DVTDw4u5MdaUYptF3YJY",
    credential_type=b"agent_identity_v1",
)

decision = policy.evaluate("rwJX1TTwbrEV3thJ5oUaKt56sNTcGSZUmn", context=RequestContext(resource="/api/transfer"))
assert decision.allow is False
assert decision.reasons[0].code.name == "AGENT_BANNED"

# Every decision lands in SQLite; optionally also on-chain.
audit = AuditLog(db_path="audit.db", xrpl_mirror=XRPLMirror.from_seed("sEd...", network="testnet"))
audit.record(decision)
```

> **Module stability:** `AuthorizationPolicy`, `BannedAgentRegistry`,
> `AuditLog`, and `XRPLMirror` live in submodules and are part of the
> *internal* surface. The curated public API (`AgentIdentity`, `Authority`,
> `Credential`, `TrustRegistry`, …) is documented in
> [`xrpl_agent_id/PUBLIC_API.md`](xrpl_agent_id/PUBLIC_API.md) and is what is
> pinned as stable. The example above uses the internal surface for
> illustration.

**8 stable reason codes:** `OK`, `AGENT_BANNED`, `CONTROLLER_BANNED`,
`NO_CREDENTIALS`, `CREDENTIAL_MISSING`, `CREDENTIAL_REVOKED`,
`CREDENTIAL_TYPE_DENIED`, `CREDENTIAL_EXPIRED`.

**Audit:** SQLite by default. The optional XRPL mirror writes a 1-drop
self-payment carrying the decision hash as a memo. Note that **XRPL memos are
not signed** — anyone can post a matching memo, so the mirror is a *tamper-
evident* record, not an attributable one. See
[`docs/MAINNET_DECISION.md`](docs/MAINNET_DECISION.md) for the full reasoning.

---


## Mainnet readiness

**Mainnet is supported as of v0.5.0.** The credential lifecycle was validated
against real mainnet ledger state on **2026-10-28** — `DIDSet`,
`CredentialCreate`, `CredentialAccept`, `SignerListSet`, and the deletion
paths, all on network ID `1`.

A full run exercised **all six authorization roles** against real mainnet
state, with every outcome verified independently against the public ledger:

| Role | Expected | Result |
|---|---|---|
| `valid` | allow | ✅ `OK` |
| `banned` | deny | ✅ `AGENT_BANNED` |
| `controller_banned` | deny | ✅ `CONTROLLER_BANNED` |
| `no_creds` | deny | ✅ `NO_CREDENTIALS` |
| `wrong_issuer` | deny | ✅ `CREDENTIAL_MISSING` |
| `pending` | deny | ✅ `CREDENTIAL_REVOKED` |

Six of six correct.

| Surface | Mainnet OK? | Why |
|---|---|---|
| DID creation (`DIDSet`) | Yes | 10 drops base, reversible via `DIDDelete`, no expiry |
| Credential issuance (`CredentialCreate`) | Yes | Expiry enforcement shipped in v0.4.0 — `CREDENTIAL_EXPIRED` is emitted for an expired credential |
| Credential acceptance (`CredentialAccept`) | Yes | Free, optional opt-in |
| Authorization decisions / audit | Forensic only | The **ledger credential entry** is the confirmed artifact; the audit memo is provenance-verified but **not attributable to the issuer** |
| Dashboard `/api/verify` | Yes | Read-only; queries the ledger only |

**Two things this library does not do.** It does not implement
`DepositPreauth`, so its trust check is a library-side evaluation rather
than a protocol-level guarantee — a relying party who wants the ledger to
enforce issuer trust must create their own `DepositPreauth` allowlist. And
it does not ship a live KMS/HSM adapter; `WalletProvider` is the interface,
and the `ExternalSignerProvider` path is what a KMS-backed deployment uses.

Two cost facts worth knowing before you plan an XRPL deployment: mainnet
requires a **1 XRP base reserve per account** and a **0.2 XRP owner reserve
per owned ledger object** (lowered 10× by validator vote in December 2024),
and each accepted credential is an owned object. The reserve is a **balance
requirement, not a charge** — per XRPL's documentation, *"When objects are
removed from the ledger, they no longer count against the reserve
requirement."* Deleting the credential releases the 0.2 XRP; deleting the
account releases the 1 XRP.

One real agent identity — account, DID, and one credential — locks about
**1.4 XRP** as a balance requirement, of which the transaction fees are
**~0.02 XRP (~$0.027)**. `Authority.issue_credential_cheapest()` takes a
two-transaction path and drops the lockup to 1.2 XRP by skipping the
optional DID object.

The full reasoning — written before any mainnet transaction, then updated
with the run results — is in
[`docs/MAINNET_DECISION.md`](docs/MAINNET_DECISION.md).

---

## API surface

The public surface is 24 names, pinned by `tests/test_public_api.py`.

```python
from xrpl_agent_id import (
    AgentIdentity,    # the central object — wraps an xrpl-py Wallet
    Credential,       # an attestation issued by one agent to another
    DIDDocument,      # W3C DID Document for an XRPL account
    did_from_account, # build did:xrpl from an r-address
    resolve_did,      # fetch a DID Document from the ledger
)
```

Also exported: `Authority`, `CredentialType`, `NetworkEndpoint`,
`VerificationResult`, `TrustCheckResult`, `TrustPolicy`, `TrustRegistry`,
`parse_did`, `get_client`, `get_network`, `NETWORK_IDS`, `NETWORKS`,
`XRPL_AGENT_ID_VERSION`, `XRPL_AGENT_ID_VERSION_INFO`.

See [`xrpl_agent_id/PUBLIC_API.md`](xrpl_agent_id/PUBLIC_API.md) for the
stability contract on each name.

### Module map

| Module | Purpose | Status |
|---|---|---|
| `xrpl_agent_id.did` | Parse `did:xrpl:1:rXXX…` → `(network_id, address)`; build and parse W3C DID Documents; resolve via `ledger_entry` | Complete |
| `xrpl_agent_id.identity` | `AgentIdentity` — wrap an `xrpl-py.Wallet`, expose identity ops | Complete, live on testnet |
| `xrpl_agent_id.credential` | `Credential` — XLS-70 wrapper: issue / accept / verify | Complete |
| `xrpl_agent_id.authority` | `Authority` — issuance-only subclass of `AgentIdentity` | Complete |
| `xrpl_agent_id.trust` | `TrustRegistry` / `TrustPolicy` — declarative credential allowlist + banlist, evaluated against an agent DID | Complete |
| `xrpl_agent_id.authorization` | `AuthorizationPolicy` — request-level allow/deny with 8 reason codes | Complete, internal surface |
| `xrpl_agent_id.audit` | `AuditLog` (SQLite) + `XRPLMirror` (1-drop on-chain audit) | Complete, internal surface |
| `xrpl_agent_id.banned` | `Ban` + `BannedAgentRegistry` — agent deny-list, file-backed | Complete, internal surface |
| `xrpl_agent_id.registry` | Resolve any DID to its full ledger state | Complete |
| `xrpl_agent_id.network` | Network constants + client factory | Complete |

### Design decisions

1. **Conform to XLS-40d; don't extend it.** The spec is Final and ships in
   `rippled`. Nothing is added to the wire format — this library just makes
   the spec easier to use from Python.
2. **W3C DID Core 1.0 on output, XLS-40d-legacy on input.** The XLS-40d
   samples use the deprecated `publicKey` field and `EcdsaKoblitzPublicKey`
   type. Those are accepted on parse; output is canonical W3C
   (`verificationMethod` + `Multikey`). Regression tests in `tests/test_did.py`.
3. **The 256-byte on-ledger DID Document cap is respected.** XRPL's `DIDSet`
   limits `DIDDocument` and `URI` to 256 bytes each. Rich content goes behind
   the `URI` (IPFS / HTTPS).
4. **`CredentialType` on the ledger, `credential_type` in xrpl-py.** XLS-70d's
   on-ledger field is `CredentialType` (PascalCase). `xrpl-py` exposes it as
   `credential_type` in transaction models and `ledger_entry` responses.
   `Credential.to_ledger_fields()` encodes the xrpl-py spelling so it matches
   what the client expects.
5. **No key custody by default.** Seeds are never stored, logged, or
   transmitted beyond what `xrpl-py` does. Bring your own `Wallet`.
6. **The MCP wrapper is not shipped.** There is no `[mcp]` extra and no
   `xrpl_agent_id.mcp` module. Install the base package — the library is fully
   usable without it.

---

## DID Document format

`RomThpt/decID` was studied before this implementation and this library
deliberately diverges on four points. Listed as technical differences, not as
a criticism of that project — `decID` is a 2024 hackathon build and was
solving a different problem.

| decID | xrpl_agent_id | Why |
|---|---|---|
| `"@context": "https://www.w3.org/ns/did/v1"` (bare string) | `"@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"]` (array) | W3C DID Core 1.0 requires an array. Strict resolvers reject bare strings. |
| `"type": "Ed25519VerificationKey2020"` | `"type": "Multikey"` | `Ed25519VerificationKey2020` was deprecated by W3C in 2022. |
| `"publicKeyMultibase": "z${hex}"` (bare `z` + hex) | Computed from `publicKeyHex` using the correct multicodec (`e701` secp256k1, `ed01` Ed25519) + base58btc | The bare-z-prefix form is not a valid multibase encoding; verifiers reject it. |
| DID form `did:xrpl:<address>` (no network ID) | `did:xrpl:<network-id>:<address>` | XLS-40d requires the network-id component — its own README example is `did:xrpl:1:rXXX…`. |

---

## Testing

```
$ python -m pytest tests/ -q
379 passed, 6 skipped
```

*(Verified against v0.5.2 on 2026-10-28.)*

Every ledger interaction is mocked in the offline suite, so CI never needs
testnet access. The live testnet path is opt-in and documented above.

---

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | Shortest path from clone to working identity |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the DID, credential, and trust layers fit together |
| [`docs/USE_CASES.md`](docs/USE_CASES.md) | Five concrete scenarios, mapped to shipped code |
| [`docs/01_issuance_flow.md`](docs/01_issuance_flow.md) | End-to-end issuance walkthrough |
| [`docs/03_known_issues.md`](docs/03_known_issues.md) | Disclosed defects and the version that fixed each |
| [`docs/MAINNET_DECISION.md`](docs/MAINNET_DECISION.md) | The go/no-go reasoning for mainnet, plus the 2026-10-28 validation run and its results |
| [`docs/STRESS_TEST_v0.3.0.md`](docs/STRESS_TEST_v0.3.0.md) | Live testnet stress-test results |
| [`SECURITY.md`](SECURITY.md) | Security policy and supported versions |

---

## Releases

| Version | Date | Highlights |
|---|---|---|
| [v0.5.2](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.5.2) | 2026-10-28 | Documentation accuracy: the API contract, the security policy, and the mainnet gate all corrected. No change to the public API. |
| [v0.5.0](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.5.0) | 2026-10-28 | **`WalletProvider`** — sign XRPL transactions without holding the key (KMS/HSM/hardware wallet); **fee ceiling** (`max_fee_drops`) so a transaction is never signed into a congestion premium; **batch anchoring** — one transaction per 1,000 decisions via a Merkle root; Python floor raised to 3.10 with `xrpl-py>=5.0,<6` pinned |
| [v0.4.3](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.4.3) | 2026-10-23 | Security fixes: credential acceptance (`lsfAccepted`) is now read from the raw ledger node and fails closed; credential expiry is checked on the real path; DID validation hardened; ban list; dashboard verification |
| [v0.4.2](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.4.2) | 2026-10-22 | Corrected a README that advertised things the repository did not have; made the REUSE badge true (`LICENSES/` + `.reuse/dep5` + CI gate) after `reuse lint` failed on 29 of 78 tracked files |
| [v0.4.1](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.4.1) | 2026-09-30 | Version metadata synced across README, `__init__.py`, and `PUBLIC_API.md` |
| [v0.4.0](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.4.0) | 2026-09-30 | `CREDENTIAL_EXPIRED` reason code is now emitted (was a silent correctness gap) |
| [v0.2.4](https://github.com/DuNorth595/xrpl_agent_id/releases/tag/v0.2.4) | 2026-09-28 | Dashboard, trust library, S_DevLabs branding |

The v0.3.0 authorization-policy layer, banned-agent registry, and SQLite
`AuditLog` were developed and shipped between v0.2.4 and v0.4.0 without their
own tagged release. Full history lives in
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

Full history: [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## Contributing

Bug reports are the most valuable contribution. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, the pre-commit checks, and
the house rules.

**Report an issue:** <https://github.com/DuNorth595/xrpl_agent_id/issues>

Before opening one, please check
[`docs/03_known_issues.md`](docs/03_known_issues.md) — five defects have
already been fixed and disclosed, and two of them let a credential count as
valid when it should not have been.

## License

MIT — see [`LICENSE`](LICENSE). Every source file carries an SPDX header
and the repository passes `reuse lint`.

## Copyright, credits, and citation

```
SPDX-FileCopyrightText: 2026 Justin Douglas
SPDX-License-Identifier: MIT
```

Every source file carries an SPDX header. The full MIT text is in
[LICENSE](LICENSE). This repository follows the [REUSE](https://reuse.software/)
specification; `reuse lint` passes cleanly.

**Author:** Justin Douglas — design, implementation, and stewardship.

AI assistance: implementation, documentation, and architecture were developed
with an AI coding assistant (Hermes Agent, Nous Research) under direct human
supervision. All design decisions, specification interpretations, and commits
were reviewed and approved by the author.

**Built on the shoulders of:**

- [Wietse Wind](https://github.com/WietseWind) — `xrpl-py`, the XRPL Python client this library wraps
- The [XRPL Foundation](https://xrpl.org/) — for XLS-40d (DID method) and XLS-70 (Credentials)
- The [W3C DID Core Working Group](https://www.w3.org/groups/wg/did/) — for the `did:` specification this library conforms to
- [jarod-vyent](https://github.com/jarod-vyent) — `xrpl-identity-mcp`, whose tool taxonomy informed a future MCP wrapper
- [RomThpt](https://github.com/RomThpt) — `decID`, whose DID Document format this library deliberately does not copy

**Dependency licenses:** `xrpl-py` — MIT (Ripple Labs / Wietse Wind). All XRPL
standards (XLS-40d, XLS-70) — public-domain spec text.

**How to cite:**

```bibtex
@software{xrpl_agent_id_2026,
  author = {Douglas, Justin},
  title = {xrpl_agent_id: Identity for AI agents on the XRP Ledger},
  year = {2026},
  url = {https://github.com/DuNorth595/xrpl_agent_id}
}
```

---

## Related projects

- [`xrpl_did_mcp`](https://github.com/DuNorth595/xrpl_did_mcp) — MCP server exposing this library to any AI agent client: derive, resolve, and authorize identities over the ledger
- [`xrpl_agent_log`](https://github.com/DuNorth595/xrpl_agent_log) — companion library: tamper-evident agent history. `SigningPair.from_identities()` takes two `AgentIdentity` objects and reads their DIDs directly, so an identity here and a log entry there need no glue code on XRPL
- [`xrpl-py`](https://github.com/XRPLF/xrpl-py) — the Python client this wraps
- [`agent-intent/verifiable-intent`](https://github.com/agent-intent/verifiable-intent) — chain-agnostic agent authorization spec
- [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) — the EVM agent identity standard
