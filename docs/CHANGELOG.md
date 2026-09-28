# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`xrpl_agent_id/dashboard/`** — live agent-ID dashboard (stdlib-only HTTP)
  - `dashboard/db.py` — SQLite schema (identity_events, credential_events, watchlist, monitor_events)
  - `dashboard/monitor.py` — XRPL testnet websocket subscriber, filters for CredentialCreate/Accept/Delete + DIDSet/DIDDelete
  - `dashboard/server.py` — stdlib HTTP server with JSON API (`/api/summary`, `/api/credentials`, `/api/identities`, `/api/watchlist`, `/api/agent_state`, `/api/health`)
  - `dashboard/templates/index.html` + `dashboard.js` — single-page dark-theme UI, polled every 2s
  - `dashboard/backfill.py` — seeds DB from existing `results/scenario_*.json` files
  - **6 API endpoints, 1 watcher, 1 server, 1 backfill tool, 1 HTML/JS dashboard**
- **`scripts/sync_to_desktop.py`** — mirrors LIFE_MEMORY project to `~/Desktop/XRPL_AGENT_ID/<version>/` per version (read-only Desktop archive)
- **Test coverage expansion:** 43 → 45 offline tests (+2 dashboard smoke tests)
- **`docs/03_dashboard.md`** — dashboard usage guide

### Known issues
- `submit_and_wait` intermittently hits `tefPAST_SEQ` when called immediately after another test (cached `LastLedgerSequence`). Mitigated with `time.sleep(3)` at script start. Long-term fix: retry decorator on `submit_and_wait`.
- `TrustRegistry._get_agent_credentials()` is a stub; needs `xrpl.account_objects` integration for full ledger enumeration.
- `TrustRegistry` cache is unbounded; needs LRU eviction or TTL.

## [0.2.0] - 2026-09-28 (build-out #2)

### Added
- **`xrpl_agent_id/trust.py`** — new module: trust library layer
  - `TrustRegistry` — composable trust policy with `.require()` and `.deny()` rules
  - `TrustPolicy`, `TrustCheckResult` — dataclasses for rule + result representation
  - `TrustRegistry.check(agent_did)` — one-call policy verification
  - Returns `TrustCheckResult` with `.satisfied`, `.missing_required`, `.denied_held`, `.summary()`
- **`Authority.verify_set(agent_did, required)`** — batch credential verification
  - Returns `VerificationResult` with per-credential pass/fail + missing list
- **`Authority.revoke_credential(subject, credential_type)`** — XLS-70 `CredentialDelete` wrapper
  - Designed for use by either issuer OR subject (both can sign)
- **Public API surface** (`xrpl_agent_id/__init__.py`):
  - `TrustRegistry`, `TrustPolicy`, `TrustCheckResult`, `VerificationResult` exported
- **`scripts/run_trust_scenarios.py`** — end-to-end scenario runner

  - 11-step trust library scenario on real testnet
  - Captures all tx hashes + verify results to `results/scenario_<ts>.json`
- **Test coverage expansion:** 27 → 43 offline tests (16 new tests for trust library)
- **`docs/02_testing_log.md`** — comprehensive testing log with bugs found + fixes
- Live testnet: 6/6 live tests passing (added test_05_revoke_credential + test_06_verify_set_and_trust_registry)

