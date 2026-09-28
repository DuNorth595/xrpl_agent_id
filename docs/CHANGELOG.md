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
  - Network map: mainnet=1, testnet=2, devnet=3, AMM-devnet=25, sidechain=222
  - 12 regression tests, all passing in 0.02s
- `xrpl_agent_id/credential.py` — XLS-70 wrapper
  - `CredentialType` enum (`AGENT_ID_V1`, `VERIFIED_AGENT_OPERATOR`)
  - `Credential` dataclass with `to_ledger_fields()`, `from_ledger_entry()`
  - Auto hex-encodes `credential_type` (snake_case wire format)
  - 14 unit tests
- `xrpl_agent_id/identity.py` — `AgentIdentity` class
  - `from_seed(seed, network)` — loads `xrpl-py` Wallet
  - `request_agent_id()`, `accept_credential()`, `set_did_document()`
- `xrpl_agent_id/authority.py` — `Authority` class
  - `issue_credential(credential, subject)` — submits CredentialCreate
  - `verify_credential(issuer, subject, credential_type)` — fetches ledger_entry
  - `get_credentials_for_account(account)` — ledger_data helper
  - Uses `submit_and_wait()` for thread-safety correctness
- Test suite: 26 offline + 3 live-gated = 29 total, 26/26 passing offline
- Integration test scaffolding (`tests/test_integration_ledger_live.py`)
- REUSE-compliant SPDX headers in all source files
- README.md (15.8 KB, 6 sections: Cover, Description, Copyright, Code, Testing, Appendix)
- Polished PDF deliverable (`xrpl_agent_id_README.pdf`, 9 pages, 474 KB)

### Fixed
- `NETWORK_IDS["mainnet"]` corrected from `0` to `1` (verified against XLS-40d README)
- `credential_type` snake_case wire-format encoding for xrpl-py compatibility
- `DIDDocument.to_json()` emits array `@context` (not bare string) per W3C DID Core 1.0
- `DIDDocument.to_json()` normalizes legacy VM types to `Multikey`

### Security
- No key custody by default; seeds never logged or transmitted beyond `xrpl-py`
- `testnet_keys/` directory gitignored — secrets never committed
- MCP design: prepare/verify/submit split keeps signing off the tool surface
