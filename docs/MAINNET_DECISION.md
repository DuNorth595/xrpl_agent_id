# Mainnet Decision — v0.3.3 / v0.4.0 (last updated 2026-09-30)

**Status:** ⛔ **NO-GO for mainnet** in v0.3.x.
**Earliest realistic mainnet target:** v0.5.0, contingent on the blockers below.

This document records the go/no-go decision for xrpl_agent_id targeting XRPL **mainnet** (network ID `1`, `xrplcluster.com`). It is written before any mainnet transaction so the reasoning is on record — not after.

**Scope:** library code, on-chain transactions, and the local dashboard's `/api/verify` endpoint. **Out of scope:** the EasyA hackathon submission, which is a code-only submission.

---

## TL;DR

| Surface | Mainnet OK now? | Why |
|---|---|---|
| DID creation (`DIDSet`) | ✅ yes | Cheap (~12 drops), reversible (`DIDDelete`), no expiry. |
| Credential issuance (`CredentialCreate`) | ⚠️ conditional | No fee surprises, but **no library-level expiry enforcement** (see §3). |
| Credential acceptance (`CredentialAccept`) | ✅ yes | Free, optional opt-in. |
| Authorization decisions / audit | ⚠️ conditional | Library works; **Memos are not signed** — anyone can post a matching memo (see §4). |
| Dashboard `/api/verify` | ✅ read-only | Doesn't sign anything; queries ledger only. |

**Recommendation:** **testnet-only until v0.5.0.** The library's identity layer is safe; its trust-layer semantics need work before mainnet-issued credentials should be relied on.

---

## 1. Credential fees and reserve requirements

### 1.1 Per-transaction fees (XRPL mainnet)

| Operation | Fee | Notes |
|---|---|---|
| `DIDSet` | ~12 drops (~0.000012 XRP) | Refundable if `DIDDelete` follows. |
| `CredentialCreate` | ~12 drops | Non-refundable; goes to fee sink. |
| `CredentialAccept` | ~12 drops | Non-refundable. |
| `CredentialDelete` (revoke) | ~12 drops | Issued by either party. |
| Audit memo (`Payment` with `MemoData`) | ~12 drops | Per `Journal` entry. |

At ~$2.50/XRP and ~12 drops/tx, **1000 audit memos cost ~0.012 XRP ≈ $0.03**. Trivial in absolute terms. Volume is not the cost issue.

### 1.2 Reserve requirements — **this is the real cost**

XRPL mainnet requires an **owner reserve** of **2 XRP per owned ledger object**.

For xrpl_agent_id, each agent identity owns:
- 1 Account (200 XRP base reserve, not a per-object reserve but never recoverable if the account is used)
- 1 DID ledger object (**+2 XRP**)
- 0–N Credential ledger objects the agent accepts as subject (**+2 XRP each**)
- 0–M audit memo `Payment` transactions (not objects, but each burns ~12 drops)

**Concrete example — 1 issuer, 10 agents, 3 credentials each:**
- 10 × 200 XRP (base) = **2,000 XRP** locked
- 10 × 2 XRP (DID) = 20 XRP
- 30 × 2 XRP (credentials) = 60 XRP
- 30 × 12 drops (acceptance fee) = negligible
- **Total reserve lockup: ~2,080 XRP** (~$5,200 at $2.50/XRP)

For a stress test of 1,000 agents with 3 credentials each, that's **~208,000 XRP locked**. That is not a software cost — it is a treasury cost, and it must be planned.

### 1.3 Fee conclusion

The library can afford mainnet fees per transaction. The **reserve lockup** is real and must be budgeted by whoever funds the issuer account. Not a blocker — a planning requirement.

---

## 2. Key custody and seed handling

### 2.1 Current state of seed handling

In `xrpl_agent_id/identity.py`:

```python
def from_seed(cls, seed: str, network: str = "testnet") -> "AgentIdentity":
    """Create an AgentIdentity from a master seed (family seed, 's...')."""
    w = Wallet.from_seed(seed)
    ...
```

The library accepts a **plaintext family seed** in-process. The seed is held in the `Wallet` object for the lifetime of the process and never persisted by the library. Comments say "NEVER log this. NEVER print this." — enforced by convention only.

### 2.2 Mainnet implications

- A mainnet issuer's seed = **direct, irreversible control of real XRP**. One leak = game over.
- The library has **no KMS / hardware-wallet integration**. `Wallet.from_seed` is the only path.
- There is no rate limit, no allowlist of `CredentialCreate` targets, and no policy on what an issuer can sign.

### 2.3 Custody conclusion

For a **single-issuer, single-operator** v0.3.x deployment on mainnet, custody is a **process problem**, not a library problem: keep the seed in a password manager, never commit, never log, kill the process between runs. That is what the docs say.

For a **multi-issuer, multi-credential-type** deployment — the realistic hackathon-aftermath use case — the library **needs**:
- A `WalletProvider` abstraction (today there is only `from_seed`).
- Hardware-wallet support via `xrpl-py`'s `Wallet` interface or a custom backend.
- A signing-audit log (separate from the on-chain audit log) recording every signature event with timestamp + caller.

**These are v0.4.0 / v0.5.0 work.** Without them, running an issuer on mainnet is operationally fragile.

---

## 3. XLS-70 credential expiry — **library gap**

### 3.1 What XLS-70 supports

`CredentialCreate` accepts an optional `expiration` field (Ripple epoch seconds). The ledger stores it. After expiry, the credential still **exists** on the ledger but is semantically dead.

