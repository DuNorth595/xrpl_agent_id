# xrpl_agent_id — Testing Log

**Started:** 2026-09-28
**Network:** XRPL Testnet (`wss://s.altnet.rippletest.net:51233`)
**Branch:** `main`
**Test framework:** pytest 8.4.2, Python 3.9.6

---

## How to run

```bash
# Offline tests only (CI-safe, no network)
cd xrpl_agent_id
/usr/bin/python3 -m pytest tests/ -q

# Live tests (hits real testnet)
/usr/bin/python3 -m pytest tests/test_integration_ledger_live.py -v -m ""
# or
RUN_LIVE=1 /usr/bin/python3 -m pytest tests/test_integration_ledger_live.py -v

# End-to-end scenario script
/usr/bin/python3 scripts/run_trust_scenarios.py
```

---

## Test counts (cumulative)

| Date | Offline | Live | Total | Notes |
|---|---|---|---|---|
| 2026-09-28 12:30 | 27 | 4 | 31 | Initial live wire-up |
| 2026-09-28 21:35 | 43 | 6 | 49 | Trust library build-out |

Latest: **43 offline + 6 live = 49 passing**, 0 failures.

---

## Bugs found & fixed (during testing)

### Bug 1: 256-byte on-ledger cap on revocation URI
- **Where:** `Authority.revoke_credential()` (initial implementation)
- **Symptom:** `xrpl.models.exceptions.XRPLModelException: {'uri': 'Length cannot exceed 256 characters.'}`
- **Root cause:** Default URI `revoke://xrpl_agent_id/<type_hex>/<issuer_rXXX>/<subject_rXXX>` was 80+ bytes — fine in theory, but combined with hex encoding and the model's strict 256-byte cap, it tripped.
- **Fix (v1):** shortened default URI to `revoke://<short-issuer-tail>..<short-subject-tail>` with fallback `revoke://xrpl_agent_id`.
- **Status:** v1 fix incomplete — see Bug 2.

