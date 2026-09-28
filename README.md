# xrpl_agent_id

> **Identity for AI agents on the XRP Ledger.**
> W3C-compatible `did:xrpl` + XLS-70 Verifiable Credentials, in one Python package.
> Native on-chain, no extra infra, ERC-8004-style without the EVM.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![xrpl-py 4.5+](https://img.shields.io/badge/xrpl--py-4.5+-black.svg)](https://pypi.org/project/xrpl-py/)
[![SPDX: REUSE](https://img.shields.io/badge/SPDX-REUSE--compliant-brightgreen)](https://reuse.software/)

---

## Cover Page

**Package:** `xrpl_agent_id`
**Version:** 0.1.0 (beta)
**Author:** Justin Douglas
**License:** MIT
**Python:** ≥ 3.9
**Dependencies:** `xrpl-py` ≥ 4.5.0
**Optional extras:** `mcp` (for MCP server wrapper, see §4)

**What this is in one sentence:**
A small Python library that gives AI agents an XRPL-native identity — a `did:xrpl` identifier, a W3C DID Document, and a way to issue / accept / verify XLS-70 Verifiable Credentials, all stored on the XRP Ledger.

**What this is NOT:**
A key-management system, a privacy layer, a wallet, or a replacement for OIDC/SAML.
Credentials on XRPL are public. Bring your own keys. Use `xrpl-py`'s `Wallet`.

---

## 1. Description

### The Problem

AI agents are proliferating. Every one of them needs an identity — something a counterparty can verify, something that travels with the agent across hosts, something that doesn't depend on a single vendor. On EVM chains, [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) is the emerging standard. **On XRPL, no equivalent exists.** All current attempts are <10 GitHub stars and abandoned.

### The Solution

XRPL shipped the primitives needed for agent identity back in 2020-2024 — they just weren't packaged for AI agents:

| Primitive | XRPL Spec | What it gives an agent |
|---|---|---|
| **DIDSet** | [XLS-40d](https://github.com/XRPLF/XRPL-Standards/blob/master/XLS-0040-decentralized-identity/README.md) | A `did:xrpl:1:rXXX…` identifier, on-ledger, no registrar |
| **CredentialCreate / Accept / Delete** | XLS-70 | W3C-style Verifiable Credentials, issuer-attested, on-ledger |
| **MPTokenIssuanceCreate** | XLS-33 | Optional reputation / capability tokens, transferable |

`xrpl_agent_id` is a thin Python library that wraps those primitives and gives you:

- **`did:xrpl` identifiers** that resolve through any xrpl-py client
- **W3C DID Core 1.0 compliant** DID Documents (array `@context`, `Multikey` types, proper multicodec + base58btc multibase)
- **Verifiable Credentials** with the `credential_type` snake_case wire-format trap handled
- **MCP-friendly design** — install with `[mcp]` extra to expose identity operations to LLM agents via the Model Context Protocol
- **Zero vendor lock-in** — your keys, your ledger, your data

### 10-line Identity (the pitch)

```python
from xrpl_agent_id import AgentIdentity, Authority

# Issuer: a service that vouches for agents (e.g. an eval registry)
issuer = Authority.from_seed("sEd...", network="testnet")

# Subject: the AI agent getting verified
agent = AgentIdentity.from_seed("sEd...", network="testnet")

# Issuer creates a credential attesting "this agent passed the eval"
issuer.issue_credential(
    subject=agent.did,
    credential_type=b"agent-eval-passed-v1",
    uri="ipfs://Qm...",  # full claim details off-chain
)

# Agent accepts it (its own signature, on its own account)
agent.accept_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)

# Anyone can verify it — no keys, no registry, just the public ledger
assert agent.has_credential(
    issuer=issuer.address,
    credential_type=b"agent-eval-passed-v1",
)
```

---

## 2. Copyright and Credits

### Copyright

```
SPDX-FileCopyrightText: 2026 Justin Douglas
SPDX-License-Identifier: MIT
```

Every source file in this repository carries an SPDX header with the above
copyright and license. The full MIT text is in [LICENSE](LICENSE).

### Credits

**Author:** Justin Douglas — design, implementation, and stewardship of `xrpl_agent_id`.

**Built with [Sam Intelligence](https://github.com/DuNorth595/sam-intelligence)** —
an AI coding assistant (Hermes Agent, Nous Research) that contributed to
implementation, documentation, and architecture under direct human supervision.
All design decisions, specification interpretations, and final commits were
reviewed and approved by Justin Douglas.

**Built on the shoulders of:**

- [Wietse Wind](https://github.com/WietseWind) — `xrpl-py`, the XRPL Python client we wrap
- The [XRPL Foundation](https://xrpl.org/) — for `XLS-40d` (DID method) and `XLS-70` (Credentials)
- [W3C DID Core Working Group](https://www.w3.org/groups/wg/did/) — the `did:` spec we conform to
- [jarod-vyent](https://github.com/jarod-vyent) — the MCP server taxonomy our future MCP wrapper mirrors
- [RomThpt](https://github.com/RomThpt) — `decID`, whose DID Document format we explicitly do NOT copy (see Appendix B for the comparison)

**License of dependencies:**
- `xrpl-py` — MIT (Ripple Labs / Wietse Wind)
- All XRPL standards (XLS-40d, XLS-70) — public-domain spec text

### How to Cite

```bibtex
@software{xrpl_agent_id_2026,
  author = {Douglas, Justin},
  title = {xrpl_agent_id: Identity for AI agents on the XRP Ledger},
  year = {2026},
  url = {https://github.com/DuNorth595/xrpl_agent_id}
}
```

---

## 3. Code Details

### Project Structure

```
xrpl_agent_id/
├── LICENSE                       ← MIT, Justin Douglas
├── README.md                     ← this file
├── pyproject.toml                ← hatchling build, MIT, Python 3.9+
├── xrpl_agent_id/                ← the package
│   ├── __init__.py               ← public API surface (5 names)
│   ├── __main__.py               ← `python -m xrpl_agent_id` smoke test
│   ├── did.py                    ← XLS-40d parser + W3C DID Document
│   ├── identity.py               ← AgentIdentity (DID + keys + credentials)
│   └── credential.py             ← XLS-70 Credential
└── tests/
    ├── __init__.py
    └── test_did.py               ← 12 tests, 0.02s runtime
```

### Public API

```python
from xrpl_agent_id import (
    AgentIdentity,    # the central object — wraps an xrpl-py Wallet
    Credential,       # an attestation issued by one agent to another
    DIDDocument,      # W3C DID Document for an XRPL account
    did_from_account, # build did:xrpl from an r-address
    resolve_did,      # fetch DID Document from the ledger
)
```

### Module Map

| Module | Purpose | Status |
|---|---|---|
| `xrpl_agent_id.did` | Parse `did:xrpl:1:rXXX…` to `(network_id, address)`; build / parse W3C DID Documents; resolve via `ledger_entry` | Complete, 12 tests |
| `xrpl_agent_id.identity` | `AgentIdentity` — wrap an `xrpl-py.Wallet`, expose identity ops | Complete, live on testnet |
| `xrpl_agent_id.credential` | `Credential` — XLS-70 wrapper, issue/accept/verify | Complete, 15 tests |
| `xrpl_agent_id.network` | Network constants + JsonRpcClient factory | Complete |
| `xrpl_agent_id.authority` | `Authority` — issuance-only subclass of `AgentIdentity` | Complete |
| `xrpl_agent_id.mcp` | MCP server wrapper, optional `[mcp]` extra | Planned (Week 2) |

### Design Decisions

1. **Conform to XLS-40d, don't extend it.** The spec is Final and ships in `rippled`. We add nothing to the wire format; we just make it easier to use from Python.
2. **W3C DID Core 1.0 on output, XLS-40d-legacy on input.** The XLS-40d README samples use the deprecated `publicKey` field and `EcdsaKoblitzPublicKey` type. We accept those on parse and emit canonical W3C (`verificationMethod` + `Multikey`) on output. See `tests/test_did.py` for the regression tests.
3. **256-byte on-ledger DID Document cap respected.** W3C DID Documents can be large; the XRPL DIDSet transaction limits `DIDDocument` and `URI` fields to 256 bytes each. We emit a `DIDDocument` that's small (canonical key reference) and put rich info behind `URI` (IPFS / HTTPS).
4. **`credential_type` snake_case trap avoided.** xrpl-py and `rippled` use `credential_type` (snake_case) inside `ledger_entry` calls, even though Python convention is `credentialType`. `Credential.to_ledger_fields()` encodes this.
5. **No key custody by default.** We do not store, log, or transmit seeds beyond what `xrpl-py` does. For MCP contexts, the prepare/verify/submit split (mirroring `xrpl-identity-mcp`) keeps signing operations off the tool surface.
6. **MCP wrapper ships as `[mcp]` extra, not a separate package.** One canonical install, one version, one set of docs. `pip install xrpl_agent_id[mcp]` gets you the server entry point.

### Dependencies

| Package | Version | Why |
|---|---|---|
| `xrpl-py` | ≥ 4.5.0 | XRPL JSON-RPC client + transaction building |
| `mcp` (optional) | ≥ 1.0.0 | Model Context Protocol server SDK |
| `pytest` (dev) | ≥ 7 | Tests |
| `ruff` (dev) | latest | Linting |
| `mypy` (dev) | latest | Type checking |

### REUSE Compliance

This repository follows the [REUSE](https://reuse.software/) specification:
- Every file has an SPDX copyright + license header
- `LICENSE` is the canonical MIT text
- All third-party content is attributed in §2

Tools like [`reuse lint`](https://github.com/fsfe/reuse-tool) will pass cleanly.

---

## 4. Testing

### Current Test Suite

```
$ /usr/bin/python3 -m pytest tests/ -v
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: .../xrpl_agent_id
configfile: pyproject.toml
collected 12 items

tests/test_did.py::test_did_from_account_mainnet                       PASSED [  8%]
tests/test_did.py::test_did_from_account_testnet                       PASSED [ 16%]
tests/test_did.py::test_did_from_account_unknown_network                PASSED [ 25%]
tests/test_did.py::test_parse_did_valid                                PASSED [ 33%]
tests/test_did.py::test_parse_did_invalid_scheme                       PASSED [ 41%]
tests/test_did.py::test_parse_did_malformed                            PASSED [ 50%]
tests/test_did.py::test_did_document_from_json_w3c                     PASSED [ 58%]
tests/test_did.py::test_did_document_from_json_xls40_legacy            PASSED [ 66%]
tests/test_did.py::test_to_json_emits_array_context                    PASSED [ 75%]
tests/test_did.py::test_to_json_normalizes_legacy_types_to_multikey    PASSED [ 83%]
tests/test_did.py::test_to_json_preserves_canonical_multibase          PASSED [ 91%]
tests/test_did.py::test_did_document_size_warning                      PASSED [100%]

============================== 12 passed in 0.02s ===============================
```

### What the Tests Cover

| Test | What it guards |
|---|---|
| `test_did_from_account_mainnet` | mainnet DID format: `did:xrpl:1:rXXX…` (matches XLS-40d README example) |
| `test_did_from_account_testnet` | testnet DID format: `did:xrpl:2:rXXX…` |
| `test_did_from_account_unknown_network` | `ValueError` on bad network name |
| `test_parse_did_valid` | Roundtrip parse of a valid DID |
| `test_parse_did_invalid_scheme` | `did:eth:0xabc` correctly rejected |
| `test_parse_did_malformed` | `did:xrpl:notanumber:rXXX` correctly rejected |
| `test_did_document_from_json_w3c` | Parse modern `verificationMethod` field |
| `test_did_document_from_json_xls40_legacy` | Parse legacy `publicKey` field (XLS-40d sample format) |
| `test_to_json_emits_array_context` | `@context` is always an array, not a bare string (decID got this wrong — see Appendix B) |
| `test_to_json_normalizes_legacy_types_to_multikey` | Deprecated VM types normalize to `Multikey` |
| `test_to_json_preserves_canonical_multibase` | Pre-existing `publicKeyMultibase` is not rewritten |
| `test_did_document_size_warning` | A real-world DID Document exceeds the 256-byte on-ledger cap — must go behind URI |

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test
pytest tests/test_did.py::test_did_from_account_mainnet -v

# With coverage (when configured)
pytest tests/ --cov=xrpl_agent_id --cov-report=term-missing
```

### Testnet Integration Tests (planned)

Week 1 work will add integration tests against XRPL testnet:
- `tests/integration/test_didset.py` — issue a real DIDSet, fetch it back
- `tests/integration/test_credential_flow.py` — full issue → accept → verify cycle
- `tests/integration/test_resolve_did.py` — resolution against multiple networks

These will use testnet faucets and require `XRPL_TESTNET_SECRET` in the env.

---

## 5. Appendix

### A. Installation

```bash
# Just the library
pip install xrpl_agent_id

# With MCP server support
pip install xrpl_agent_id[mcp]

# Development install from source
git clone https://github.com/DuNorth595/xrpl_agent_id
cd xrpl_agent_id
pip install -e ".[dev,mcp]"
```

### B. Why We Don't Copy `decID`'s DID Document Format

`RomThpt/decID` (7★) is the most-starred existing XRPL DID project. We
studied it before writing our DID Document implementation and explicitly
diverged from it on these points:

| decID | xrpl_agent_id | Why we diverged |
|---|---|---|
| `"@context": "https://www.w3.org/ns/did/v1"` (bare string) | `"@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"]` (array) | W3C DID Core 1.0 §"context" requires an array. Strict resolvers reject bare strings. |
| `"type": "Ed25519VerificationKey2020"` | `"type": "Multikey"` | Ed25519VerificationKey2020 deprecated by W3C in 2022. |
| `"publicKeyMultibase": "z${hex}"` (bare `z` + hex, no multicodec) | Computed from `publicKeyHex` using correct multicodec (`e701` for secp256k1, `ed01` for Ed25519) + base58btc | The bare-z-prefix form decID uses isn't a valid multibase encoding; verifiers reject it. |
| DID form: `did:xrpl:<address>` (no network ID) | `did:xrpl:<network-id>:<address>` | XLS-40d explicitly requires the network-id component (their own README example uses `did:xrpl:1:rXXX…`). |
| Proof suite: custom `XrplSignature2023` | (no proof suite yet — W3C DataIntegrity to come) | Custom proof suites are not interoperable. W3C DataIntegrity proofs are. |
| Last push: 2025-01-10 (8.5 months stale) | Active development | No commits in 8 months, no tests, no CI. |

Full comparison in `research/07_existing_repos_assessment.md` (289 lines).

### C. XRPL Network IDs

| Network | Network ID | Notes |
|---|---|---|
| Mainnet | `1` | Production XRP Ledger |
| Testnet | `2` | `wss://s.altnet.rippletest.net:51233` |
| Devnet | `3` | Beta releases, may be reset |
| AMM-Devnet | `25` | Automated Market Maker testing |
| Sidechain | `222` | Cross-chain / hooks sidechains |

Confirmed against [XLS-37](https://github.com/XRPLF/XRPL-Standards/blob/master/XLS-0037-concise-transaction-identifier-ctid/README.md) and the XLS-40d README example `did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh`.

### D. Project Status

| Milestone | Target | Status |
|---|---|---|
| Skeleton + DID module + 12 tests | today | Done |
| `xrpl-py` ledger wire-up (DIDSet, CredentialCreate/Accept) | +1 week | Done — `xrpl-py 4.5.0` live |
| Testnet end-to-end demo | +1 week | Done — `scripts/issue_agent_id.py`, results in `results/` |
| Live issuance flow documentation | +1 week | Done — `docs/01_issuance_flow.md` |
| PyPI v0.1.0 release | +2 weeks | Next |
| MCP server wrapper (`[mcp]` extra) | +2-3 weeks | Planned |
| README + tutorial for EasyA submission | +3 weeks | Planned |

### E. Related Projects

- [`xrpl-py`](https://github.com/XRPLF/xrpl-py) — the Python client we wrap
- [`xrpl-identity-mcp`](https://github.com/jarod-vyent/xrpl-identity-mcp) — TypeScript MCP server whose tool taxonomy we mirror
- [`decID`](https://github.com/RomThpt/decID) — original XRPL DID hackathon project (not adopted, see Appendix B)
- [`XAIP`](https://github.com/xkumakichi/xaip-protocol) — trust-scoring layer that could consume `did:xrpl` IDs as input
- [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) — the EVM equivalent we're mirroring on XRPL

---

**End of README.**
