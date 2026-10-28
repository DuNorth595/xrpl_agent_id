# Mainnet Decision — v0.3.3 / v0.4.0 / v0.5.0 (last updated 2026-10-28)

**Status:** ✅ **GO for mainnet** as of v0.5.0.
**What changed:** the mainnet stress run described in §5 was executed on
**2026-10-28** against a disposable treasury. It is no longer pending.

This document was originally written *before* any mainnet transaction, so the
original reasoning is on the record rather than rewritten to look prescient.
The §7 audit trail records what was decided when, and the §5 status updates
record what actually happened.

**One caveat to read before relying on this library:** it does not implement Deposit
Authorization, so its trust check is a **library-side evaluation, not a
protocol-level guarantee**. See §4.5.

This document records the go/no-go decision for xrpl_agent_id targeting XRPL **mainnet** (network ID `1`, `xrplcluster.com`). It is written before any mainnet transaction so the reasoning is on record — not after.

**Scope:** library code, on-chain transactions, and the local dashboard's `/api/verify` endpoint. **Out of scope:** the EasyA hackathon submission, which is a code-only submission.

---

## TL;DR

| Surface | Mainnet OK now? | Why |
|---|---|---|
| DID creation (`DIDSet`) | ✅ yes | Cheap (10 drops base), reversible (`DIDDelete`), no expiry. |
| Credential issuance (`CredentialCreate`) | ✅ yes | No fee surprises, and **expiry enforcement landed in v0.4.0** (§3). |
| Credential acceptance (`CredentialAccept`) | ✅ yes | Free, optional opt-in. |
| Authorization decisions / audit | ✅ forensic only | The **ledger credential entry** is the confirmed artifact. Memos are provenance-verified but **not issuer-attributable** (§4.3). |
| Dashboard `/api/verify` | ✅ read-only | Doesn't sign anything; queries ledger only. Returns `verified: true` **only** for a memo fetched from a transaction, and names its signer. |

**Recommendation (2026-10-28):** **mainnet is supported.** The identity layer is
sound, expiry is enforced, the audit-memo question is closed by decision, and
the §5 stress run has now executed successfully against real mainnet ledger
state. The remaining gap is **on-chain enforcement of issuer trust** (§4.5) and
the operator decisions in §6 — neither is a library-correctness problem.

**What the stress run proved.** The credential lifecycle — `DIDSet` →
`CredentialCreate` → `CredentialAccept` → `SignerListSet` — works against
real mainnet state, and the reserve arithmetic in §1.2 holds in practice. The
run also surfaced three defects in the *test harness* (not the library), all
since fixed and documented in §5.1.

---

## 1. Credential fees and reserve requirements

### 1.1 Per-transaction fees (XRPL mainnet)

| Operation | Fee | Notes |
|---|---|---|
| `DIDSet` | 10 drops (0.00001 XRP) | Refundable if `DIDDelete` follows. |
| `CredentialCreate` | 10 drops | Non-refundable; goes to fee sink. |
| `CredentialAccept` | 10 drops | Non-refundable. |
| `CredentialDelete` (revoke) | 10 drops | Issued by either party. |
| Audit memo (`Payment` with `MemoData`) | 10 drops | Per `Journal` entry. |

The **base cost** of a transaction is 10 drops. Actual fees float above it
with ledger load — the live median at the time of writing was ~5,000
drops, and the open-ledger fee was ~5,121 drops — so budget against the
**median**, not the base. The library does not pin a fee; it lets
`autofill` compute the current value, and it retries on
`telINSUF_FEE_P`.

At $1.33/XRP and ~5,000 drops/tx, **1,000 audit memos cost ~0.05 XRP ≈
$0.07**. Trivial in absolute terms. Volume is not the cost issue.

### 1.2 Reserve requirements

XRPL mainnet requires a **base reserve of 1 XRP per account** and an
**owner reserve increment of 0.2 XRP per owned ledger object**.

