# xrpl_agent_id — Public API Contract

**Version: 0.5.0** · Pinned by `tests/test_public_api.py` · Last updated: 2026-10-28

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
| `TrustCheckResult` | Outcome of a trust check: satisfied / missing / denied. |
| `TrustPolicy` | A set of `require` / `deny` rules. |
| `TrustRegistry` | Evaluates an agent DID against a `TrustPolicy`. |

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
| `XRPL_AGENT_ID_VERSION` | String `"0.5.0"` (canonical version, same value as `__version__`). |
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

These names appeared in earlier drafts or internal docs but are **not**
importable from `xrpl_agent_id`:

* `BannedAgentRegistry` (removed in v0.3.3 — dead code, never re-exported).
* `AuthorizationPolicy` (never existed — was a doc typo).
* `Registry` (duplicate dead-code class, removed in v0.3.3).
* `Publisher`, `Keypair`, `AcceptancePolicy`, `AuthorizationDecision`,
  `ReasonCode`, `RequestContext`, `LedgerAuditLog`, `XRPLMirror`, `Journal`,
  `AuditRecord`, `Ban`, `AgentRecord`, `AgentRegistry` — all internal-only,
  not re-exported.
* `provider_from_wallet` — a convenience helper in
  `xrpl_agent_id.wallet`, not re-exported at the top level.

Use the names listed under "Core types", "DID helpers", "Network helpers",
"Trust library", and "Version" instead.
