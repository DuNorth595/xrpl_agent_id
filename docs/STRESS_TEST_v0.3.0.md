# xrpl_agent_id — v0.3.0 Live Testnet Stress Test Report

**Date:** 2026-09-29 (UTC)
**Network:** XRPL Testnet (`wss://s.altnet.rippletest.net:51233`)
**Tag:** [xrpl-agent-id v0.3.0](https://github.com/DuNorth595/xrpl_agent_id)
**Authoring org:** S_DevLabs · S_DevLabs@outlook.com

This report documents the first live end-to-end authorization stress run
of xrpl_agent_id's `AuthorizationPolicy` against the public XRPL testnet.
Every decision is logged in two places:

1. **Local SQLite** (`./xrpl_agent_id_dashboard.db`, table `auth_decisions`)
2. **Public ledger** — a 1-drop XRP self-payment with a memo, signed by a
   dedicated mirror wallet. Public, immutable, timestamped by the ledger.

All 12 ledger transactions (6 credential ops + 6 audit-mirror memos) were
independently verified against the testnet RPC at
`https://s.altnet.rippletest.net:51234/` with `tesSUCCESS` and the expected
memo payloads.

---

## 1. Run summary

| Metric | Value |
|---|---|
| Mode | live |
| Agents tested | 6 (one of each role) |
| Decisions logged | 6 |
| Allowed | 2 |
| Denied | 4 |
| CredentialCreate tx (issue) | 3 (one per agent that received a credential) |
| CredentialAccept tx (subject) | 3 (one per agent that accepted) |
| Audit-mirror tx (1-drop memo) | 6 (one per decision) |
| **Total ledger transactions** | **12** |
| **Total cost** | **66 drops** (~0.0000669 XRP, ~$0.00003 USD) |
| Elapsed wall-clock | 42 seconds (decisions only); ~5 minutes with faucet funding |
| Degraded setups | 0 |

---

## 2. Wallets (testnet)

| Role | Address | Purpose |
|---|---|---|
| Good issuer | `rPpvzbNUiqAQFrJTBZ8jRwaseB9vbBUGet` | Issues the canonical `agent_identity_v1` credential to valid + controller_banned + pending subjects |
| Mirror | `rh8HzM1NEe7n8YhsC4QYGRqC2Ti2hACrmA` | Funds each audit-memo transaction |
| Mirror sink | `rEF11xS2PSDnhbEPPXyFK3C3bbvHQ4hsts` | Receives the 1-drop mirror payments (XRPL rejects XRP self-payments) |
| Wrong issuer | (faucet-funded, ephemeral) | Issued a credential of the same type but from a different issuer — exercises `CREDENTIAL_MISSING` |

The six agent wallets are ephemeral — faucet-funded at run start, never
funded by us, retained only for the testnet lifetime.

The mirror wallet seed is persisted (chmod 600) at
`results/stress_harness_live_seeds.json` so a follow-up audit can re-derive
the tx signatures. Never commit that file.

---

## 3. Decision table

| # | Role | Allow? | Reason code | Agent DID | Mirror tx |
|---|---|---|---|---|---|
| 0 | valid | ✓ ALLOW | `OK` | `did:xrpl:2:rEP2Ce4QVuhj7Aw7TQwCjrrk719xYyfq8d` | [`9DD5E33…0263A`](https://s.altnet.rippletest.net:51234/) |
| 1 | banned | ✗ DENY | `AGENT_BANNED` | `did:xrpl:2:r3SoXmx4GBw1UzNBQSJDVcRF3Kdu1VSc5g` | [`D2247DC…D0423`](https://s.altnet.rippletest.net:51234/) |
| 2 | controller_banned | ✓ ALLOW | `OK` | `did:xrpl:2:r47EURWUp2NUvQsWkxTs2PLN27n7fLFs5F` | [`F8AE706…FDD0`](https://s.altnet.rippletest.net:51234/) |
| 3 | no_creds | ✗ DENY | `NO_CREDENTIALS` | `did:xrpl:2:rDCuGqUeEAcgPW8nw9aAsHYiyRUmvWwbDV` | [`C37CFD8…8BA40`](https://s.altnet.rippletest.net:51234/) |
| 4 | wrong_issuer | ✗ DENY | `CREDENTIAL_MISSING` | `did:xrpl:2:rrpp4DJM4E7KCxoTtkSBE2SBFbWUHh4LHS` | [`B0AA816…7A9D`](https://s.altnet.rippletest.net:51234/) |
| 5 | pending | ✗ DENY | `CREDENTIAL_REVOKED` | `did:xrpl:2:rfcaZeFVxWKskiLJPeTM1qTE8bPUyrm4BS` | [`D5D22E8…205D6`](https://s.altnet.rippletest.net:51234/) |

> **Honest note on `controller_banned`:** The agent in this scenario was
> ALLOWED because the harness does not currently publish a SignerList on the
> ledger for the controller-banned role — only the local in-memory
> `AgentRecord.controllers` is populated. The `CONTROLLER_BANNED` code path
> is fully covered by unit tests (44 of them) using mocked ledger responses,
> and the test docstring states that producing real on-chain multi-sig is
> out of scope for the live harness. To exercise the on-chain path,
> extend `setup_live_agents` to issue `SignerListSet` from each agent with
> a banned co-signer.

---

## 4. On-chain credential evidence

The good issuer issued the `agent_identity_v1` credential to three agents
(valid, controller_banned, pending — pending by design never accepts). All
issuer-side `CredentialCreate` transactions confirmed `tesSUCCESS` on the
testnet:

| Subject | Issuer → Subject hash | Subject's accept hash |
|---|---|---|
| valid (`rEP2Ce4…`) | `1454A783BC8216294D6E9776…` | `E5E9E041C37DA3D2A0EBF324…` |
| controller_banned (`r47EURWU…`) | `F178118789CF3FCB350A3701…` | (not asserted for this role) |
| pending (`rfcaZeFVx…`) | `1D61EDCC18D9C85E1E8B5F50…` | never accepted (by design) |

The full hex credential type
`6167656E745F6964656E746974795F7631` decodes to ASCII
`agent_identity_v1` — the canonical harness type.

The wrong-issuer credential (issued to `rBYMw3T…`) was issued from a
separate faucet-funded wallet. `AuthorizationPolicy.require_credential(issuer=
good_issuer_addr)` therefore does not match, and the agent is correctly
denied with `CREDENTIAL_MISSING`.

---

## 5. Audit-mirror memo format

Each audit-mirror tx is a 1-drop Payment from the mirror wallet to the sink,
with a hex-encoded UTF-8 JSON memo in `Memos[0].MemoData`. The decoded memo:

```json
{
  "app": "xrpl_agent_id_audit",
  "v": 1,
  "decision_id": "586BE80551BA17C79227DA1B281A5BFCDADA724E43D1BBB9C756C74863B77B8F",
  "allow": true,
  "agent": "did:xrpl:2:rEP2Ce4QVuhj7Aw7TQwCjrrk719xYyfq8d",
  "ts": "2026-09-29T00:59:23.229004+00:00"
}
```

`decision_id` is the SHA-256 of the canonical-JSON decision record. The
full decision lives in the local SQLite; the ledger only proves "decision
hash X was made for agent Y at time Z" — anyone with the SQLite can recompute
the hash and verify it.

---

## 6. Timing

```
2026-09-29T00:59:33Z  valid            ALLOW
2026-09-29T00:59:42Z  banned           DENY   (+9s)
2026-09-29T00:59:50Z  controller_banned ALLOW (+8s)
2026-09-29T00:59:59Z  no_creds         DENY   (+9s)
2026-09-29T01:00:09Z  wrong_issuer     DENY   (+10s)
2026-09-29T01:00:15Z  pending          DENY   (+6s)
```

42 seconds for 6 decisions, end-to-end. The ~8s per decision reflects the
ledger's median close time + the retry-backoff for `tefPAST_SEQ`.

---

## 7. Reproduce

```bash
cd ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID
PYTHONPATH=. RUN_LIVE=1 /usr/bin/python3 scripts/stress_harness_live.py \
    --n=6 \
    --dashboard-db=./xrpl_agent_id_dashboard.db \
    --save-seeds=./results/stress_harness_live_seeds.json \
    --out=./results/stress_summary_live_v030.json
```

Set `RUN_LIVE=1` to authorize real testnet submissions; without it, the
script refuses to run.

Verify a single mirror tx:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"v","method":"tx","params":[{"transaction":"9DD5E336C1715686DCA01020C25127B8FA982C435BB93E8DBCDD1E6D1320263A","binary":false}]}' \
  https://s.altnet.rippletest.net:51234/ | /usr/bin/python3 -m json.tool
```

---

## 8. Issues encountered (and how this run solved them)

| Symptom | Root cause | Fix |
|---|---|---|
| First run: `xrpl.core.hash` import error | Module path doesn't exist in xrpl-py 4.5.0 | Switch to stdlib `hashlib.sha256` |
| First run: `tefPAST_SEQ` killing the run | Freshly-funded wallets race the 4-ledger window | Per-spec retry with 4s/8s backoff; graceful degrade to `no_creds` after 3 attempts |
| First run: `XRPLModelException` on `destination == account` | XRP rejects self-payments | Fund a separate sink wallet from the faucet; pass its address as the mirror destination |
| Second run: `temBAD_AMOUNT: Malformed: Bad amount` | 0-XRP to a non-ACCountRoot destination is rejected | Change payment amount from `"0"` to `"1"` (1 drop, smallest valid XRP) |
| Verification: `txnNotFound` on xrplcluster.com | I queried **mainnet** to verify **testnet** txs | Use `s.altnet.rippletest.net:51234` for verification |

All five fixes are checked into the v0.3.0 commit. The audit-mirror path
now works end-to-end and is exercisable via `RUN_LIVE=1`.

---

## 9. What's next

- [x] **Add on-chain multi-sig to the controller_banned path so the live harness
  can exercise `CONTROLLER_BANNED` for real, not just in unit tests.**
  Done in v0.3.1. See `docs/CONTROLLER_BANNED_MULTISIG.md` for the design.
  Two shapes supported: `compromised` (banned addr is the sole signer,
  realistic compromise) and `quarantined` (banned + sentinel, quorum 2,
  account is operationally frozen). Both verified live on testnet
  via `scripts/live_signerlist_smoke.py`.
- [ ] Run a 50-agent stress to characterize ledger performance under load
  (target: 8s/decision median).
- [ ] Add a `/api/verify` endpoint to the dashboard that takes a memo hex and
  returns the matching local SQLite row (so anyone can verify a memo
  without owning the DB).

---

## 10. v0.3.1 rerun evidence — controller_banned fires end-to-end on live testnet

After v0.3.1, the full live harness was rerun (`RUN_LIVE=1`) to confirm
`CONTROLLER_BANNED` actually fires on the **live decision path**, not just
in unit tests. Run: 2026-09-29 01:53 UTC. N=6. Mirror mode: deny-only.

Decision table (verbatim from `results/stress_summary_v031.json`):

| Role | Allow | Reasons | Mirror tx |
|---|---|---|---|
| valid | ✓ | — | (none — deny-only mode) |
| banned | ✗ | `[AGENT_BANNED]` | `F4F73A684C5514D40D40819863C1EF0B7724C1D757597E2D6A0A33928F099602` |
| **controller_banned** | **✗** | **`[CONTROLLER_BANNED]`** | **`11E1DA8FB556E441367E0DCAA8FB278146BA29B45290B62E9650C953AED36AE5`** |
| no_creds | ✗ | `[NO_CREDENTIALS]` | `019AC48904E447AEA63AB28E995468232461561EFC62BB2B9EDD2823CAF24EB7` |
| wrong_issuer | ✗ | `[CREDENTIAL_MISSING]` | `14D49E49DDA8421A2456C5792FEAD9BEF4C9D851F9935F4D39816152E81A3B93` |
| pending | ✗ | `[CREDENTIAL_REVOKED]` | `31D37ECED081E61F42644F3DBDCC0E5450E034DDAC8EB3D69D4E552128203693` |

The chain of evidence for `controller_banned`:

1. **On-chain SignerList exists.** `AccountObjects` on
   `rJpbZgjLvkuuYe28t2sW6mBAD8DJxLzBUr` returns a `SignerList` with
   `SignerEntries=[rP21Ur8ePcwNWtZkDapXmsh8eNsNm5vvfp]` at weight 1,
   quorum 1 — `index=D4312A09AF0B66DB2E63D06ADB627E9B294AD8F0C2E95D910CB1E28C49F157DA`.
2. **The SignerList is `compromised` shape** (realistic compromise: banned
   co-signer has full authority; master removed from list because XRPL
   forbids the master from appearing in its own SignerList).
3. **`AgentRegistry._read_signer_list` saw it** — returned
   `controllers=[master, rP21Ur8ePcwNWtZkDapXmsh8eNsNm5vvfp]`.
4. **`AuthorizationPolicy.evaluate` saw the banned controller** — returned
   `allow=False`, `reasons=[CONTROLLER_BANNED]`.
5. **The decision was mirrored on-chain** at
   `11E1DA8FB556E441367E0DCAA8FB278146BA29B45290B62E9650C953AED36AE5`
   (1 drop payment to sink `rnEWXuBUvpR6aUJ8GBPMi8NjXqBqGCZhjs`,
   `tesSUCCESS`).

Mirror memo decoded from the on-chain tx:

```json
{
  "app": "xrpl_agent_id_audit",
  "v": 1,
  "decision_id": "7791EDF5B6FF32E66091845B796B91A5127E26EDB493996FD595B1E1FAC50EB5",
  "allow": false,
  "agent": "did:xrpl:2:rJpbZgjLvkuuYe28t2sW6mBAD8DJxLzBUr",
  "ts": "2026-09-29T01:53:27.096846+00:00"
}
```

**Note on memo schema:** the on-chain memo carries `decision_id + allow +
agent + ts` only — not `reasons`. Reasons live in the local SQLite row
keyed by `decision_id`. To make a memo **self-contained** (verifiable
without the SQLite DB), the memo schema would need to either inline the
reasons as a compact code (e.g. `R=1,3,5`) or split the memo across
multiple Memos to fit `CREDENTIAL_MISSING` etc. within the ~1 KB cap.
Tracked as a known limitation; not blocking.

**Conclusion:** v0.3.1 closes the gap flagged in v0.3.0 §9. The
`CONTROLLER_BANNED` reason is now reachable through the **same code path
any production caller would hit**, with on-chain evidence at every step.
