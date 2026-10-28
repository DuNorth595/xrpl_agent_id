# xrpl_agent_id — Public API Contract

**Version: 0.5.1** · Pinned by `tests/test_public_api.py` · Last updated: 2026-10-28

This document is the formal public-API contract for `xrpl_agent_id`. Every name
listed here is exported from the top-level `xrpl_agent_id` package and is
covered by a test that fails if the name is removed or renamed without notice.

## Import contract

```python
import xrpl_agent_id
xrpl_agent_id.<NAME>  # all names below must be reachable this way
```

## Core types

| Name | Purpose |
| --- | --- |
| `AgentIdentity` | A single agent's XRPL identity (DID + keys + credentials). |
| `Authority` | An issuer-side identity that can only sign `CredentialCreate` transactions. |
| `Credential` | An XLS-70 attestation issued by one agent to another. |
| `CredentialType` | Closed enum: `AGENT_ID_V1`, `VERIFIED_AGENT_OPERATOR`, `EVAL_PASSED`. |
| `DIDDocument` | W3C DID Document for an XRPL account. |
| `NetworkEndpoint` | Named XRPL network (mainnet / testnet / devnet) with URL + network ID. |
| `VerificationResult` | Result of verifying a Credential against its issuer. |

## DID helpers

| Name | Purpose |
| --- | --- |
| `NETWORK_IDS` | Mapping of network name → numeric network ID. |
| `NETWORKS` | Mapping of network name → `NetworkEndpoint`. |
| `did_from_account(address, network)` | Build a `did:xrpl:<netid>:<address>` string. |
| `parse_did(did)` | Parse a `did:xrpl:` URI into `(network_id, address)`. |
| `resolve_did(did)` | Fetch a DID Document from the ledger. |

## Network helpers

| Name | Purpose |
| --- | --- |
| `get_client(network)` | Get an async JSON-RPC client (xrpl-py) for a network. |
| `get_network(name)` | Get a `NetworkEndpoint` by name. |

## Trust library

| Name | Purpose |
| --- | --- |
| `TrustCheckResult` | Outcome of a trust check: satisfied / missing / denied / banned. |
| `TrustPolicy` | A single `require` / `deny` / `ban` rule. |
| `TrustRegistry` | Holds the rules and evaluates an agent DID against them. |

`TrustRegistry` methods:

| Method | What it does |
|---|---|
| `require(issuer, credential_type, description=...)` | The agent must hold a matching credential. A set of issuers is satisfied by **any** of them. |
| `deny(issuer, credential_type, description=...)` | The agent must **not** hold a matching credential. |
| `ban(subject, description=...)` | The agent is denied outright, whatever it holds. Evaluated first, and reported as `result.banned` with `result.ban_reason`. |
| `check(agent_did)` | Evaluate every rule. Returns a `TrustCheckResult`. |

`credential_type` accepts `bytes` or `str` and is stored as `bytes`
(`TrustPolicy.credential_type_bytes` is the normalized form).

`TrustCheckResult` fields: `agent_did`, `satisfied`, `missing_required`,
`denied_held`, `all_credentials_found`, `banned`, `ban_reason`.

`missing_required` reports one entry per unsatisfied **rule**, not one per
issuer. A rule with three issuers is satisfied by a credential from any
of them, so listing three entries would read as though all three were
individually required.

## Wallet providers

Added in v0.5.0. A provider is anything that can identify an account and
sign transactions for it — including a KMS, an HSM, or a hardware wallet that
never reveals the key.

| Name | Purpose |
| --- | --- |
| `WalletProvider` | The protocol. Three members: `classic_address`, `public_key`, `sign(tx)`. Structural — any object with those members satisfies it. |
| `SeedWalletProvider` | Wraps an `xrpl.wallet.Wallet` in process. The pre-v0.5.0 behaviour. |
| `ExternalSignerProvider` | Signs via a caller-supplied callable, for KMS / HSM / hardware-wallet integration. |

`AgentIdentity.provider` holds one of these. The previous `AgentIdentity.wallet`
attribute is **deprecated** and will be removed in v0.6.0; it still works for a
`SeedWalletProvider` and raises a clear `AttributeError` for an external one,
which has no wallet to return.

## Fee control

These controls exist so that the *optional* on-chain paths stay affordable:
they stop a transaction being signed into a congestion premium nobody intended
to pay, and they keep the audit trail from costing one transaction per
decision.

