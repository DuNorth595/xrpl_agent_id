# xrpl_agent_id — Use Cases

> What this library actually enables, in concrete terms, for people who haven't read the README yet.

`xrpl_agent_id` is a Python library that gives an AI agent an XRPL-based identity, lets other agents issue it credentials, and produces auditable ALLOW/DENY decisions about its actions — with the proof of every decision optionally mirrored to the XRP Ledger.

This document describes five concrete scenarios where this matters. None of them are speculative; each one maps to code that ships in the `xrpl_agent_id` package today.

---

## 1. AI Agent Marketplace — Verifying a "Certified" Agent

**Who:** A platform connecting users to AI agents (coding assistants, research bots, customer-service agents).

**The problem:** Users want to know whether an agent has passed a third-party eval, has a clean abuse history, or has any operator-level credentials — before they let it touch their data.

**Without `xrpl_agent_id`:** The platform runs its own database. Each agent's history lives in a silo. Switching platforms means losing history. Users have to trust the platform's word.

**With `xrpl_agent_id`:**
- An eval registry (e.g., SWE-bench, GPQA, an internal eval suite) issues an XLS-70 Credential to the agent's XRPL account: *"This agent passed eval X on date Y."*
- The agent's DID (`did:xrpl:1:rABC…`) is resolvable from the public ledger.
- The platform calls `TrustRegistry.check(agent_did)` and gets back a stable verdict in one call.
- The verdict has a hash on-chain (`xrpl_agent_id.audit.XRPLMirror`) that anyone can verify independently.

**Code shape:**
```python
from xrpl_agent_id import TrustRegistry, TrustPolicy

policy = TrustPolicy()
policy.require("eval_passed_v1")
policy.deny_on_ban_list(my_org_bans)

verdict = TrustRegistry(client).check(agent_did, policy)
if verdict.satisfied:
    # allow the agent to proceed
    ...
```

The same code works whether the platform is a marketplace, a hiring portal, or an internal agent gateway — because the underlying primitives (DIDs, Credentials, audit log) are public ledger objects.

---

## 2. Multi-Agent System — One Agent Delegates to Another

**Who:** A team building a multi-agent workflow (planner → researcher → writer → reviewer), where each agent is owned by a different service or organization.

**The problem:** The planner needs to decide whether to trust the researcher's output, the researcher's output needs to be attributable, and the whole chain needs to be auditable when something goes wrong.

**Without `xrpl_agent_id`:** Each service signs its own way. Audit trails are JSON logs in vendor-controlled storage. Cross-org forensics is impossible.

**With `xrpl_agent_id`:**
- Each agent has its own DID + wallet.
- The org that deployed the planner can issue a credential to the researcher attesting *"this researcher is part of project X."*
- Every delegation decision — "planner delegated to researcher" — produces an `AuthorizationDecision` that gets a SHA-256 `decision_id` and (optionally) a 1-drop memo on the ledger.
- Months later, when something breaks, the chain is reconstructible from the public ledger alone.

**Why this is non-trivial without us:** XLS-70 gives you the credential primitive. The decision layer — `AuthorizationPolicy`, `BannedAgentRegistry`, audit mirror with reverse-lookup via `/api/verify` — is the part you'd otherwise write yourself.

---

## 3. Compliance Audit Trail — "Show me what your agent did"

**Who:** A regulated industry (finance, health, legal) where an AI agent takes actions that need to be explainable to auditors.

**The problem:** The agent made 10,000 decisions last quarter. Auditors want a tamper-evident trail.

**Without `xrpl_agent_id`:** Logs in a vendor's database. Vendor could rewrite them. Or logs in your DB, but no third party can verify.

**With `xrpl_agent_id`:**
- Every decision is recorded in `xrpl_agent_id.audit.AuditLog` (SQLite by default).
- The same decision's `decision_id` is mirrored to the XRPL via a 1-drop memo. Cost: ~15 drops of XRP, which on testnet is free and on mainnet is fractions of a cent.
- An auditor can be given a tx hash and reverse-lookup the decision via `GET /api/verify?tx_hash=…` (or directly via `xrpl_agent_id.audit.XRPLMirror.verify()`).
- The auditor doesn't need to trust you, your database, or your uptime. They need to trust the XRPL consensus.

