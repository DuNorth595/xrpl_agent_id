# xrpl_agent_id — Development Notes

Session log for `xrpl_agent_id`. Working entries are at the top.

---

## Session: 2026-09-29 (v0.3.3 planning + use-cases doc)

**Goal:** Lay foundations for v0.3.3 — the release that makes `xrpl_agent_id` presentable to external users (long-term) while keeping the dev-tool character of the dashboard.

**Framing decisions (from yesterday's session):**
- The dashboard (`xrpl_agent_id/dashboard/`, port 8768) is an internal monitoring tool for build & testing — NOT the product. Don't pitch it externally.
- The product is the library: `trust`, `did`, `credential`, `identity`, `authority`, `registry`, `authorization`, `audit`. The value prop is "every decision lands in SQLite. Optionally also on-chain."
- v0.3.2 (this session's parent release) is done: 160/160 tests, 50-agent + 1000-tx runs, stress PDF at `xrpl_agent_id_STRESS_v032.pdf`.

**v0.3.3 scope (6 items):**
1. External use-cases doc — sharpens the library-vs-dashboard framing
2. Quickstart — library-only, no dashboard mention, PDF via existing Playwright pipeline
3. Lock public API surface — `@experimental` decorators on unstable bits
4. `/version` endpoint — single source = `xrpl_agent_id.__version__`
5. One mainnet decision — exercise `/api/verify` on real net, capture proof
6. `pyproject.toml` publish dry-run — catch the "missing long_description" failure before someone else does

**Server decision (off-roadmap):**
- Considering Dell OptiPlex Micro / SFF (used/refurbished, $80-200) running Ubuntu Server LTS + rippled for home node + always-on dev box. Best $/perf for 24/7 desktop server.

**Notes discipline:**
- Session log: top entry here in `NOTES.md`
- Code commits: each v0.3.3 item gets its own commit with conventional message
- Snapshots: refresh `docs_snapshot_<date>/` at v0.3.3 release
- Telegram: PDF of Quickstart + use-cases doc at release time

---

## Session: 2026-09-28 (live testnet wire-up)

**Goal:** Wire up `xrpl_agent_id` against live XRPL testnet, document the issuance flow, verify transparency.

### Setup decisions
- **Wallet roles**:
  - Subject (Agent): `rn3bppvwt7Qc15mQa7EFkbMWa2BjkAtV8X` (USER's existing testnet wallet)
  - Issuer (Authority): `rMB3kkswgAu6bzJ6modhCF1xxDi6ugqFv4` (treasury wallet, acting as credentialing authority)
  - Same user holding both is acceptable for the demo because verification only requires reading the public ledger.

- **GitHub:** https://github.com/DuNorth595/xrpl_agent_id (tagged `v0.2.4`, release with PDF attached)
- **Testnet endpoint**: `wss://s.altnet.rippletest.net:51233`
- **JSON-RPC submit**: `https://s.altnet.rippletest.net:51234`
- **Min fee**: 15 drops on testnet (10 drops base; we use 15 for safety margin)

### Spec notes (re-verified)
- **XLS-40d** (DID method): DIDSet tx creates a DID ledger object bound to Account. Fields: `DIDDocument` (≤256 bytes), `URI` (≤256 bytes), `Data` (hex). Canonical example: `did:xrpl:1:rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh` (mainnet network ID = 1).
- **XLS-70** (Credentials): CredentialCreate signed by Issuer, includes `Subject`, `CredentialType` (hex), optional `Expiration`, `URI` (≤256 bytes). Subject submits CredentialAccept to opt-in (creates a Credential ledger object).
- **Ledger entry lookup**: `ledger_entry` accepts `credential` parameter with `subject` + `issuer` + `credential_type` (all snake_case).
- **`credential_type` trap**: must be hex-encoded for submission; xrpl-py uses snake_case `credential_type` (not `credentialType`).

### Implementation status (before this session)
- `did.py`: full XLS-40d parser + W3C DID Document, 12 tests passing
- `credential.py`: hex encoding, ledger serialization, parsing
- `identity.py`: `AgentIdentity.from_seed()` + signing ops interface
- `authority.py`: `Authority.from_seed()`, `issue_credential()`, `verify_credential()`
- 26/26 offline tests passing

### Next steps
- See `results/` for live testnet run output and on-ledger artifacts
- See `docs/01_issuance_flow.md` (pending) for narrative documentation