### Bug 2: `tecDUPLICATE` on second `CredentialCreate` for same triple
- **Where:** `Authority.revoke_credential()` (initial implementation)
- **Symptom:** `XRPLReliableSubmissionException: Transaction failed: tecDUPLICATE`
- **Root cause:** XLS-70 enforces uniqueness on `(issuer, subject, credential_type)`. A second `CredentialCreate` for the same triple is rejected by the ledger as a duplicate. The initial revocation design (re-issue with a `revoke://` URI) was fundamentally wrong.
- **Fix:** rewrote `revoke_credential()` to use `CredentialDelete` (XLS-70's native deletion mechanism, signable by either issuer or subject). Docstring now explicitly notes that for "softer" revocation (audit trail preserved), set `expiration` at issuance time instead.
- **Lesson:** **read the spec more carefully before implementing** — XLS-70 uniqueness enforcement is a load-bearing design property, not an edge case.

### Bug 3: Test isolation in live suite
- **Where:** `test_06_verify_set_and_trust_registry` (initial)
- **Symptom:** When run in isolation (or after `test_05_revoke_credential` deletes the test_03 credential), the `verify_set` test for "EVAL_PASSED is held" fails.
- **Root cause:** Test assumed state from earlier tests in the same module session; pytest session reloads + test ordering created real fragility.
- **Fix:** `test_06` now issues its own fresh credential (PRODUCTION_READY → VERIFIED_AGENT_OPERATOR after enum check) and accepts it inline. Tests are now self-sufficient.

### Bug 4: `AttributeError: 'NetworkEndpoint' object has no attribute 'url'`
- **Where:** `scripts/run_trust_scenarios.py`
- **Symptom:** Script crashed at startup on the print statement.
- **Root cause:** Used `.url` shorthand; the actual attribute is `.json_rpc_url`.
- **Fix:** Replaced with `network.json_rpc_url`.

### Bug 5: `AttributeError: PRODUCTION_READY`
- **Where:** `tests/test_integration_ledger_live.py` test_06
- **Symptom:** Enum lookup failed — that name didn't exist.
- **Root cause:** Made up a credential type name; the enum has only `AGENT_ID_V1`, `VERIFIED_AGENT_OPERATOR`, `EVAL_PASSED`.
- **Fix:** Used `VERIFIED_AGENT_OPERATOR`. **Lesson: enum-check before assuming.**

### Issue 6: Intermittent `tefPAST_SEQ`
- **Where:** `scripts/run_trust_scenarios.py` (first run)
- **Symptom:** `XRPLReliableSubmissionException: The latest validated ledger sequence N is greater than LastLedgerSequence N`
- **Root cause:** `submit_and_wait` autofill picked up a `LastLedgerSequence` from a cached/prior ledger, then the actual ledger advanced before the tx landed. Race condition between autofill and submission.
- **Fix:** Added 3-second sleep at script start to sync with current ledger. Still intermittent in adversarial conditions — long-term fix should add a retry decorator on `submit_and_wait`.
- **Status:** Mitigated, not eliminated.

---

## Live testnet transactions (2026-09-28 21:35 UTC)

From `scripts/run_trust_scenarios.py` — full scenario run:

| # | Step | Type | Subject | Issuer | Result |
|---|---|---|---|---|---|
| 1 | Faucet | funding | `rhu...1pz` | — | 1000 XRP |
| 2 | Faucet | funding | `rw1...A1o` (issuer) | — | 1000 XRP |
| 3 | Faucet | funding | `rEm...QA2` (evaluator) | — | 1000 XRP |
| 4 | Issue KYC | CredentialCreate | `rhu...1pz` | `rw1...A1o` | ✓ AGENT_ID_V1 |
| 5 | Issue EVAL | CredentialCreate | `rhu...1pz` | `rEm...QA2` | ✓ EVAL_PASSED |
| 6 | Accept KYC | CredentialAccept | `rhu...1pz` | `rw1...A1o` | ✓ |
| 7 | Accept EVAL | CredentialAccept | `rhu...1pz` | `rEm...QA2` | ✓ |
| 8 | Set DID | DIDSet (URI-only) | `rhu...1pz` | — | ✓ |
| 9 | verify_set | ledger_entry ×3 | `rhu...1pz` | — | 2/3 (NONEXISTENT correctly missing) |
| 10 | TrustRegistry.check | ledger_entry ×2 | `rhu...1pz` | — | ✓ pass |
| 11 | TrustRegistry.fail | ledger_entry ×1 | `rhu...1pz` | — | ✓ fail |
| 12 | Revoke KYC | CredentialDelete | `rhu...1pz` | `rw1...A1o` | ✓ `EFB97EEB...AFEF` |
| 13 | verify_set post-revoke | ledger_entry ×2 | `rhu...1pz` | — | 1/2 (KYC gone, EVAL still held) |
| 14 | TrustRegistry post-revoke | ledger_entry ×2 | `rhu...1pz` | — | ✓ fail (KYC missing) |

**End state:** 14 transactions, all tesSUCCESS. Two credentials issued, both accepted, one revoked. Trust library correctly detects the revocation on the next verify.

---

## Library improvements identified during testing

| # | Suggestion | Source | Status |
|---|---|---|---|
| 1 | `revoke_credential()` should support `expiration`-based soft revocation as alternative to delete | Bug 2 docstring | documented, not implemented |
| 2 | `verify_set()` should accept credentials "with expiration > now" filter | nice-to-have | open |
| 3 | `TrustRegistry` should expose `add_issuer_trust(issuer_address, weight)` style weighting | future feature | open |
| 4 | `Authority.get_credentials_for_account()` should return structured list (currently best-effort) | improvement | open |
| 5 | `TrustRegistry._get_agent_credentials()` is a no-op stub — needs `xrpl.account_objects` integration | improvement | open |
| 6 | `submit_and_wait` should have a retry decorator for intermittent `tefPAST_SEQ` | Issue 6 | mitigated (sleep), not fixed |
| 7 | Dashboard should subscribe to subject's account on testnet and live-display new credentials | dashboard work | next |

---

## Open questions / things to verify

1. **Revocation semantics:** is `CredentialDelete` the right primary path, or should we lobby for a `CredentialRevoke` opcode that preserves the audit trail without removing the entry?
2. **TrustRegistry edge case:** what happens when a `require` rule has `issuer=None, credential_type=None`? Currently returns False (can't enumerate). Should this mean "any credential" or be an error?
3. **Multi-issuer trust:** if a credential can be issued by any of {A, B, C} for the same `credential_type`, how should the policy express that? Currently `require(issuer=[A, B, C], type=X)` works (OR semantics).
4. **Cache TTL:** `TrustRegistry._cache` is unbounded. Need LRU eviction or TTL.

---

## Next steps

- Build the agent-id dashboard (port 8768? distinct from 8766 wallet monitor) that subscribes to a watched set of agent addresses and live-displays new credentials as they land.
- Refine the trust library API based on dashboard feedback.
- Write EasyA submission narrative using these testing results.
