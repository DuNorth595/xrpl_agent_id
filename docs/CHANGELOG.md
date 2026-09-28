# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.4] - 2026-09-28

### Changed
- **Public contact information migrated from personal to org** (`S_DevLabs@outlook.com`)
  - `docs/00_executive_summary.md` and `docs/00_executive_summary.html`: contact block now reads `S_DevLabs / S_DevLabs@outlook.com`. Personal name, personal email, and personal phone removed from these user-facing surfaces.
  - `README.md`: added `Organization: S_DevLabs (Strategic Development Labs)` and `Contact: S_DevLabs@outlook.com` rows in the header table.
- License/copyright headers (SPDX, `pyproject.toml` `authors`) keep `Justin Douglas` as the legal author — that's the correct attribution per the project's copyright model.

### Added
- **`scripts/build_summary_pdf.py`** — single-source-of-truth builder for the executive summary PDF, reads the hand-crafted HTML directly (no pandoc dep), prints via Playwright. Use this whenever `00_executive_summary.html` is edited so the PDF stays in sync.

### Fixed
- Older Desktop snapshot PDFs (`v0.1.0`, `v0.2.0`, `v0.2.1`, `v0.2.2`) had the old contact baked in. Deleted those stale binaries — the source-of-truth MD/HTML in those folders has been redacted, and the latest snapshot's PDF is regenerated from the clean source.

## [0.2.3] - 2026-09-28

### Added
- **S_DevLabs branding in dashboard header** — SVG hexagon + stylized S mark (teal-to-deep-blue gradient, matches the S_DevLabs logo) renders to the left of the title
- **"BY S_DEVLABS" pill tag** next to the dashboard title (teal accent, uppercase, rounded)
- Brand mark is inline SVG (no external assets), mirrors the official logo's gradient

## [0.2.2] - 2026-09-28

### Added
- **Three-column "Tracked agents" layout** — Subject / Issuer / Evaluator as separate side-by-side columns
  - Each column is a `col-4` panel with color-coded heading (Subject teal, Issuer purple, Evaluator amber)
  - Agents are bucketed by `role` in the watchlist (`subject`, `issuer`, `evaluator`)
  - Empty columns show "No <role> watched." instead of the whole panel going blank
- **Connection health moved to header** as a clickable pill
  - Pill shows status dot + `connected · N events` + secondary count `M events` (events buffered)
  - Click expands a dropdown popover with the full event log (When / Event table)
  - × close button + click-outside-to-dismiss

### Changed
- **Watchlist role values** renamed for clarity: `agent` → `subject` (the agent being verified). Evaluator gets its own role value rather than being aliased as `issuer` with a label.
- `backfill.py` seeds the watchlist with the new role names so re-running it stays consistent.
- `xrpl_agent_id/__version__` bumped to `0.2.2`

### Fixed
- Live DB row mismatches: `rhuboR2n…` (Subject) was incorrectly `role="agent"`, `rEmcSP…` (Evaluator) was `role="issuer"` with label "Evaluator". Now correctly `subject` and `evaluator`.

## [0.2.1] - 2026-09-28

### Added
- **Dashboard "Project Files" panel** — exposes the on-disk project tree to the UI
  - New `/api/files` endpoint walks `PROJECT_ROOT`, returns 52 files (no `.db`, `.pyc`, sidecars, secrets)
  - Grouped, collapsible display: `xrpl_agent_id/` (15) · `tests/` (6) · `scripts/` (3) · `docs/` (8) · `results/` (3) · root files (17)
  - Each file has a "open" link → `file://` URL → opens in Finder
  - Refreshes once on load + every 60s (files change rarely)
- **`api_files()`, `_walk_project()`, `_pkg_version()`** — pure server-side helpers, no I/O outside the project tree
- **2 new offline tests** (`test_files_api_lists_project`, `test_files_api_excludes_secrets_and_artifacts`) — 45 → 47 tests passing

### Changed
- `xrpl_agent_id/__version__` bumped to `0.2.1`

## [0.2.0] - 2026-09-28 (build-out #2)

### Added
- **`xrpl_agent_id/dashboard/`** — live agent-ID dashboard (stdlib-only HTTP)
  - `dashboard/db.py` — SQLite schema (identity_events, credential_events, watchlist, monitor_events)
  - `dashboard/monitor.py` — XRPL testnet websocket subscriber, filters for CredentialCreate/Accept/Delete + DIDSet/DIDDelete
  - `dashboard/server.py` — stdlib HTTP server with JSON API (`/api/summary`, `/api/credentials`, `/api/identities`, `/api/watchlist`, `/api/agent_state`, `/api/health`)
  - `dashboard/templates/index.html` + `dashboard.js` — single-page dark-theme UI, polled every 2s
  - `dashboard/backfill.py` — seeds DB from existing `results/scenario_*.json` files
- **`xrpl_agent_id/trust.py`** — new module: trust library layer
  - `TrustRegistry` — composable trust policy with `.require()` and `.deny()` rules
  - `TrustPolicy`, `TrustCheckResult` — dataclasses for rule + result representation
  - `TrustRegistry.check(agent_did)` — one-call policy verification
  - Returns `TrustCheckResult` with `.satisfied`, `.missing_required`, `.denied_held`, `.summary()`
- **`Authority.verify_set(agent_did, required)`** — batch credential verification
  - Returns `VerificationResult` with per-credential pass/fail + missing list
- **`Authority.revoke_credential(subject, credential_type)`** — XLS-70 `CredentialDelete` wrapper
- **Public API surface** (`xrpl_agent_id/__init__.py`):
  - `TrustRegistry`, `TrustPolicy`, `TrustCheckResult`, `VerificationResult` exported
- **`scripts/run_trust_scenarios.py`** — 11-step trust library scenario on real testnet
- **`scripts/sync_to_desktop.py`** — mirrors LIFE_MEMORY project to `~/Desktop/XRPL_AGENT_ID/<version>/` per version (read-only Desktop archive)
- **Test coverage expansion:** 27 → 45 offline tests (16 trust + 2 dashboard)
- **`docs/02_testing_log.md`** — comprehensive testing log with bugs found + fixes
- **`docs/03_dashboard.md`** — dashboard usage guide
- Live testnet: 6/6 live tests passing

### Fixed
- **`Authority.revoke_credential()` v1 design flaw**: initial implementation used a second `CredentialCreate` for revocation, but XLS-70 enforces `(issuer, subject, credential_type)` uniqueness → `tecDUPLICATE`. Rewrote to use `CredentialDelete` (XLS-70's native delete).
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