> **Corrected 2026-10-27.** This section previously stated 200 XRP base
> and 2 XRP per object. Those were the **pre-2024** values. Reserves were
> lowered 10× by validator vote on **2024-12-02** (1 XRP base, 0.2 XRP
> per object) — see
> [Lower Reserves Are In Effect](https://xrpl.org/blog/2024/lower-reserves-are-in-effect).
> The figures below are verified against the live mainnet ledger
> (`server_info` → `validated_ledger`: `reserve_base_xrp: 1.0`,
> `reserve_inc_xrp: 0.2`, `base_fee_xrp: 1e-05`). Re-verified **2026-10-28**
> at ledger #107,958,359 — the values are unchanged from the 2026-10-27
> check.

For xrpl_agent_id, each agent identity owns:
- 1 Account (**1 XRP** base reserve, recoverable when the account is deleted)
- 1 DID ledger object (**+0.2 XRP**, recoverable on `DIDDelete`)
- 0–N Credential ledger objects the agent accepts as subject (**+0.2 XRP
  each**, recoverable on `CredentialDelete`)
- 0–M audit memo `Payment` transactions (not owned objects, but each burns a
  transaction fee)

**These are balance requirements, not expenses.** Per XRPL's documentation:
*"When objects are removed from the ledger, they no longer count against the
reserve requirement."* No reserve is ever burned by deletion — deleting the
objects releases the XRP in full. See §1.4.

**Concrete example — 1 issuer, 10 agents, 3 credentials each:**
- 10 × 1 XRP (base) = **10 XRP** locked
- 10 × 0.2 XRP (DID) = 2 XRP
- 30 × 0.2 XRP (credentials) = 6 XRP
- Transaction fees (creation + acceptance) ≈ **~0.2 XRP actual spend**
  (~$0.27) — see §1.4
- **Total reserve lockup: ~18 XRP** (~$24 at $1.33/XRP) — **recoverable in
  full** by deleting the objects and accounts

**Concrete example — the same shape at scale:** 1,000 agents with 3
credentials each locks **~1,400 XRP** (~$1,860), not the ~208,000 XRP
previously stated. The lockup is also avoidable at the topology level: one
account can hold many credentials, so 100 credentials on a single account
locks 21 XRP instead of 120.

**Real spend per agent identity** (1 account + 1 DID + 1 credential, 4
transactions): **~0.02 XRP (~$0.027)**. The 1.4 XRP is a *balance
requirement*, not a cost.

### 1.3 Fee conclusion

The library can afford mainnet fees per transaction. The **reserve lockup**
is real and must be budgeted by whoever funds the issuer account, but it is
recoverable in full and — at current reserves — an order of magnitude cheaper
than this document previously claimed. Not a blocker — a planning
requirement.

### 1.4 Spend vs. lockup — the distinction this section previously blurred

> **Corrected 2026-10-28.** This section called the reserve "the real
> cost." That conflated two different things. The full model, with the
> scale economics and three verified code findings, is in
> [`XRPL_COST_MODEL.md`](../XRPL_COST_MODEL.md). Summary:

| | What it is | Recoverable? | Scales with |
|---|---|---|---|
| **Fee** | XRP burned per transaction | ❌ No | transaction volume |
| **Base reserve** | 1 XRP per *account* | ✅ Yes, on account deletion | number of accounts |
| **Owner reserve** | 0.2 XRP per *owned object* | ✅ Yes, on object deletion | number of objects |

**Per agent identity, the real spend is ~0.02 XRP (~$0.027) — the
transaction fees.** The 1.4 XRP is a *balance requirement*, not a
expense: *"When objects are removed from the ledger, they no longer
count against the reserve requirement."* Deleting the credential
releases it.

**Who pays the reserve matters:** the DID's 0.2 XRP goes to whoever
created it; a credential's 0.2 XRP goes to the **subject** once it
accepts (the issuer pays it before acceptance). An issuer issuing to
existing accounts pays the account base reserve and the DID, not the
credential.

**At scale the fee dominates, not the reserve.** Anchoring one
transaction per decision costs ~$6,650/day at 1M decisions/day (median
fee); anchoring a batched Merkle root costs ~$6.65/day — **1,000×
cheaper**. The library's on-chain mirror is optional and defaults to
`mirror_on_deny_only=True`, but per-decision anchoring does not scale.
Batch anchoring is the fix, and **it is implemented**: `AuditLog.anchor_batch()`
(commit `d48264b`) anchors up to 1,000 decisions in one transaction via a
Merkle root, with `AuditLog.batch_proof()` and `xrpl_agent_id/merkle.py`
providing inclusion proofs that need only leaf + path + root — not the
operator's database.

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

> **Update (2026-10-27):** the `WalletProvider` interface referenced here as
> deferred has now shipped in v0.5.0 (`3fdcf4f`, `2be313c`). A
> `SeedWalletProvider` wraps an in-process wallet; an
> `ExternalSignerProvider` delegates signing to a KMS, HSM, or hardware
> wallet so the key never enters the process. A concrete vendor adapter
> (AWS/GCP/Azure) is v0.6.0 — note AWS KMS does not support Ed25519, so
> secp256k1 is the viable path.

For a **single-issuer, single-operator** v0.3.x deployment on mainnet, custody is a **process problem**, not a library problem: keep the seed in a password manager, never commit, never log, kill the process between runs. That is what the docs say.

For a **multi-issuer, multi-credential-type** deployment — the realistic hackathon-aftermath use case — the library **needs**:
- A `WalletProvider` abstraction (today there is only `from_seed`).
- Hardware-wallet support via `xrpl-py`'s `Wallet` interface or a custom backend.
- A signing-audit log (separate from the on-chain audit log) recording every signature event with timestamp + caller.

**These are v0.4.0 / v0.5.0 work.** Without them, running an issuer on mainnet is operationally fragile.

---

## 3. XLS-70 credential expiry — **library gap (RESOLVED in v0.4.0)**

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

> **RESOLVED in v0.4.0 (2026-09-30).** The text below records the gap as
> it was found. `AuthorizationPolicy.evaluate` now emits a
> `CREDENTIAL_EXPIRED` reason when a required credential's `expiration`
> is in the past, gated by `tests/test_authorization.py::TestCredentialExpiry`.

Issuing a credential with an `expiration` on mainnet today provides **no security guarantee**. The trust policy says "credential X required" but the policy never checks "credential X is still valid." A relying party that trusts the library's `satisfied=True` result is being lied to.

**Blocker for mainnet.** Fix in v0.4.0, gated by a new test in `tests/test_authorization.py`.

---

## 4. Audit-memo provenance — **decision, 2026-10-27**

**Status: CLOSED by decision.** No code change required. This section
records what the library promises, so a relying party knows exactly what
it can and cannot rely on.

### 4.1 The decision

> **The ledger `Credential` entry is the confirmed, auditable artifact.
> The audit memo is a forensic record — provenance-verified, but not
> attributable to the issuer, and not evidence of issuer intent.**

### 4.2 Why the ledger credential entry is the source of truth

The `Credential` ledger object records `Issuer`, `Subject`,
`CredentialType`, `Expiration`, `URI`, and the `lsfAccepted` flag
(XLS-70d §2.1). Its issuer attribution is **cryptographic, not a
claim**: `CredentialCreate` is signed by the issuer's account, and the
ledger verifies that signature. The object ID is a hash over `Subject` +
`Issuer` + `CredentialType`, so the issuer cannot be swapped without
changing the object's identity.

A third party can therefore verify, using the public ledger and without
this library:

1. **Who** issued the credential — the `Issuer` field, backed by the
   transaction signature.
2. **What** it attests — the `CredentialType`.
3. **Whether the subject accepted it** — the `lsfAccepted` flag.
4. **Whether it has expired** — the `Expiration` field.
5. **Who is permitted to rely on that issuer** — the relying party's own
   allowlist (see §4.5).

### 4.3 What the audit memo is, and what it is not

The audit memo is a `MemoData` field on a 1-drop `Payment` transaction
(`app = xrpl_agent_id_audit`). It records a **decision event** — that a
request was evaluated, its outcome, and when — which the credential
entry does not record at all.

It **is** verifiable as to provenance:

- The transaction is on the ledger, immutable and timestamped.
- The signing account is recorded on-chain (`Account`, `SigningPubKey`,
  `TxnSignature`), so **you can validate who signed it**. The verify
  endpoint returns that address in `on_chain_proof.signer`.
- `on_chain_proof` is only populated when the memo was *fetched from a
  transaction*. A pasted blob does not establish provenance, and the
  endpoint returns `verified: false` for that path, with the reason
  stated.

It **is not**:

- **Attributable to the issuer.** The memo is signed by the **audit
  mirror key**, which is deliberately a separate key from the issuer's,
  so the issuer's key stays out of a high-volume logging path. A memo
  proves *the audit service recorded this*, not *the issuer decided
  this*.
- **Proof that a real evaluation happened.** `decision_id` is an
  **unsalted SHA-256** over the decision's public fields. Anyone who can
  read a decision can recompute its id and submit their own memo
  carrying the same id. The library's own `/api/verify` handles this:
  it flags a memo that contradicts the stored record, and refuses to
  call an unanchored memo verified.
- **Issuer intent.** A memo says a decision was logged. It does not say
  the issuer's policy engine produced it, and it does not say what the
  request was (the request context is not in the memo payload).

**Do not use the memo as evidence that the issuer authorized something.**
Use the credential entry.

### 4.4 What this closes

The §4 replay concern — a third party submitting a duplicate `decision_id`
to confuse the audit log — is **not** a blocker under this decision,
because the audit log is not the authoritative record. A forged memo can
add noise to a forensic log; it cannot create, alter, or revoke a
credential, and it cannot satisfy a relying party who checks the ledger.

This was the framing that needed closing, not a signature scheme.

### 4.5 The gap this decision does NOT close — on-chain enforcement

**This library does not implement Deposit Authorization.** The XLS-70
standard extends `DepositPreauth` so an account can allowlist *accounts
holding specific credentials*, not just specific accounts. The library
does not create `DepositPreauth` objects (there is no reference to
`DepositPreauth` or `DepositAuth` anywhere in it).

Consequences, stated plainly:

- `TrustRegistry.check()` is a **library-side evaluation**. It reads the
  ledger and decides in Python. A counterparty who does not use this
  library is not bound by its result.
- The **protocol-level** enforcement of issuer trust is the relying
  party's own `DepositPreauth` allowlist, and a counterparty that wants
  that guarantee must create one.

**A relying party who assumes this library's check is enforced at the
protocol level is worse off than one who has no library at all** — the
first believes they are protected and is not. This is the single most
important thing to communicate to a counterparty.

### 4.6 Key-management caveat

The memo's provenance is only as good as the **audit mirror key's**
hygiene. It is a key that signs a high-volume stream; if it is
compromised, the forensic log can be forged wholesale. Since v0.5.0 the
`WalletProvider` interface exists to put that key in a KMS or hardware
wallet rather than in process. **That is a key-management decision for
the operator, not a library change.**

### 4.7 Follow-ups this decision defers (not blockers)

- A real KMS/HSM adapter (`AwsKmsWalletProvider` et al.) — v0.6.0.
  Note AWS KMS does not support Ed25519; secp256k1 is the viable path.
- Optional issuer-signed decision proofs, if a counterparty needs
  decision-level attribution rather than grant-level attestation. Not
  required under this decision.

---

## 5. Recommendation — staged rollout

### v0.3.3 (shipped) — testnet only
- ✅ Ship `xrpl_agent_id 0.3.3` to GitHub as testnet-only.
- ✅ README states "**this library targets testnet. Mainnet is not supported in v0.3.x.**"
- ✅ Dashboard defaults to `network=testnet`; mainnet URL must be explicit.
- ✅ All `pytest` runs use testnet fixtures.

### v0.4.0 (shipped 2026-09-30) — expiry fix closes §3 blocker
- ✅ **Implemented expiry enforcement** in `AuthorizationPolicy.evaluate` (`xrpl_agent_id/authorization.py`).
  Emits `CREDENTIAL_EXPIRED` reason when a required credential's `expiration`
  (XLS-70, Ripple epoch seconds) is in the past. Was a silent gap before.
- ✅ Added `tests/test_authorization.py::TestCredentialExpiry` — 6 new hermetic tests.
- ✅ Bumped to `xrpl_agent_id 0.4.0`. Public GitHub tip `3ba4b13`. GitHub-only release
  (no PyPI publish planned; install from source via `pip install -e ".[dev,mcp]"`).
- ✅ `WalletProvider` interface — shipped in v0.5.0 (`2be313c`). A live KMS/HSM
  adapter remains v0.6.0.
- ⏭ Document reserve lockup costs in README (deferred — §1.2 numbers still hold).

**Status update (2026-09-30):** §3 blocker resolved. Remaining path to mainnet is §2
(KMS / hardware wallet) and §4 (signed audit memos). Both target v0.5.0.

### v0.5.0 — mainnet go (shipped, 2026-10-28)
- ✅ **`WalletProvider` interface** — shipped. `classic_address` /
  `public_key` / `sign(tx)`, structural so any object with those members
  satisfies it. `SeedWalletProvider` (in-process, current behaviour) and
  `ExternalSignerProvider` (delegates to a signing callable) both ship.
  **A live KMS/HSM adapter is v0.6.0** — AWS KMS does not support
  Ed25519, so secp256k1 is the viable path.
- ✅ **Audit-log integrity** — closed **by decision** (§4), not by
  signing. The ledger credential entry is the confirmed artifact; the
  memo is forensic. The verify endpoint now names the memo's signer and
  only reports `verified: true` for a memo fetched from a transaction.
  Optional issuer-signed decision proofs are a v0.6.0+ feature, not a
  blocker.
- ✅ **SECURITY.md** with a seed-handling threat model — published.
- ✅ **Stress run on mainnet** — **executed 2026-10-28** against a
  disposable, operator-funded treasury. See §5.1 for the results.
- ⚠️ **Deposit Authorization is not implemented** (§4.5). The library's
  trust check is a library-side evaluation. A relying party who wants
  protocol-level enforcement must create their own `DepositPreauth`
  allowlist.
- ~~PyPI publish~~ — **not planned.** Library installs from source; no PyPI account available.

### 5.1 Mainnet stress run — executed 2026-10-28

Run against a disposable treasury funded by the operator for this purpose
only. Network ID `1`, XRPL mainnet.

**Ledger evidence.** The treasury's public transaction history shows
**56 validated transactions — 39 `Payment` and 17 `AccountDelete`** —
covering every run and the cleanup of each one. Every funded account was
subsequently deleted, and the treasury's `OwnerCount` ended at **0**.

**The full n=6 run (2026-10-28, complete).** A later run exercised **all
six authorization roles** against real mainnet state, with every outcome
verified independently against the public ledger rather than read from the
harness's own report:

| Role | Expected | Result |
|---|---|---|
| `valid` | allow | ✅ `OK` |
| `banned` | deny | ✅ `AGENT_BANNED` |
| `controller_banned` | deny | ✅ `CONTROLLER_BANNED` |
| `no_creds` | deny | ✅ `NO_CREDENTIALS` |
| `wrong_issuer` | deny | ✅ `CREDENTIAL_MISSING` |
| `pending` | deny | ✅ `CREDENTIAL_REVOKED` |

Six of six correct. The treasury's transaction history after the run shows
**27 `Payment` and 13 `AccountDelete`** across 19 funded accounts. Spend for
the run was **12.51 XRP** — the reserve lockup, released on deletion, plus
transaction fees.

**What it validated.**

| Claim | Result |
|---|---|
| `DIDSet` / `CredentialCreate` / `CredentialAccept` / `SignerListSet` against real mainnet state | ✅ works |
| Reserve arithmetic in §1.2 (1 XRP base, 0.2 XRP owner) | ✅ holds in practice |
| `CredentialDelete` / `DIDDelete` release the owner reserve | ✅ confirmed |
| `AccountDelete` releases the base reserve | ✅ confirmed — 9 accounts deleted, `OwnerCount` → 0 |
| Budget gate refuses to start an under-funded run | ✅ verified by deliberately triggering it |

**Defects found — all three in the test harness, none in the library.**

The harness had three separate budget and safety defects before the run
was correct. Each was found by the harness's own gates rather than by
inspection, and each is documented so a future run does not repeat it:

1. **A dry run spent 4 XRP.** The mock patched the harness's binding of the
   submit function but not `xrpl.transaction.submit_and_wait`, which the
   library calls directly in wallet mode. The dry run reported success while
   two real 2 XRP Payments had gone out. Two account seeds had not been
   persisted, so those 4 XRP were **stranded** — not recoverable, but not
   taken by anyone either. The harness now patches the source module and
   installs a raising backstop, and the dry run asserts the balance is
   unchanged before reporting success.
2. **The budget omitted the treasury's own base reserve.** A treasury
   funding N accounts needs N+1 XRP of base reserve. The run failed part-way
   through its funding sequence with `tecUNFUNDED_PAYMENT`.
3. **The budget had no fee headroom.** The computed need was 0.2% short of
   what the ledger actually required, because one transaction paid 13,155
   drops where the budget assumed the 5,000-drop median. The fee line now
   carries a 1.5× multiplier; the reserve figures are exact protocol
   constants and are not inflated.

A later n=6 run surfaced two more of the same class — an account funded for
fewer owned objects than it would actually hold:

4. **The wrong issuer was funded as a plain wallet.** It creates a
   credential, and the owner reserve for a credential is charged to the
   account that *creates* it, not the one that accepts it. The run failed at
   `tecINSUFFICIENT_RESERVE`. It is now funded as an issuer.
5. **The `controller_banned` agent was funded for two owned objects.** It
   publishes a `SignerListSet` on its own account, so the agent — not the
   banned co-signer — holds that object and needs a third owner reserve.
   `agent_funding_drops()` now takes the object count as an argument.

`tests/test_harness_funding.py` pins all five: every account is funded for
the objects it will actually hold, and the computed budget counts them. Each
check was verified by mutation — reintroducing the wrong-issuer bug fails the
suite.

The budget function was then validated against the ledger's own record of
what the run required — computed 9.127 XRP against 9.104 XRP actually
needed — before any further spend.

**What it did not validate.** The run exercised the credential lifecycle
and the reserve arithmetic. It did **not** exercise the optional on-chain
audit mirror, and it did not validate the `WalletProvider` path against a
live KMS. Both remain open.

**What this costs the operator.** The treasury is disposable and was funded
for this run. It is not part of the library's operating cost — see §1.3.

---

## 6. Open questions for the operator (not the library)

These are decisions for whoever funds the issuer account, not code:

1. **Who holds the issuer seed?** Single human? Multi-sig? Hardware wallet?
2. **What is the budget for reserve lockup?** One account with one credential
   costs 1.2 XRP of lockup (1 XRP base + 0.2 XRP owner). Ten agents that way
   is ~12 XRP. **One account holding many credentials is far cheaper** — the
   0.2 XRP owner reserve is per *object*, not per identity, and the base
   reserve is per *account*. See §1.2.
3. **What is the revocation policy?** XLS-70 has no native revoke; `CredentialDelete` is a transaction the subject must submit.
4. **What is the disclosure policy for audit memos?** They are public on the ledger. Decide what goes in them.

---

## 7. Audit trail

| Date | Commit | Note |
|---|---|---|
| 2026-09-30 | (this doc) | Initial decision. Testnet-only for v0.3.3. |
| 2026-09-30 | `29ee5b3` | v0.4.0 shipped — §3 (expiry) blocker resolved. Re-decide on §2/§4 path to mainnet. |
| 2026-09-30 | `3ba4b13` | Public GitHub repo tip after v0.4.0 push. PyPI publish: not planned. |
| 2026-10-27 | `3fdcf4f` | **v0.5.0 Step A** — Python floor raised to 3.10, `xrpl-py` pinned to `>=5.0,<6`, CI matrix 3.10–3.13. Closes the §2 dependency concern. |
| 2026-10-27 | `2be313c` | **v0.5.0 Step B** — `WalletProvider` shipped. `AgentIdentity.wallet` → `.provider` (deprecated, removed in v0.6.0). `submit_with_retry` re-autofills and re-signs per attempt in provider mode, fixing a stale-`LastLedgerSequence` retry that would have failed with `tefMAX_LEDGER`. |
| 2026-10-27 | (this doc) | **§4 closed by decision.** Audit memos are forensic only; the ledger credential entry is the confirmed artifact. Two corrections to the previous §4: it said memos were signed by "the issuer's key" — they are signed by the **audit mirror key**, a deliberately separate key; and it described `decision_id` as a UUID — it is an **unsalted SHA-256**, which is what makes it recomputable. Also added §4.5: Deposit Authorization is not implemented. |
| 2026-10-27 | (this doc) | Verify endpoint now returns `on_chain_proof.signer` — the account that signed the memo — so the response is self-contained. |
| 2026-10-27 | `232cbae` | Public GitHub repo tip after the v0.5.0 push and the history scrub. PyPI publish: not planned. |
| 2026-09-30 | `444cfb9` | README "Releases" + docs/dev/SYNC.md landed. PyPI plan retired from public docs. |
| 2026-10-28 | `b722766`, `d48264b`, `0abe429` | v0.5.0 fee controls shipped: a fee ceiling with price-aware USD bounds, batch anchoring (1 Merkle root per 1,000 decisions), and `issue_credential_cheapest()` — one transaction instead of two. |
| 2026-10-28 | `d1b0a53` | Reserve and fee figures corrected — the previous numbers were off by roughly two orders of magnitude. |
| 2026-10-28 | (this doc) | **Mainnet stress run executed.** Status changed from ⛔ NO-GO to ✅ GO. §5.1 added with the ledger evidence and the three harness defects the run surfaced. The library itself was not defective; every fix was to the harness. |
| 2026-10-28 | `f2c3b67` | Documentation aligned to XRPL repository conventions: README Usage and Installation sections, `docs/README.md` as a reader-path index, `py.typed` added (the package was fully annotated but shipped no PEP 561 marker, so type checkers ignored it), and stale test/module counts corrected. |

---

*This document is a recorded decision; it tracks v0.3.3 (initial), v0.4.0 (expiry blocker resolved), and v0.5.0 (mainnet go — the stress run executed 2026-10-28).*
