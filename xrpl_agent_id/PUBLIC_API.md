# xrpl_agent_id — Public API Contract

**Version: 0.4.0** · Pinned by `tests/test_public_api.py` · Last updated: 2026-09-30

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

## Version

| Name | Purpose |
| --- | --- |
| `XRPL_AGENT_ID_VERSION` | String `"0.4.0"` (canonical version, same value as `__version__`). |
| `XRPL_AGENT_ID_VERSION_INFO` | Tuple `(0, 4, 0)` — PEP-440-style, loose. |

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

Use the names listed under "Core types", "DID helpers", "Network helpers",
"Trust library", and "Version" instead.
