# xrpl_agent_id — End-to-end architecture (v0.3.2)

This document is the single source of truth for how the project fits
together as of the v0.3.2 release. It covers the library, the dashboard,
the stress harness, the audit-mirror path, the build pipeline, and the
test pyramid. Code references are pinned to v0.3.2.

> **Audience.** Engineers joining the project. Read top-to-bottom on day
> one; return to §7 (Stress harness) and §8 (Build) when you need to
> re-run or rebuild.

## Table of contents

1. [Layer model](#1-layer-model)
2. [The library: `xrpl_agent_id/`](#2-the-library-xrpl_agent_id)
3. [The on-chain decision flow](#3-the-on-chain-decision-flow)
4. [The dashboard](#4-the-dashboard)
5. [The audit log + XRPL mirror](#5-the-audit-log--xrpl-mirror)
6. [The `/api/verify` endpoint](#6-the-apiverify-endpoint)
7. [The stress harness](#7-the-stress-harness)
8. [The build pipeline](#8-the-build-pipeline)
9. [The test pyramid](#9-the-test-pyramid)
10. [On-disk layout](#10-on-disk-layout)

---

## 1. Layer model

```
┌─────────────────────────────────────────────────────────────────┐
│  Stress harness                                                 │
│  scripts/stress_harness.py        (offline, sim mode)           │
│  scripts/stress_harness_live.py   (live testnet, end-to-end)    │
│  scripts/stress_harness_scale.py  (50+ agent live, with metrics)│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Authorization layer                                            │
│  xrpl_agent_id/authorization.py    (policy, decisions, reasons) │
│  xrpl_agent_id/trust.py            (composable require/deny)    │
│  xrpl_agent_id/banned.py           (org-level ban list)         │
│  xrpl_agent_id/registry.py         (DID → ledger state lookup)  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Identity primitives                                            │
│  xrpl_agent_id/did.py              (XLS-40d DID parsing + ledger)│
│  xrpl_agent_id/credential.py       (XLS-70 credential wrapper) │
│  xrpl_agent_id/identity.py         (AgentIdentity — a single agent)│
│  xrpl_agent_id/authority.py        (Authority — an issuer)     │
│  xrpl_agent_id/network.py          (NetworkEndpoint + JSON-RPC)│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Audit + mirror                                                 │
│  xrpl_agent_id/audit.py            (SQLite log + on-chain memo)│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Dashboard (read-only consumer)                                 │
│  xrpl_agent_id/dashboard/                                            │
│    db.py        SQLite schema + idempotent migrations           │
│    server.py    stdlib ThreadingHTTPServer, JSON API            │
│    monitor.py   xrpl-py websocket subscriber (live events)      │
│    templates/   HTML / CSS / JS (single-page dark UI)           │
└─────────────────────────────────────────────────────────────────┘
```

The layers are **strict**. Lower layers never import higher ones. The
harness drives the authorization layer; the dashboard reads the audit
log but never drives it. The mirror is the only layer that touches the
ledger on the **write** path; everything else is read-only or library
code.

---

## 2. The library: `xrpl_agent_id/`

### 2.1 Public surface (`__init__.py`)

The package's public surface is intentionally small — callers
`import xrpl_agent_id as xai` and see:

| Name | What it is |
|---|---|
| `AgentIdentity`, `Authority`, `Credential`, `CredentialType`, `DIDDocument` | Identity primitives |
| `NETWORK_IDS`, `NETWORKS`, `did_from_account`, `parse_did`, `resolve_did` | DID helpers |
| `get_client`, `get_network`, `NetworkEndpoint` | Network helpers |
| `TrustRegistry`, `TrustPolicy`, `TrustCheckResult`, `VerificationResult` | Trust layer |

Internal modules (`authorization`, `audit`, `registry`, `banned`,
`dashboard`) are deliberately not re-exported — callers reach for the
auth flow through `xrpl_agent_id.trust.TrustRegistry.check()` or the
canonical policy in `xrpl_agent_id.authorization.AuthorizationPolicy`.

### 2.2 `xrpl_agent_id/did.py`

XLS-40d-compliant DID parser + W3C DID Core 1.0 document builder.

- `did_from_account(network, address)` → `did:xrpl:<network-id>:<address>`
- `parse_did(did)` → `(network_id, address)`
- `DIDDocument.to_json()` emits array `@context` (not bare string), normalizes
  legacy VM types to `Multikey` per W3C Core 1.0
- `resolve_did(did, client)` calls `ledger_entry(did=...)` and synthesizes a
  minimal DID Document when no `DIDDocument` ledger entry exists
- `NETWORK_IDS` map: `mainnet=1`, `testnet=2`, `devnet=3`, `AMM-devnet=25`, `sidechain=222`

### 2.3 `xrpl_agent_id/credential.py`

XLS-70 wrapper. `Credential` is the dataclass; `CredentialType` is the
enum (`AGENT_ID_V1`, `VERIFIED_AGENT_OPERATOR`, `EVAL_PASSED`).

- `Credential.to_ledger_fields()` / `from_ledger_entry()` — round-trip
  on/off the ledger
- `Credential.to_w3c_vc()` — RFC 0112 verifiable-credentials JSON
- Auto hex-encodes `credential_type` (snake_case wire format expected by
  xrpl-py)
- Validates 64-byte `credential_type` cap, 256-byte URI cap per XLS-70

### 2.4 `xrpl_agent_id/identity.py` and `authority.py`

- `AgentIdentity.from_seed(seed, network)` loads an `xrpl-py` Wallet
- `AgentIdentity.issue_credential()`, `.accept_credential()`,
  `.set_did_document()`, `.set_did_uri()` are thin `xrpl-py` wrappers
- `Authority` is the issuance-only subclass — it can issue/revoke
  credentials but cannot accept them
- `Authority.revoke_credential()` uses XLS-70 `CredentialDelete` (NOT a
  second `CredentialCreate` — uniqueness on `(issuer, subject, type)`
  would `tecDUPLICATE`)

### 2.5 `xrpl_agent_id/network.py`

`NetworkEndpoint` is the canonical record describing a JSON-RPC endpoint;
`get_client(name)` returns a memoized `xrpl.clients.JsonRpcClient`.

### 2.6 `xrpl_agent_id/registry.py`

`AgentRegistry` is the bridge between the authorization layer and the
ledger.

- `AgentRegistry.resolve(agent_did)` returns an `AgentRecord` containing:
  - account info (`Balance`, `OwnerCount`, `Sequence`)
  - controllers via `AccountObjects type=signer_list` (multi-sig aware)
  - credentials via `AccountObjects type=credential`
- `AgentRegistry` is a thin read-side facade. Caching is in-memory and
  unbounded today; per-call refresh is a TODO tracked in `docs/NOTES.md`

### 2.7 `xrpl_agent_id/banned.py`

`BannedAgentRegistry` is the org-level deny list.

- Each entry is a `Ban(address, reason, added_by, expires_at)`
- JSON file persistence at `results/bans_<network>.json` (loaded/saved
  on each call site, no DB)
- Used directly by `AuthorizationPolicy` for the `AGENT_BANNED` and
  `CONTROLLER_BANNED` code paths

### 2.8 `xrpl_agent_id/authorization.py`

The **canonical policy**. Everything else funnels through this.

```python
policy = AuthorizationPolicy(bans=bans)
policy.require_credential(issuer=good_issuer_addr, credential_type=b"agent_identity_v1")
decision = policy.evaluate(agent_address, context=RequestContext(resource="/api/x"))
assert decision.summary() == "deny: CONTROLLER_BANNED"
```

The `evaluate()` algorithm (single pass):

1. Check `BannedAgentRegistry` → if agent address banned → `AGENT_BANNED` deny
2. Resolve `AgentRecord` via `AgentRegistry` → if missing → `NO_CREDENTIALS` deny
3. Resolve controllers from the SignerList → if any controller banned → `CONTROLLER_BANNED` deny
4. Check `require_credential` rules → if missing/expired/revoked → corresponding deny
5. Check `deny_credential_type` rules → if held → corresponding deny
6. Otherwise → `OK` allow

Reason codes (stable string enum):
- `OK`
- `AGENT_BANNED`
- `CONTROLLER_BANNED`
- `NO_CREDENTIALS`
- `CREDENTIAL_MISSING`
- `CREDENTIAL_REVOKED`
- `STALE_CREDENTIAL`

`AuthorizationDecision.to_dict()` is canonical-JSON-safe (sort_keys,
stable) so `compute_decision_id()` is deterministic.

### 2.9 `xrpl_agent_id/trust.py`

A higher-level composable API sitting on top of `AuthorizationPolicy`.
Used by application code that wants to ask "does this agent have the
trust profile I need?" rather than the lower-level "deny this because X."

```python
registry = TrustRegistry()
registry.require(TrustPolicy(credential_type=b"agent_identity_v1", issuer=GOOD_ISSUER))
registry.deny(TrustPolicy(credential_type=b"banned_credential", issuer=...))
result = registry.check(agent_did)  # TrustCheckResult with .satisfied, .missing_required, .denied_held
```

### 2.10 `xrpl_agent_id/audit.py`

The only module that writes to the audit DB or the ledger on the
**mirror** path. See §5.

---

## 3. The on-chain decision flow

A single `policy.evaluate(agent_did)` call is the public surface. The
rest is plumbing:

```
                 ┌───────────────────────────────┐
                 │ caller code                   │
                 │  AuthorizationPolicy.evaluate│
                 └───────────┬───────────────────┘
                             │
                             ▼
   ┌────────────────────────────────────────────┐
   │ 1. agent_address in BannedAgentRegistry?  │ ─── yes ──▶ AGENT_BANNED
   └──────────────┬─────────────────────────────┘
                  │ no
                  ▼
   ┌────────────────────────────────────────────┐
   │ 2. AgentRegistry.resolve(agent_did)        │ ─── miss ─▶ NO_CREDENTIALS
   │    • account_info                          │
   │    • AccountObjects(type=signer_list)      │ ─── any banned? ─▶ CONTROLLER_BANNED
   │    • AccountObjects(type=credential)       │
   └──────────────┬─────────────────────────────┘
                  │ ok
                  ▼
   ┌────────────────────────────────────────────┐
   │ 3. for each require_credential rule:      │
   │      any matching Credential in registry?  │ ─── no ──▶ CREDENTIAL_MISSING
   │      status==revoked?                      │ ─── yes ─▶ CREDENTIAL_REVOKED
   │      expired?                              │ ─── yes ─▶ STALE_CREDENTIAL
   └──────────────┬─────────────────────────────┘
                  │ all pass
                  ▼
   ┌────────────────────────────────────────────┐
   │ 4. for each deny_credential_type rule:     │
   │      agent holds it?                       │ ─── yes ─▶ corresponding DENY
   └──────────────┬─────────────────────────────┘
                  │ no
                  ▼
                OK (allow)
```

Every `AuthorizationDecision` is then handed to the audit log (§5). The
audit log is responsible for SQLite write + optional XRPL mirror.

---

## 4. The dashboard

### 4.1 What it is

A stdlib-only `ThreadingHTTPServer` on port `8768` serving a single-page
HTML+CSS+JS UI that polls `/api/*` every 2 seconds. No external
dependencies — no Flask, no FastAPI, no WebSockets on the server side.
The XRPL websocket subscriber (`monitor.py`) is the only async piece,
and it runs in its own thread.

### 4.2 On-disk layout

```
xrpl_agent_id/dashboard/
├── __init__.py
├── db.py          # SQLite schema (idempotent migrations via _migrate())
├── monitor.py     # xrpl-py websocket subscriber, writes to DB
├── server.py      # ThreadingHTTPServer, JSON API
├── templates/
│   └── index.html
└── dashboard.js   # SPA shell, polls /api/* every 2s
```

### 4.3 API surface (v0.3.2)

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | Counts: identities, credentials, watchlist, decisions |
| `GET /api/credentials?limit=N` | Recent credential events |
| `GET /api/identities?limit=N` | Recent DIDSet events |
| `GET /api/watchlist` | Tracked agents with role tags |
| `GET /api/agent_state?did=…` | Full ledger state for one agent |
| `GET /api/files` | Project file tree (read-only) |
| `GET /api/health` | Recent websocket events |
| `GET /api/authz/events?limit=N&agent_did=…&allow=…` | Recent auth decisions |
| `GET /api/authz/stats?since_seconds=N` | Aggregated allow/deny counts |
| **`GET /api/verify?memo_hex=…`** | Decision row + decoded memo (v0.3.2) |
| **`GET /api/verify?tx_hash=…&network=testnet\|mainnet`** | Decision row + on-chain proof (v0.3.2) |

### 4.4 DB schema (key tables)

```sql
CREATE TABLE identity_events (...);     -- DIDSet / DIDDelete
CREATE TABLE credential_events (...);   -- CredentialCreate / Accept / Delete
CREATE TABLE watchlist (...);           -- tracked agents by role (subject/issuer/evaluator)
CREATE TABLE monitor_events (...);      -- raw websocket events
CREATE TABLE auth_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT,                   -- v0.3.2 — SHA-256 of canonical JSON decision
    agent_did TEXT NOT NULL,
    allow INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    request_json TEXT NOT NULL,
    extra_json TEXT,
    mirrored_tx TEXT,                   -- on-chain mirror tx hash (if any)
    evaluated_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX idx_auth_decisions_decision_id ON auth_decisions(decision_id);
CREATE INDEX idx_auth_decisions_mirrored_tx  ON auth_decisions(mirrored_tx);
```

`_migrate()` runs before `CREATE INDEX IF NOT EXISTS` on `open_db()`, so
upgrading from v0.3.1 → v0.3.2 just adds the `decision_id` column
without manual SQL.

### 4.5 Running it

```bash
RUN_LIVE=1 bash scripts/restart_dashboard.sh
# server: http://127.0.0.1:8768  (UI + /api/*)
```

`restart_dashboard.sh` kills any stale PID, then launches the server
in the background with the current source tree, redirecting stdout to
`results/dashboard.log`.

---

## 5. The audit log + XRPL mirror

### 5.1 Two storage tiers, one write

Every decision flows through `AuditLog.record(decision, extra=...)`:

1. Compute `decision_id = sha256(canonical_json(decision.to_dict())).hexdigest().upper()`
2. INSERT into `auth_decisions` (decision_id + reasons + request + extra + timestamp)
3. If `xrpl_mirror` is configured AND policy allows mirroring (e.g. deny-only mode), submit a 1-drop payment to the sink with the memo

```python
memo = {
    "app": XRPLMirror.APP_TAG,        # "xrpl_agent_id_audit"
    "v":   XRPLMirror.SCHEMA_VERSION, # 1
    "decision_id": <sha256 hex>,
    "allow": bool,
    "agent": "did:xrpl:2:r...",
    "ts":   <iso-8601>,
}
memo_hex = json.dumps(memo, separators=(",", ":")).encode().hex().upper()
```

The on-chain memo is intentionally minimal. It contains a foreign key
(`decision_id`) into the local SQLite DB, plus the four fields that
matter most for at-a-glance verification. Reasons and the full decision
context stay local — putting them on-chain would either bloat the memo
(~1 KB cap) or require splitting across multiple `Memos[]` entries.

`/api/verify` (v0.3.2) is the bridge that resolves the foreign key.
See §6.

### 5.2 Cost

`XRPLMirror.submit()` costs ~15 drops + small fee buffer per decision
on testnet. For a 50-agent run with full mirroring that's 50 mirror txs
≈ 750 drops ≈ 0.000750 XRP ≈ $0 (testnet). On mainnet the same would
be ~$0.00015 at typical rates.

### 5.3 Why 1 drop, not 0?

XRPL rejects 0-XRP to non-`ACCountRoot` destinations with `temBAD_AMOUNT`.
The fix is either: (a) pay 1 drop to a non-root sink (chosen here), or
(b) pay 0 to the root address `rrrrrrrrrrrrrrrrrrrrrrrrLvLvTp` (the
memo lands but XRP fee is burned).

(a) keeps the fees recoverable on testnet (the sink can be re-funded).

### 5.4 Why a separate sink wallet?

XRPL rejects self-payments for XRP. The mirror wallet can't pay itself.
Funding a separate sink alongside the mirror is the canonical pattern
(see `stress_harness_live.py::_submit_with_retry`).

---

## 6. The `/api/verify` endpoint

Added in v0.3.2 to resolve the §10 "known limitation" flagged in the
v0.3.1 stress report.

### 6.1 The contract

```
GET /api/verify?memo_hex=<hex>
GET /api/verify?tx_hash=<hash>&network=testnet|mainnet
```

Returns a JSON envelope with `verified`, `lookup_via` (`decision_id` or
`mirrored_tx fallback`), `decision_id`, `memo` (decoded), `decision`
(row contents — agent_did, reasons, extra, mirrored_tx), and (when
called with `?tx_hash`) `on_chain_proof` (tx hash, network, explorer URL).

Error matrix:

| Input | HTTP | Body |
|---|---|---|
| valid `memo_hex` w/ matching row | 200 | full envelope |
| valid `memo_hex` w/o matching row | 404 | `{"error": "decision not found locally"}` |
| valid `memo_hex`, wrong `app` tag | 400 | `{"error": "memo is not an xrpl_agent_id_audit memo (app=...)"}` |
| invalid `memo_hex` | 400 | `{"error": "invalid memo_hex"}` |
| valid `tx_hash` w/ row | 200 | full envelope + `on_chain_proof` |
| valid `tx_hash` w/o row | 404 | `{"error": "decision not found locally"}` |
| bogus `tx_hash` | 404 | `{"error": "tx not found on <network>"}` |
| unreachable XRPL RPC | 502 | `{"error": "xrpl rpc error: ..."}` |
| missing both args | 400 | `{"error": "must provide memo_hex or tx_hash"}` |
| both args | 400 | `{"error": "provide exactly one of memo_hex or tx_hash"}` |

### 6.2 The lookup algorithm

```
if tx_hash:
    fetch tx via testnet.xrpl.org / xrplcluster.com JSON-RPC
    extract memos[0].MemoData → hex-decode → utf-8 → json.loads
    validate memo.app == "xrpl_agent_id_audit" else 400
    # Foreign key:
    row = SELECT * FROM auth_decisions WHERE decision_id = memo.decision_id
    if not row:
        # Fallback for legacy rows that pre-date the decision_id column:
        row = SELECT * FROM auth_decisions WHERE mirrored_tx = tx_hash
        if not row: 404
elif memo_hex:
    decode memo hex → utf-8 → json.loads
    same validation as above
    same lookup (decision_id first, mirrored_tx fallback)
```

The `mirrored_tx` fallback exists specifically so the v0.3.1 rows
(which have `mirrored_tx` populated but no `decision_id` since the
column didn't exist yet) remain verifiable. New rows after the schema
migration always have `decision_id` populated by `AuditLog.record()`.

### 6.3 End-to-end live demo

```bash
curl -s 'http://127.0.0.1:8768/api/verify?tx_hash=11E1DA8FB556E441367E0DCAA8FB278146BA29B45290B62E9650C953AED36AE5' | jq .
```

Returns the v0.3.1 `controller_banned` decision with
`reasons=[{"code": "CONTROLLER_BANNED", "detail": "controller rP21Ur8ePcwNWtZkDapXmsh8eNsNm5vvfp: live test controller ban"}]`
plus the testnet explorer URL for manual verification.

---

## 7. The stress harness

Three harnesses, all live:

### 7.1 `scripts/stress_harness.py` — offline

`sim` mode only, mocked ledger. Used in CI. Exercises 12 roles against
a deterministic policy. No network.

### 7.2 `scripts/stress_harness_live.py` — live, single run

The canonical live harness. `RUN_LIVE=1 python3 scripts/stress_harness_live.py`
runs `run_live(n=6)` — 6 roles (valid/banned/controller_banned/no_creds/wrong_issuer/pending).
Used for the v0.3.0 + v0.3.1 + v0.3.2 reports.

Key features:
- Single issuer wallet — credentials issued sequentially
- Single mirror wallet — decisions mirrored sequentially
- Graceful degradation on transient ledger errors: agent rolls to
  `no_creds` after 3 retries, summary flags `degraded[]`
- `controller_banned` role supports two `controller_banned_shape` values:
  `compromised` (default) and `quarantined` (banned + sentinel, quorum 2)
- Live testnet: `s.altnet.rippletest.net:51234`

### 7.3 `scripts/stress_harness_scale.py` — live, N≥50

Added for the §9 "50-agent scale run" item. Designed for `n ≥ 50`.

Key features beyond the canonical harness:
- **Two issuer wallets** — good (for valid/pending) and evil (for
  wrong_issuer), so credential setup parallelizes across the two
  wallet sequences
- **Per-agent timing breakdown** — fund / setup / evaluate / mirror
  latencies captured separately
- **Mirror latency stats** — p50/p95/p99 mirror latency in ms
- **Throughput** — `agents_per_minute` in summary

The N=50 setup has 4 funded wallets (good_issuer, evil_issuer, sink,
mirror) + 50 agent wallets = 54 wallets total. Each agent wallet
requires: 1 faucet call + 1 balance poll + 1 CredentialCreate + 1
CredentialAccept + (sometimes) 1 SignerListSet = ~3-5 ledger txs per
agent. The mirror wallet does 50 sequential `submit_and_wait` calls
(each waits for a validated ledger ~3-5s on testnet = 2.5-4 min).

**v0.3.2 50-agent result** (full report in
`docs/STRESS_TEST_v0.3.0.md` §12):

| Metric | Value |
|---|---|
| Agents evaluated | 50 / 50 |
| Mirror txs submitted | 50 / 50 |
| Mirror txs failed | 0 |
| Wall time | 21m 11s |
| Throughput | 2.36 agents/min |
| Evaluate p50 / p95 | 936 ms / 1044 ms |
| Mirror p50 / p95 | 6889 ms / 8790 ms |
| Total/agent p50 / p95 | 7839 ms / 9904 ms |
| Degraded agents | 0 |

### 7.4 `scripts/stress_harness_throughput.py` — pure mirror benchmark

Isolates the **mirror-only** throughput ceiling by stripping out
per-agent setup. Funds 1 sink + N signing wallets and blasts 1000+
synthetic 1-drop payments as fast as the testnet accepts them.

- Single wallet: sequential (xrpl-py sequence numbers are not
  thread-safe per-wallet). One stream → ~7-10s/tx → ~600-900 txs/hour.
- Multi wallet (`--wallets N`): round-robin across N signing wallets,
  each running its own `submit_and_wait` loop in parallel via
  `ThreadPoolExecutor`. Each wallet has its own sequence so no race.
- Synthetic `decision_id` per tx (SHA-256 of `throughput:<nonce>:<index>`),
  not tied to a real `AuthorizationDecision` — the goal is the pure
  ledger-side rate.
- Outputs JSON (per-tx + aggregate stats) + CSV (one row per tx).

### 7.5 Run any harness

```bash
# Canonical 6-agent live run
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_live.py

# 50-agent scale run with timing metrics
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_scale.py --n 50

# Pure mirror throughput, 1000 txs from one wallet
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_throughput.py --n 1000

# 1000 txs across 5 wallets in parallel
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_throughput.py --n 1000 --wallets 5
```

Each writes a JSON summary to `results/` and the scale + throughput
harnesses also write a per-run audit SQLite DB.

---

## 8. The build pipeline

### 8.1 PDFs

Two PDF deliverables, both built from Markdown via `pandoc` →
`Playwright` → Letter PDF with print CSS:

| Script | Source | Output | Notes |
|---|---|---|---|
| `build_pdf.py` | `README.md` | `xrpl_agent_id_README.pdf` | Cover, REUSE-compliant SPDX headers |
| `build_stress_pdf.py` | `docs/STRESS_TEST_v0.3.0.md` | `xrpl_agent_id_STRESS_v030.pdf` | Stress-test cover, §10 + §11 addendum, v0.3.2 pin |

Both inject:
- Custom cover (project name + tagline)
- Custom footer (project name + version + date)
- Print CSS (`@page` size Letter, ~0.5in-equivalent margins, code-block
  styling, table alternation)

Build: `cd <project_root> && /usr/bin/python3 build_pdf.py` (or
`build_stress_pdf.py`).

### 8.2 Telegram delivery

The Telegram Home channel receives the PDFs after each rebuild:

```bash
/usr/bin/python3 /Users/samintelligence/.hermes/scripts/send_telegram_document.py \
    6921445477 \
    ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/xrpl_agent_id_STRESS_v030.pdf
```

### 8.3 Desktop mirror

`scripts/sync_to_desktop.py` copies the project tree to
`~/Desktop/XRPL_AGENT_ID/<version>/` as a read-only archive. Bumps the
folder name on every version increment.

### 8.4 Versioning

`xrpl_agent_id/__version__` is the canonical source. Pinned by:
- `tests/test_trust.py::test_imports` (smoke test)
- Cover/footer in `build_pdf.py` and `build_stress_pdf.py`
- `CHANGELOG.md` (manual bump in the same commit)

Bump rule: patch for bugfixes/small additions, minor for new public
API, major for breaking changes. v0.3.0 → v0.3.1 → v0.3.2 are all patch
bumps.

---

## 9. The test pyramid

### 9.1 Layout

```
┌──────────────────────────────────────┐
│ Live integration                     │  scripts/stress_harness_live.py
│ (XRPL testnet, real txs)             │  scripts/run_trust_scenarios.py
│                                      │  tests/test_integration_ledger_live.py
│                                      │  scripts/live_signerlist_smoke.py
└──────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────┐
│ Unit tests (160/160 passing)         │  tests/test_*.py
│ (mocked ledger, hermetic)            │  • test_credential, test_did, test_trust
│                                      │  • test_authorization, test_audit
│                                      │  • test_dashboard, test_dashboard_verify
│                                      │  • test_registry
│                                      │  • test_stress_harness_live_signerlist
│                                      │  • test_stress_harness_scale
│                                      │  • test_stress_harness_throughput
└──────────────────────────────────────┘
```

### 9.2 Offline tests (145 total)

```bash
/usr/bin/python3 -m pytest -x -q
# 145 passed, 6 skipped in 4.54s
```

The 6 skipped tests are the integration tests that only run when the
testnet is reachable AND `RUN_LIVE=1` is exported.

### 9.3 Live tests

Run on demand, not in CI. The canonical sequence:

```bash
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_live.py           # canonical 6-agent run
RUN_LIVE=1 /usr/bin/python3 scripts/live_signerlist_smoke.py        # SignerListSet verification
RUN_LIVE=1 /usr/bin/python3 scripts/run_trust_scenarios.py          # trust library 11-step
RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_scale.py --n 50 # scale run with metrics
```

---

## 10. On-disk layout

```
~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/
├── xrpl_agent_id/                # the library
│   ├── __init__.py               # public surface, __version__
│   ├── __main__.py               # CLI entry
│   ├── did.py                    # XLS-40d
│   ├── credential.py             # XLS-70
│   ├── identity.py               # AgentIdentity
│   ├── authority.py              # Authority
│   ├── network.py                # JSON-RPC client factory
│   ├── trust.py                  # composable trust layer
│   ├── registry.py               # DID → ledger state
│   ├── banned.py                 # org-level ban list
│   ├── authorization.py          # canonical policy
│   ├── audit.py                  # SQLite + on-chain mirror
│   └── dashboard/                # stdlib HTTP server
│       ├── db.py
│       ├── monitor.py
│       ├── server.py
│       ├── templates/index.html
│       └── dashboard.js
├── tests/                        # 145 offline tests
│   ├── test_did.py               # 12 tests
│   ├── test_credential.py        # 15 tests
│   ├── test_authorization.py     # ~20 tests
│   ├── test_audit.py             # ~15 tests
│   ├── test_trust.py             # ~25 tests (incl. version pin)
│   ├── test_registry.py
│   ├── test_dashboard.py
│   ├── test_dashboard_verify.py  # 12 tests, v0.3.2
│   ├── test_stress_harness.py
│   ├── test_stress_harness_live_signerlist.py   # 9 tests, v0.3.1
│   ├── test_stress_harness_scale.py             # 7 tests, v0.3.2
│   ├── test_stress_harness_throughput.py        # 8 tests, v0.3.2
│   └── test_integration_ledger_live.py
├── scripts/                      # runnable entry points
│   ├── issue_agent_id.py         # canonical live demo
│   ├── run_trust_scenarios.py    # trust library 11-step
│   ├── stress_harness.py         # offline sim
│   ├── stress_harness_live.py    # canonical live harness
│   ├── stress_harness_scale.py   # 50+ agent live (v0.3.2)
│   ├── stress_harness_throughput.py  # pure mirror benchmark (v0.3.2)
│   ├── live_signerlist_smoke.py  # SignerListSet on-chain test
│   ├── backfill_auth_decisions.py # v0.3.2 — results DB → dashboard DB
│   ├── build_summary_pdf.py
│   ├── restart_dashboard.sh
│   └── sync_to_desktop.py
├── docs/                         # source-of-truth documentation
│   ├── 00_executive_summary.{md,html,pdf}
│   ├── 01_issuance_flow.md
│   ├── 02_testing_log.md
│   ├── 03_dashboard.md
│   ├── ARCHITECTURE.md           # this file (v0.3.2)
│   ├── CHANGELOG.md              # version history (canonical)
│   ├── CONTROLLER_BANNED_MULTISIG.md
│   ├── NOTES.md
│   └── STRESS_TEST_v0.3.0.md     # canonical stress test report (§1-§12)
├── build_pdf.py                  # README → PDF
├── build_stress_pdf.py           # STRESS_TEST_v0.3.0.md → PDF
├── pyproject.toml
├── README.md
├── LICENSE
├── xrpl_agent_id_README.pdf
├── xrpl_agent_id_STRESS_v030.pdf
└── results/                      # ephemeral: live run artifacts
    ├── stress_summary_*.json
    ├── stress_summary_scale_n50_*.json
    ├── audit_scale_*.db
    ├── stress_bans_scale.json
    └── dashboard.log
```

Everything in `results/` is gitignored. PDFs at the project root are
regeneratable from `docs/` + the build scripts.