**Code shape:**
```python
from xrpl_agent_id.audit import AuditLog, XRPLMirror

mirror = XRPLMirror(network="mainnet")
audit = AuditLog(db_path="decisions.db", mirror=mirror)

decision = policy.evaluate(request)
audit.record(decision)  # writes to SQLite + submits memo to XRPL
```

The XRPL memo is the receipt. The SQLite row is the detail. Both are queryable. They match by `decision_id`.

---

## 4. Agent Reputation That Travels

**Who:** An agent that operates across multiple platforms (a coding agent that works for GitHub, GitLab, and an internal corp environment).

**The problem:** Today, "agent reputation" is fragmented per platform. An agent banned on platform A starts fresh on platform B. There's no portable record.

**Without `xrpl_agent_id`:** Each platform maintains its own blacklist. They don't talk.

**With `xrpl_agent_id`:**
- An agent's DID is portable across every XRPL-using platform.
- `xrpl_agent_id.banned.BannedAgentRegistry` keeps org-level deny lists, but the *evidence* for a ban can itself be on-chain (e.g., a CredentialDelete referencing the original CredentialCreate).
- Platforms that agree to share bans can cross-reference. Platforms that don't still see the credential revocation on-chain and can decide their own policy.
- "Agent reputation" becomes a property of the agent's DID, not the platform's database.

This is the long-term use case — it requires ecosystem coordination, not just library adoption. But the primitives are in place today.

---

## 5. On-Chain Agent Identity for Trust-Minimized Authorization

**Who:** An agent that holds funds (XRP, tokens, NFTs) on behalf of a user, and the user wants fine-grained control over what the agent can do.

**The problem:** The agent has signing keys. The user trusts the agent not to drain the wallet. But "trust" here is enforced by nothing — the agent could sign any transaction it wants.

**Without `xrpl_agent_id`:** Multi-sig with a hardware wallet. Slow UX. The agent can't react in real time.

**With `xrpl_agent_id`:**
- The user's wallet has a `SignerList` that includes the agent's key *and* a sentinel key.
- The sentinel key holds the master weight; the agent's key weight is below quorum for high-value transactions.
- For low-value transactions (below a threshold the user set), the agent can act alone.
- For everything else, the sentinel must co-sign.
- This isn't `xrpl_agent_id` writing the multi-sig — that's `rippled`'s job. But `xrpl_agent_id.authorization.AuthorizationPolicy` lets the user's code express *"this is a low-value action"* and *"this isn't"* in stable, queryable terms that map onto the on-chain constraints.

**Code shape:**
```python
from xrpl_agent_id.authorization import AuthorizationPolicy, ReasonCode

policy = AuthorizationPolicy()
result = policy.evaluate(
    agent_did=agent,
    request=request,
    transaction_amount_xrp=12.5,
)

if result.allow:
    agent.sign_and_submit(...)
else:
    request_human_co_sign(...)
```

The policy layer is the *interface* between "what the agent wants to do" and "what the ledger will allow unsupervised." This is what makes the rest of the library a *system*, not a collection of primitives.

---

## What this library is NOT

- **Not a dashboard.** `xrpl_agent_id/dashboard/` exists for build-time monitoring. It's a tool we use, not a deliverable.
- **Not a node.** `xrpl_agent_id` talks to any XRPL node (public or your own). It doesn't ship one.
- **Not a wallet.** Keys stay with you. The library signs transactions through `xrpl-py`; we never see or store your seeds.
- **Not a custody solution.** If you need regulatory-grade custody, use a custodial wallet. This library assumes you already control the keys.

---

## How to read this if you're evaluating the library

| If you are… | Start with |
|---|---|
| A developer trying it out | [`QUICKSTART.md`](QUICKSTART.md) |
| Evaluating it for production use | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Reviewing the on-chain claims | [`STRESS_TEST_v0.3.0.md`](STRESS_TEST_v0.3.0.md) |
| Comparing against another library | This document (above) — read the use cases, then check the code |
| Filing a bug or asking a question | Open an issue on GitHub |

---

**Document status:** v0.3.3 — first public-facing articulation of use cases.