### 3.2 What the library does

Search the codebase:

```
$ grep -rn "expir" xrpl_agent_id/authorization.py
27:  CREDENTIAL_EXPIRED        A required credential's expiration is in the past.
74:    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
```

`CREDENTIAL_EXPIRED` is **declared but never emitted.** `AuthorizationPolicy.evaluate` does not check the `expiration` field on a credential it just resolved. An expired credential would currently **pass** the trust check as long as the ledger still has it.

**This is a real bug, not just a missing feature.** The fix is small (~20 lines in `authorization.py`), but it has not been done, and there is no test asserting "expired credential → deny".

### 3.3 Mainnet implication

Issuing a credential with an `expiration` on mainnet today provides **no security guarantee**. The trust policy says "credential X required" but the policy never checks "credential X is still valid." A relying party that trusts the library's `satisfied=True` result is being lied to.

**Blocker for mainnet.** Fix in v0.4.0, gated by a new test in `tests/test_authorization.py`.

---

## 4. Replay-attack resistance

### 4.1 Issuance replay

`CredentialCreate` is keyed by `(issuer, subject, credential_type)`. The ledger allows **only one** credential per triple — reissuing with different `URI` or `expiration` updates the existing entry.

**However**, the library treats the issuance event as recorded in the **on-chain audit memo** (`Payment` with `app = xrpl_agent_id_audit`). An attacker who knows the `decision_id` could submit a memo with a duplicate `decision_id` to confuse the audit log. The library's audit log dedup is **best-effort by `decision_id`** but `decision_id` is a UUID, so collision is not the issue — **the issue is that memos are unsigned**.

### 4.2 What memos actually prove

A `MemoData` field on a `Payment` transaction proves:
- **Someone** with the issuer's key signed a transaction containing this data.
- The data was committed to the ledger.

It does **not** prove:
- That the issuer's library code generated the memo (a compromised dependency could).
- That the `decision_id` corresponds to a real `AuthorizationDecision` (the library could be bypassed).

### 4.3 Mainnet implication

The on-chain audit log is a **corroboration** signal, not a proof. For a high-stakes reliance ("X is authorized to do Y"), the audit memo is a tiebreaker, not primary evidence. **Don't sell it as more than that.**

For mainnet, recommend: rely on the **ledger credential entry itself** as the source of truth, not the audit memo. Memos are for transparency and forensics.

---

## 5. Recommendation — staged rollout

### v0.3.3 (now) — testnet only
- ✅ Ship `xrpl_agent_id 0.3.3` to GitHub as testnet-only.
- ✅ README states "**this library targets testnet. Mainnet is not supported in v0.3.x.**"
- ✅ Dashboard defaults to `network=testnet`; mainnet URL must be explicit.
- ✅ All `pytest` runs use testnet fixtures.

### v0.4.0 (shipped 2026-09-30) — expiry fix closes §3 blocker
- ✅ **Implemented expiry enforcement** in `AuthorizationPolicy.evaluate` (`xrpl_agent_id/authorization.py`).
  Emits `CREDENTIAL_EXPIRED` reason when a required credential's `expiration`
  (XLS-70, Ripple epoch seconds) is in the past. Was a silent gap before.
- ✅ Added `tests/test_authorization.py::TestCredentialExpiry` — 6 new hermetic tests.
- ✅ Bumped to `xrpl_agent_id 0.4.0`. Public GitHub tip `3ba4b13`. PyPI publish deferred
  (build verified clean via `twine check`; not uploaded).
- ⏭ `WalletProvider` interface (deferred to v0.5.0).
- ⏭ Document reserve lockup costs in README (deferred — §1.2 numbers still hold).

**Status update (2026-09-30):** §3 blocker resolved. Remaining path to mainnet is §2
(KMS / hardware wallet) and §4 (signed audit memos). Both target v0.5.0.

### v0.5.0 — mainnet go
- ⏭ Hardware-wallet / KMS-backed `WalletProvider`.
- ⏭ Audit-log integrity proofs (signed decision_id, not just memo).
- ⏭ Stress run on **mainnet** with a capped treasury (e.g., 100 XRP) to validate reserve math in practice.
- ⏭ SECURITY.md published with seed-handling threat model.
- ⏭ PyPI publish (`twine upload`) — requires PyPI account + 2FA token, gated by you.

---

## 6. Open questions for the operator (not the library)

These are decisions for whoever funds the issuer account, not code:

1. **Who holds the issuer seed?** Single human? Multi-sig? Hardware wallet?
2. **What is the budget for reserve lockup?** At ~$5K for 10 agents with 3 credentials, plan accordingly.
3. **What is the revocation policy?** XLS-70 has no native revoke; `CredentialDelete` is a transaction the subject must submit.
4. **What is the disclosure policy for audit memos?** They are public on the ledger. Decide what goes in them.

---

## 7. Audit trail

| Date | Commit | Note |
|---|---|---|
| 2026-09-30 | (this doc) | Initial decision. Testnet-only for v0.3.3. |
| 2026-09-30 | `29ee5b3` | v0.4.0 shipped — §3 (expiry) blocker resolved. Re-decide on §2/§4 path to mainnet. |
| 2026-09-30 | `3ba4b13` | Public GitHub repo tip after v0.4.0 push. PyPI publish deferred. |

---

*This document is a recorded decision; it tracks v0.3.3 (initial), v0.4.0 (expiry blocker resolved), and the v0.5.0 path to mainnet.*
