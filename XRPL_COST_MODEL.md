# XRPL Cost Model for AI Agent Traffic — Fees, Lockup, Release

**Status:** analysis, 2026-10-28. Written to define the cost vocabulary the
rest of the project was using loosely, and to answer the question that
actually matters at scale: **what does a decision cost, and what does it
cost when there are millions of them?**

All mainnet figures below were read from the live ledger
(`server_info`/`fee` on `s1.ripple.com`, ledger #107,949,986) on 2026-10-28.
XRP price is $1.33 (CoinGecko, same date).

---

## 1. The vocabulary — four distinct things people were calling "cost"

This is the core confusion. XRPL has **four** separate economic concepts,
and the project docs were using "cost" for all of them.

| Term | What it is | Recoverable? | Scales with |
|---|---|---|---|
| **Fee** | XRP burned to include a transaction | ❌ **No** — destroyed to the fee sink | transaction volume |
| **Base reserve** | Minimum balance per *account* | ✅ Yes — on account deletion | number of accounts |
| **Owner reserve** | Balance required per *owned ledger object* | ✅ Yes — on object deletion | number of objects |
| **Load cost** | The *minimum fee* the network will accept, rising with congestion | n/a | current ledger load |

**The reserve is not a cost.** Per XRPL's documentation: *"You cannot send
the reserved XRP to others, but you can recover some of the XRP by
deleting the account,"* and *"When objects are removed from the ledger,
they no longer count against the reserve requirement."*

**So:** a reserve is a **balance requirement** (a lockup), and a fee is a
**burn**. Only the fee is a true expense. This document uses *spend* for
fees and *lockup* for reserve, consistently.

### 1.1 Where the old "~1.4 XRP per agent identity" figure came from

| Component | XRP | Who pays | Lockup or spend |
|---|---|---|---|
| Account base reserve | 1.0 | the account's owner | lockup |
| DID owner reserve | 0.2 | *"the account that creates the DID object"* | lockup |
| Credential owner reserve | 0.2 | **the subject** if it accepted; else the issuer | lockup |
| Transaction fees (~4 txs) | ~0.02 | the signer | **spend** |

**Real spend per identity: ~0.02 XRP (~$0.027).**
**Balance locked per identity: 1.4 XRP (~$1.86), fully recoverable.**

The previous framing in `docs/MAINNET_DECISION.md` §1 called the 1.4 XRP
"the real cost." That was wrong, and this document supersedes it.

### 1.2 Release mechanics — what gives the reserve back

| Action | What is released | Who gets it |
|---|---|---|
| `CredentialDelete` | that credential's 0.2 XRP | the object's owner |
| `DIDDelete` | that DID's 0.2 XRP | the object's owner |
| `CredentialAccept` | **moves** 0.2 XRP from issuer to subject | subject now holds the lockup |
| Account deletion | the 1 XRP base reserve | the account owner |

**No reserve is ever burned by deletion.** Reserve is a *claim on your
balance*, not a *charge against it*.

**Important subtlety:** a `Credential` counts against the **subject's**
reserve once accepted, and against the **issuer's** before acceptance. An
issuer who issues to existing accounts pays only the account base reserve
and the DID — **not** the credential's 0.2 XRP.

---

## 1.3 What actually costs money — the four layers

This is the question a counterparty asks first: *"why would there be a
fee for agent ID?"* The honest answer is that **almost nothing does**,
and the cost has to be attributed to the right layer.

### Layer 1 — The hot path: authorize a request

| | Cost |
|---|---|
| `AuthorizationPolicy.evaluate()` | pure Python + SQLite read — **$0.00** |
| `TrustRegistry.check()` | XRPL **read** (free) + local cache — **$0.00** |

**Authorizing a million requests costs nothing.** No transaction, no
fee, no XRPL write. XRPL reads are free — you pay for *consensus* (a
ledger write), not for bandwidth. A counterparty can run agent ID
entirely off-chain for the authorization path and only touch the ledger
when establishing or revoking trust.

### Layer 2 — The cold path: establish an identity

| Transaction | Required? |
|---|---|
| `CredentialCreate` (issuer) | **yes** — this is the attestation |
| `CredentialAccept` (subject) | yes — the subject's consent |
| `DIDSet` | **no** — XLS-70 makes it *recommended*, not required |
| `CredentialDelete` (revoke) | yes — but it replaces a revoke you'd need anyway |

**Two transactions, once.** At the base fee: **0.00002 XRP
(~$0.0000266)**. At the median fee: **0.01 XRP (~$0.0133)**. Plus
**1.2 XRP of recoverable reserve** — parked, not spent.

### Layer 3 — The optional layer: audit anchoring

| Path | Cost | Paid by |
|---|---|---|
| Every decision → SQLite | **$0.00** (this is the default) | — |
| `mirror_on_deny_only` → 1 tx per **denial** | 1 tx — attacker-reachable | **the operator** |
| `anchor_batch()` → 1 tx per 1,000 decisions | 1 tx / 1,000 | the operator |

**Who pays matters.** The audit mirror has its **own key** — it is
deliberately separate from the issuer's — so the on-chain audit cost is
the *operator's*, not the counterparty's. A counterparty whose agent is
denied pays **$0.00**; it never signs a transaction because of a denial.

That is the right allocation, and it is why the denial-path griefing
vector (§5.2) is an *operator* problem to solve rather than a tax on
the ecosystem.

### Layer 4 — The agent's actual work

The agent doing its job — paying, swapping, whatever — pays XRPL fees
**anyway**. That is the cost of using XRPL, not the cost of agent ID.
It should not appear in the agent-ID line item.

### Summary

| Stage | Cost |
|---|---|
| Establishing trust | two transactions, ~0.00002 XRP at base fee |
| Using that trust to authorize requests | **$0.00** — a ledger read, not a write |
| On-chain audit trail | optional; batched, one transaction per 1,000 decisions |

**The fee is a one-time setup cost.** The authorization path is read-only,
so authorizing requests submits no transaction.

## 2. The fee model

### 2.1 Live mainnet fee levels (2026-10-28)

| Level | Drops | XRP | USD |
|---|---|---|---|
| Minimum / base fee | 10 | 0.00001 | $0.000013 |
| **Median fee** | 5,000 | 0.005 | $0.00665 |
| Open-ledger fee (at soft limit) | 5,000 | 0.005 | $0.00665 |
| Current open-ledger fee (unloaded) | 10 | 0.00001 | $0.000013 |

**The 10-drop base fee is a floor, not a price.** The real transaction cost
is set by the *open ledger cost*, which "increases exponentially for each
transaction added beyond the soft limit" of the open ledger.

### 2.2 The escalation mechanism — why the median is 5,000 drops

XRPL has two thresholds:

1. **Local load cost** — below it, a server *ignores* the transaction
   entirely (doesn't even relay it).
2. **Open ledger cost** — below it, the transaction is *queued* for a later
   ledger rather than included now.

The open ledger cost equals the minimum unscaled cost until the open
ledger reaches its soft limit, then **increases exponentially** per
additional transaction. This is the mechanism that prices congestion.

`xrpl-py` exposes `fee_mult_max` / `fee_div_max` on `sign()` so a caller
can refuse to sign a transaction above a fee ceiling. **Any production
agent should set these.** An unfilled transaction that lands at a 50×
premium is a real cost, and it is avoidable.

### 2.3 Throughput ceiling

| Metric | Value |
|---|---|
| Average ledger close | ~3.9 s |
| Sustained throughput | ~1,500 TPS |
| Theoretical max (7,500 tx/ledger ÷ 3.9 s) | ~1,900 TPS |
| **Capacity** | **~130–166 million tx/day** |

**"Millions of AI transactions a day" is feasible.** 1M tx/day is ~11.6
TPS average — under 1% of capacity. But it is *not* free of the
throughput constraint: sustained AI traffic is a permanent load, and the
open-ledger cost will sit well above the 10-drop base.

---

## 3. Three tiers of anchoring — the design that makes scale work

**The library already supports this.** `AuditLog` is SQLite-backed and
always writes every decision. `XRPLMirror` is **optional**
(`xrpl_mirror: XRPLMirror | None = None`) and the default policy is
`mirror_on_deny_only=True`.

So the architecture already separates the cheap case from the important
case. What was missing was the *third* tier.

### Tier 0 — Off-chain (default, and correct for allows)

Every decision → SQLite, append-only, WAL. **$0.**
Not independently verifiable; the operator can alter it. Appropriate for
the common case, and it is what the library does by default.

### Tier 1 — On-chain anchor (denials, high-value, disputes)

One transaction per anchored decision.

| Fee level | XRP/tx | USD/tx | 1M anchored/day |
|---|---|---|---|
| 10 drops (base) | 0.00001 | $0.000013 | $0.01 |
| 5,000 drops (median) | 0.005 | $0.00665 | $6,650 |
| 50,000 drops (congested) | 0.05 | $0.06650 | $66,500 |

**Anchoring every decision at the median is $6,650/day.** That is the
number that makes "millions a day" a business decision rather than an
obvious one.

### Tier 2 — Batched anchor (the scale answer)

**Implemented** (commit `d48264b`). `AuditLog.anchor_batch()` anchors a
**Merkle root of N decisions** in one transaction. The individual decisions
remain off-chain but provably included in the anchored root.
`AuditLog.batch_proof()` returns the inclusion proof for any one decision,
and it needs only leaf + path + root — not the operator's database.

| Batch size | @ 10 drops | @ 5,000 drops |
|---|---|---|
| 100 | $0.00000013 / decision | $0.00006650 |
| 1,000 | $0.00000001 | **$0.00000665** |
| 10,000 | ~$0 | $0.00000067 |

**1M decisions/day, anchored daily in 1,000-transaction batches:**

| Fee level | Tx/day | XRP/day | USD/day | USD/year |
|---|---|---|---|---|
| Base (10 drops) | 1,000 | 0.01 | **$0.01** | **~$4** |
| Median (5,000 drops) | 1,000 | 5.00 | $6.65 | $2,427 |

**At the base fee, anchoring a million decisions a day costs about a
dollar.** At the median it's $6.65/day. Both are viable. Per-transaction
anchoring at the median is $6,650/day — **1,000× worse** than batching.

**The gap between Tier 1 and Tier 2 is the single most important economic
fact in this document.** It is why per-transaction anchoring does not
scale — and why `anchor_batch()` is the path to use instead.

---

### Tier 3 — the ONE case that earns an on-chain transaction

A **revocation**. `CredentialDelete` is a state change that must be
on-chain, and it costs nothing extra because it replaces the revoke you
would have needed anyway.

### 3.1 The cheapest identity — skip the optional DID object

The full flow is **three** transactions: `DIDSet`,
`CredentialCreate`, `CredentialAccept`. It can be **two**.

XLS-70 defines the `Credential` object's `Subject` and `Issuer` as
`AccountID`s — not as DID documents. A DID object is *recommended*
("It is recommended that anyone who holds a credential also have a DID
object, to increase compliance with the W3C VC spec"), **not
required**. And the DID is *derivable* from the address with no ledger
access at all — `did_from_account()` is pure string manipulation.

So a credential to a bare address is fully valid.
`Authority.issue_credential_cheapest()` is the two-transaction path.

| Path | Transactions | @ base fee | @ median fee | DID object? |
|---|---|---|---|---|
| Full (DID + create + accept) | 3 | $0.000040 | $0.0199 | yes |
| **Cheapest (create + accept)** | **2** | **$0.000027** | **$0.0133** | **no** |

**33% fewer setup fees**, and the lockup drops from 1.4 XRP to 1.2 XRP
because there is no DID object to own. The DID object can be published
later, in one transaction, if W3C VC-document compliance is needed.

### 3.2 One account, many credentials

The 1 XRP base reserve is per-**account**, and one account can hold
many credentials. An "agent" that is really a fleet of sub-agents can
share one account:

| Topology | Transactions | Reserve locked |
|---|---|---|
| 100 identities, 1 account each | 200 | 120 XRP |
| **100 credentials, 1 account** | 200 | **21 XRP** |

**Same fees, 5.7× less locked.** This is the single biggest lockup
lever, and it is invisible in the library's API because `AgentIdentity`
maps 1:1 to a wallet.

## 3.9 Releasing the lockup — the account lifecycle

Reserve is released in three steps, in this order. Each has a cost, and
the order matters.

| Step | Transaction | Releases | Costs |
|---|---|---|---|
| 1 | `CredentialDelete` | the credential's 0.2 XRP owner reserve | ordinary fee (~10 drops) |
| 2 | `DIDDelete` | the DID's 0.2 XRP owner reserve | ordinary fee (~10 drops) |
| 3 | `AccountDelete` | the account's **1 XRP base reserve** | **0.2 XRP** (one owner reserve) |

**Step 3 is not optional if you want the base reserve back.** An account's
*transferable* balance is `Balance - BaseReserve` — the base reserve is
locked for the life of the account. A plain `Payment` can only move what
sits above it. On a 1.02 XRP account, a Payment recovers 0.02 XRP;
`AccountDelete` recovers 0.82 XRP.

**The `AccountDelete` cost is 0.2 XRP, not 0.00001.** It is a special
transaction cost equal to one owner reserve, deliberately 20,000× the
ordinary base fee, to discourage account create/delete spam. It is burned.

**Two requirements, both easy to trip over:**

1. **Age.** `Sequence + 255 <= current_ledger_index`. An account must be at
   least ~256 ledgers old — about 15 minutes. This is an anti-spam
   measure. A run that creates accounts and immediately tries to reclaim
   them will fail.
2. **No deletion blockers.** The account must own no objects. Delete the
   credentials and DIDs first, and remove any `SignerList` with a
   `SignerListSet` carrying a weight-0 entry for the master key.

**The account is gone for good.** `AccountDelete` is irreversible, and the
address cannot be reused.

**Why this matters for a deployment.** A fleet of N agent accounts costs
`N x 1 XRP` locked, and tearing it down costs `N x 0.2 XRP` in deletion
fees. For 100 accounts that is 100 XRP locked and 20 XRP burned to
release it. If the fleet is short-lived, that is 20% of the lockup gone.
For long-lived identities, the lockup is recovered once and the accounts
persist.

**Sizing a treasury.** A treasury that funds N accounts and recovers them
needs `N x (1 XRP + objects) + N x 0.2 XRP` in deletion fees on top of
the lockup it wants back. The treasury's own 1 XRP base reserve is locked
too, and it is not counted in the per-account figures above — a treasury
funding 6 accounts needs 7 XRP of base reserve, not 6.

## 4. Lockup at scale

**Lockup does not scale with transaction volume. Fees do.** They are
different problems.

| Identities | XRP locked | USD @ $1.33 | USD @ $10 | USD @ $25 |
|---|---|---|---|---|
| 1,000 | 1,400 | $1,862 | $14,000 | $35,000 |
| 100,000 | 140,000 | $186,200 | $1,400,000 | $3,500,000 |
| 1,000,000 | 1,400,000 | $1,862,000 | $14,000,000 | $35,000,000 |

**The 1 XRP base reserve dominates** — it is per-account, and a naive
implementation provisions one account per agent.

**Mitigations, in order of impact:**

1. **Don't provision an account per agent.** One account can hold many
   DIDs and credentials. A single account with 1 DID and 50 credentials
   locks 11.2 XRP total, not 70. This is the biggest lever by far.
2. **Delete objects when done.** Revoking a credential releases 0.2 XRP.
3. **Wait for the `Sponsor` amendment.** `Credential` supports
   sponsorship; `DID` does not. Status on mainnet 2026-10-28:
   `enabled=False, supported=True` — **not yet live.** When it activates,
   an issuer can pay the subject's credential reserve, pushing the
   0.2 XRP off the subject and onto the issuer.
4. **Price sensitivity is real.** At $25/XRP, 1.4 XRP locked per identity
   is $35 — painful at 1M identities. But the *fee* side barely moves:
   1M decisions/day at the median is $6.65/day, not $1.86M.

---

## 4.1 Price sensitivity — what happens at $20, $50, $500, $1,000

Both the fee and the reserve are denominated in **XRP**, so both scale
linearly with price. The arithmetic is trivial; the *system* behaviour
is not. Four things change beyond the dollar sign.

### 4.1.1 The daily bill at 1M decisions/day

| Strategy | $1.33 | $20 | $50 | $500 | $1,000 |
|---|---|---|---|---|---|
| per-decision @ base | $13 | $200 | $500 | $5,000 | $10,000 |
| per-decision @ median | $6,650 | $100,000 | $250,000 | $2,500,000 | $5,000,000 |
| **batch 1,000 @ base** | **$0.01** | **$0.20** | **$0.50** | **$5** | **$10** |
| **batch 1,000 @ median** | **$6.65** | **$100** | **$250** | **$2,500** | **$5,000** |
| batch 10,000 @ median | $0.67 | $10 | $25 | $250 | $500 |

### 4.1.2 Affordable volume on a $1,000/month audit budget

| XRP price | per-decision @ base | per-decision @ median | batch 1,000 @ base |
|---|---|---|---|
| $1.33 | 2,506,266/day | 5,013/day | 2,506/day |
| $20 | 166,667/day | 333/day | 167/day |
| $50 | 66,667/day | 133/day | 67/day |
| $500 | 6,667/day | 13/day | 7/day |
| $1,000 | 3,333/day | 7/day | 3/day |

**At $1,000/XRP, per-decision anchoring at the median fee buys 7
decisions a day.** Batching at the base fee buys 3,333 — 475× more.
**The two optimizations are not independent: batching is a
prerequisite for the base fee to be reachable.** At high prices the base
fee becomes the only viable anchoring mode, and batching is what makes
it viable.

### 4.1.3 What actually changes, beyond the dollar sign

1. **A drop ceiling is not a dollar ceiling.** `max_fee_drops` caps the
   XRP paid. The same 5,000-drop ceiling is $6.65 at $1.33/XRP and
   $5,000 at $1,000/XRP. If the operator's constraint is a dollar
   amount, use `max_fee_drops_for_usd(usd_limit, xrp_price)` to
   recompute it against the current price.

2. **The reserve becomes a treasury decision long before the fee
   becomes a problem.** 1 XRP per account is $20 at $20/XRP and $1,000
   at $1,000/XRP. *Account-per-agent* becomes unaffordable well before
   the fee does. The shared-account topology (§3.2) is the fix, and it
   is worth more at high prices than at low ones.

3. **The lockup is XRP-denominated, so it carries no price exposure.**
   It is a storage requirement, not a position: 1.2 XRP locked is 1.2
   XRP locked whether XRP is at $0.50 or $1,000. It is released on
   deletion either way. The *dollar value* of the lockup tracks price,
   but the *XRP obligation* does not.

4. **A fee denominated in a volatile asset is a long-term cost
   ratchet.** The fee is a fixed number of drops, so if the asset
   trends up, the dollar cost of every transaction trends up with it.
   This is the structural reason to measure cost in **transactions per
   dollar**, not dollars per transaction.

### 4.1.4 The one structural recommendation

**Denominate in XRP, not USD.** If the service is priced in XRP (or the
customer holds XRP), a price rise is neutral: the cost is flat in XRP
and the revenue moves with it. A USD-priced service paying XRP-denominated
fees is a **naked short on XRP** — it gets worse exactly when the
ecosystem it is built on is succeeding.

## 5. The conclusion that matters

**For millions of AI transactions per day, the fee is the whole game, and
the reserve is a second-order problem that a naive implementation makes
first-order.**

| Scale | Dominant cost | Fix |
|---|---|---|
| 1,000 tx/day | fees (~$7/day) | nothing needed |
| 1M tx/day, per-tx anchoring | **fees (~$6,650/day)** | **batch anchor** |
| 1M tx/day, batched | fees (~$0.01–6.65/day) | nothing needed |
| 1M identities | **lockup (~$1.86M)** | shared accounts, deletion, sponsorship |

### 5.1 Recommendations

**Status as of 2026-10-28:** items 1, 2, and 4 are **done**; items 3 and 5
remain open.

1. ~~**Implement Tier 2 batch anchoring.**~~ ✅ **Done** (commit `d48264b`).
   It is the difference between a $6,650/day and a $6.65/day cost at 1M
   decisions/day. `AuditLog.anchor_batch()` anchors a Merkle root over up to
   1,000 decisions in one transaction; `AuditLog.batch_proof()` returns the
   inclusion proof. See `xrpl_agent_id/merkle.py`.
2. ~~**Set `fee_mult_max` / `fee_div_max`** on signing so an agent never
   signs into a congestion premium it did not intend to pay.~~ ✅ **Done**
   (commit `b722766`). `max_fee_drops` is enforced against the *autofilled*
   fee, pre-signing, and `max_fee_drops_for_usd()` converts a dollar ceiling
   into a drop ceiling. The refusal is *recorded* on the audit row
   (`mirror_dropped_reason`), never silently dropped.
3. **Document the account-per-agent trap** prominently. It is the single
   biggest lockup mistake available, and it is invisible in the library's
   API because `AgentIdentity` maps 1:1 to a wallet. ⬜ **Still open.**
4. ~~**Revise `docs/MAINNET_DECISION.md` §1** to use *spend* and *lockup*
   consistently and to state the release mechanics.~~ ✅ **Done** (2026-10-28).
   §1.2–§1.3 now lead with real spend and state the release mechanics; §1.4
   carries the full distinction.
5. **Do not let a per-transaction anchor be the default.** It already
   isn't — `mirror_on_deny_only=True` — but the *denial* path is exactly
   the path an attacker would spam, so it needs a rate limit, not just a
   fee. ⬜ **Partially open:** the fee ceiling bounds the damage, but there is
   still no per-period *mirror budget* on the denial path.

### 5.2 Findings — three gaps, verified in the code

These were checked against the implementation, not inferred. All three are
real.

#### Finding 1 — the denial path is an unconditional griefing vector

`AuditLog.record()` mirrors on every denial:

```python
should_mirror = (not self.mirror_on_deny_only) or (not decision.allow)
if should_mirror:
    mirror_tx = self.xrpl_mirror.submit(decision, extra)
```

There is **no rate limit, no ceiling, and no aggregation** on that path.
A denial is decided from a request the caller supplies, so an attacker
who can cause denials can cause **transactions**. At the median fee that
is $0.00665 per denial, paid by the operator.

**The failure mode is bad in both directions:** unbounded spend under
attack, and a silently-dropped audit trail if a ceiling is added without
recording the gap.

**Recommendation:** a per-period mirror budget, with denials past the
budget recorded as `mirrored_tx=NULL` **and** an explicit
`mirror_dropped` reason — a *recorded* gap, never a silent one. Batch
anchor the overflow instead of dropping it.

#### Finding 2 — no fee ceiling anywhere in the library

`xrpl-py` supports `fee_mult_max` / `fee_div_max` on `sign()`, and
`submit_with_retry` re-autofills per attempt. But the library never sets
a ceiling, and the mirror's `Payment` does not pin a `Fee`.

Consequence: a mirror transaction submitted during congestion can be
signed at whatever the current open-ledger cost is — the exponential
escalation path from §2.2 — with no ceiling. A single anchored decision
could cost 50× the median.

**Recommendation:** set a fee ceiling on every signing path, and record
the ceiling's value so an auditor can tell what a decision *would* have
cost.

#### Finding 3 — the mirror's destination is a burn

The mirror sends a 1-drop `Payment` to a sink address. The 1 drop is
burned (it cannot be 0 — *"0-XRP fails"*). At 1M anchored decisions/day
that is 1 XRP/day burned into the sink, on top of fees.

Small, but it is a **second, silent, per-transaction cost** that the fee
model does not capture, and it scales with exactly the volume that
matters. Batching fixes this too: 1,000 transactions/day instead of
1,000,000.

### 5.3 Open questions this document raises

- **Does a batched Merkle anchor satisfy an auditor?** An auditor
  verifying "this decision was anchored" must be shown the leaf, the
  path, and the root. That is a proof-verification interface, not a
  lookup. Design work, and it is the thing a counterparty will ask about.
- **What is the right denial policy?** Rate limit, budget, or batch?
  Depends on the operator's risk posture — but it cannot be "mirror
  everything, unconditionally," which is what the code does today.
- **Does the off-chain SQLite log need tamper-evidence?** It is
  operator-mutable. A Merkle root anchored on-chain makes it
  *tamper-evident* — which is the whole point of Tier 2.
