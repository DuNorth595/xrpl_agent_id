# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