### Fixed
- **`Authority.revoke_credential()` v1 design flaw**: initial implementation used a second `CredentialCreate` for revocation, but XLS-70 enforces `(issuer, subject, credential_type)` uniqueness → `tecDUPLICATE`. Rewrote to use `CredentialDelete` (XLS-70's native delete). Docstring updated to recommend `expiration`-based soft revocation for audit-trail-preserving revocation.
- **Default revocation URI**: was too long; fixed to compact `revoke://<short-tails>` with safety fallback.
- **`scripts/run_trust_scenarios.py`**: `.url` → `.json_rpc_url` (matches actual NetworkEndpoint attribute).
- **Test isolation**: test_06 no longer depends on test_03 / test_05 state — issues its own fresh credential.

### Known issues
- `submit_and_wait` intermittently hits `tefPAST_SEQ` when called immediately after another test (cached `LastLedgerSequence`). Mitigated with `time.sleep(3)` at script start. Long-term fix: retry decorator on `submit_and_wait`.
- `TrustRegistry._get_agent_credentials()` is a stub; needs `xrpl.account_objects` integration for full ledger enumeration.
- `TrustRegistry` cache is unbounded; needs LRU eviction or TTL.

## [0.1.0] - 2026-09-28

Initial live-capable release. Same as [Unreleased] minus the trust library additions. 27 offline + 4 live tests passing.

### Added
- Project skeleton (LICENSE, README, pyproject.toml, package directory)
- `xrpl_agent_id/did.py` — XLS-40d parser, W3C DID Core 1.0 compliant
  - `did_from_account()` — builds `did:xrpl:<network-id>:<address>`
  - `parse_did()` — parses back to `(network_id, address)` tuple
  - `DIDDocument` — W3C DID Document with array `@context`, `Multikey` types
  - `resolve_did()` — live `ledger_entry(did=...)` resolution
  - Network map: mainnet=1, testnet=2, devnet=3, AMM-devnet=25, sidechain=222
  - 12 regression tests, all passing
- `xrpl_agent_id/network.py` — NetworkEndpoint + JSON-RPC client factory
- `xrpl_agent_id/credential.py` — XLS-70 wrapper (full implementation)
  - `CredentialType` enum (`AGENT_ID_V1`, `VERIFIED_AGENT_OPERATOR`, `EVAL_PASSED`)
  - `Credential` dataclass with `to_ledger_fields()`, `from_ledger_entry()`, `to_w3c_vc()`
  - Auto hex-encodes `credential_type` (snake_case wire format)
  - Validates 64-byte type cap, 256-byte URI cap per XLS-70
  - 15 unit tests
- `xrpl_agent_id/identity.py` — `AgentIdentity` class (live-ready)
  - `from_seed(seed, network)` — loads `xrpl-py` Wallet
  - `issue_credential()` — submits CredentialCreate
  - `accept_credential()` — submits CredentialAccept
  - `set_did_document()`, `set_did_uri()` — DIDSet helpers
  - `has_credential()`, `resolve_did_document()` — verification helpers
- `xrpl_agent_id/authority.py` — `Authority` class (issuance-only subclass)
- Live testnet demo: `scripts/issue_agent_id.py`
  - Funds fresh wallets via faucet
  - Issues, accepts, writes DID, verifies — all on real testnet
  - Captures all tx hashes and raw ledger entries to `results/`
- Test suite: 27 offline + 4 live-gated = 31 total, 27/27 passing offline
- Integration test scaffolding (`tests/test_integration_ledger_live.py`)
- REUSE-compliant SPDX headers in all source files
- README.md (15.8 KB, 6 sections: Cover, Description, Copyright, Code, Testing, Appendix)
- Polished PDF deliverable (`xrpl_agent_id_README.pdf`, 9 pages, 474 KB)
- Live testnet results doc: `docs/01_issuance_flow.md`
  - First live run: `results/issuance_testnet_20260928_173724.json`
  - All 3 transactions (CredentialCreate, CredentialAccept, DIDSet) succeeded on testnet

### Fixed
- `NETWORK_IDS["mainnet"]` corrected from `0` to `1` (verified against XLS-40d README)
- `credential_type` snake_case wire-format encoding for xrpl-py compatibility
- `DIDDocument.to_json()` emits array `@context` (not bare string) per W3C DID Core 1.0
- `DIDDocument.to_json()` normalizes legacy VM types to `Multikey`
- `resolve_did()` returns synthesized DID Document when no `DIDDocument` on-ledger

### Security
- No key custody by default; seeds never logged or transmitted beyond `xrpl-py`
- `testnet_keys/` directory gitignored — secrets never committed
- MCP design: prepare/verify/submit split keeps signing off the tool surface