| Name | Purpose |
| --- | --- |
| `FeeCeilingExceeded` | Raised when a transaction's fee exceeds the configured drop ceiling. Raised *before* signing, so no premium is ever paid. |
| `max_fee_drops_for_usd(usd_limit, xrp_price)` | Convert a USD fee ceiling into a drop ceiling. A drop ceiling caps the XRP paid, not the dollars — the same 5,000-drop ceiling is $6.65 at $1.33/XRP and $5,000 at $1,000/XRP. Use this when the real constraint is a dollar amount. |
| `submit_with_retry(..., max_fee_drops=N)` | The ceiling is enforced against the *autofilled* fee, which is the one that reflects the current open-ledger cost. |
| `AuditLog.anchor_batch(limit=1000)` | Anchor up to 1,000 decisions in **one** transaction carrying a Merkle root. This is the cheap path: one fee per batch instead of one per decision — roughly 1,000× cheaper at a million decisions a day. |
| `AuditLog.batch_proof(decision_id)` | Return the inclusion proof for one decision in an anchored batch. Needs only leaf + path + root, so a verifier does not need the operator's database. |
| `xrpl_agent_id.merkle` | `merkle_root()`, `merkle_proof()`, `verify_inclusion()` — domain-separated leaves, direction-encoded proof paths. |

The audit mirror is **optional and off by default**: `AuditLog` accepts
`xrpl_mirror=None`, and when a mirror is configured the default policy is
`mirror_on_deny_only=True`, so an allowed decision writes to SQLite and
touches nothing on-chain. The mirror runs on its **own key**, so its
on-chain cost is the operator's, never the counterparty's.

## Version

| Name | Purpose |
| --- | --- |
| `XRPL_AGENT_ID_VERSION` | String `"0.5.1"` (canonical version, same value as `__version__`). |
| `XRPL_AGENT_ID_VERSION_INFO` | Tuple `(0, 5, 0)` — PEP-440-style, loose. Derived from `__version__`. |

The `__version__` attribute is also exported (standard Python convention).

## Stability policy

* **Additive changes** (new names) are allowed in minor versions (0.3.x → 0.3.y).
* **Breaking changes** (removed/renamed names) require a major version bump
  and a deprecation period of at least one minor release.
* The contract is enforced by `tests/test_public_api.py` — that test must pass
  before any release.

## Submodule access

Submodules (`xrpl_agent_id.identity`, `.authority`, `.credential`, `.did`,
`.network`, `.trust`, `.audit`) remain importable for advanced use, but they
are **not** part of this contract. New symbols can appear in submodules
without bumping the version; only the top-level names above are pinned.

## What is NOT in the public API

Only the top-level names above are pinned. The following real symbols live in
submodules and are **not** re-exported at the top level. They are importable
from their submodule if you need them, but their names and signatures are not
covered by the compatibility guarantee:

| Name | Import from | Why it is not top-level |
|---|---|---|
| `AuthorizationPolicy` | `xrpl_agent_id.authorization` | The authorization layer is an opt-in subsystem |
| `AuthorizationDecision` | `xrpl_agent_id.authorization`, `.audit` | Returned by the policy; use it, don't construct it |
| `ReasonCode` | `xrpl_agent_id.authorization` | Enum behind `AuthorizationDecision.reasons` |
| `RequestContext` | `xrpl_agent_id.authorization` | Passed *into* `AuthorizationPolicy.evaluate()` |
| `AgentRegistry` | `xrpl_agent_id.authorization` | Internal registry implementation |
| `BannedAgentRegistry` | `xrpl_agent_id.banned` | Ban storage is pluggable |
| `Ban` | `xrpl_agent_id.banned` | A ban record; construct one and pass it to the registry |
| `AuditLog` | `xrpl_agent_id.audit` | Audit logging is an opt-in subsystem |
| `AuditRecord` | `xrpl_agent_id.audit` | Returned by `AuditLog.record()` |
| `XRPLMirror` | `xrpl_agent_id.audit` | The optional on-chain mirror |
| `provider_from_wallet` | `xrpl_agent_id.wallet` | Convenience helper for migrating an existing wallet |

Names that appear in older drafts or internal notes but **do not exist in the
package at all**: `Registry`, `Publisher`, `Keypair`, `AcceptancePolicy`,
`LedgerAuditLog`, `Journal`, `AgentRecord`.

Use the names listed under "Core types", "DID helpers", "Network helpers",
"Trust library", "Wallet providers", "Fee control", and "Version" instead.
