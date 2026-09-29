# Controller-Banned Multi-Sig Design

**Status:** v0.3.1 (live testnet verified)
**Authoring org:** S_DevLabs · S_DevLabs@outlook.com
**Date:** 2026-09-29 UTC

This document describes how xrpl_agent_id exercises the `CONTROLLER_BANNED`
authorization reason on-chain. It is the design rationale for the
`controller_banned_shape` field on `LiveAgentSpec` and for the
`_setup_controller_banned_onchain()` function in `scripts/stress_harness_live.py`.

---

## 1. Background: what `CONTROLLER_BANNED` means

`AuthorizationPolicy.evaluate()` produces a `CONTROLLER_BANNED` decision when
one of the agent's **controllers** (the addresses that can sign on its
behalf) appears on the org-level deny list. Controllers are derived from
two sources by `AgentRegistry`:

  1. The agent's classic address itself (always present, even for single-sig accounts).
  2. Every entry in the agent's on-chain `SignerList` (`account_objects` type=`signer_list`).

So to exercise `CONTROLLER_BANNED` on-chain, we must publish a `SignerList`
on the agent account whose entries include an address on the deny list.

---

## 2. The XRPL constraint that drove the design

XRPL forbids the master account from appearing in its own SignerList. The
SignerList, once published, **replaces** the master key as the source of
signing authority on the account. So:

  - You can NEVER have a SignerList with `[master, banned]` — the XRPL
    transaction validator raises:
    `{'signer_entries': 'The account submitting the transaction cannot appear in a signer entry.'}`
  - This was caught by the unit tests (see `test_stress_harness_live_signerlist.py`)
    on the first run with a naive `co_signer` shape design — exactly the
    kind of silent invariant the test suite is meant to surface.

That constraint leaves two real-world configurations where a banned
address is a controller:

### 2.1 Shape: `compromised` (the realistic one)

```
SignerList on agent account:
  - banned_addr  weight=1
  - (master implicitly removed)
quorum = 1
```

**Effect:** the banned address has full authority over the account.
Operationally the account is hijacked. This is exactly what an
"address-key compromise" looks like on the ledger.

`AgentRegistry` resolves `controllers = [banned_addr]`. `AuthorizationPolicy`
hits `CONTROLLER_BANNED` on the very first controller check.

### 2.2 Shape: `quarantined` (the frozen one)

```
SignerList on agent account:
  - banned_addr  weight=1
  - sentinel     weight=1
quorum = 2
```

**Effect:** neither the banned address nor the sentinel can transact
alone, because the master is gone and quorum is 2. The account is
operationally dead, but the **deny list still fires** because
`AgentRegistry` sees the banned co-signer as a controller.

Use case: an org publishes a `quarantined` SignerList the moment a
co-signer is suspected — freezes the account without losing the
deny-list audit trail.

---

## 3. Live verification

Both shapes were published and verified on the XRPL testnet on
2026-09-29 via `scripts/live_signerlist_smoke.py`:

| Shape        | Agent account                       | SignerListSet tx hash                                | On-chain SignerList verified |
|--------------|-------------------------------------|------------------------------------------------------|------------------------------|
| compromised  | `rhab3dBB7BiZ5H19RBFUDaCtPVLb6XqyHL` | `C30BEBB20D031B3E4D3E7B6684739F156EC3724E3E8EC4B70B75B20F06364946` | quorum=1, 1 entry = banned   |
| quarantined  | `rKx1jGKPd2h3rYxVpfT27xd3jwdGPf9RrG` | `C68C6BAB87E3F6AE093B90C28579C539846BCC414D75AF280C091B5359A3A49B` | quorum=2, 2 entries (banned + sentinel) |

The verifier:

1. Calls `account_objects` with `type=signer_list` against the agent account.
2. Reads `SignerQuorum` and `SignerEntries` from the first returned object.
3. Asserts the addresses match what the harness configured.

The full transaction bodies and decoded memos are visible at:

```
https://s.altnet.rippletest.net:51234/  (POST tx method with the hash)
```

---

## 4. How this fixes the v0.3.0 stress-test gap

The v0.3.0 stress test report (`docs/STRESS_TEST_v0.3.0.md`, §3) honestly
flagged that the `controller_banned` role came back ALLOW because the
harness did not publish a SignerList on ledger — only the local
in-memory `AgentRecord.controllers` was populated. With this change:

  - `setup_live_agents` now funds an extra banned co-signer wallet
    and publishes a `SignerListSet` with the chosen shape.
  - `AuthorizationPolicy.evaluate()` queries `AgentRegistry.resolve()`,
    which reads the SignerList from the ledger and surfaces the banned
    address as a controller.
  - The CONTROLLER_BANNED code path runs against real on-chain state,
    not a synthetic fixture.

End-to-end: the same code path that production callers will hit is what
the test exercises.

---

## 5. Operational notes

  - **Cost:** a SignerListSet costs the same base fee as any other
    transaction (~10 drops + reserve). The harness funds all wallets
    via the testnet faucet, so this is free on testnet and trivial on
    mainnet (~$0.005 USD at $0.50/XRP).

  - **Reversibility:** a new SignerListSet with the same quorum and a
    different entry set replaces the previous one. To restore the
    master as the only signer, publish a SignerList with **zero
    entries** and quorum 0 — XRPL treats this as "clear the list,
    revert to master."

  - **Sentinel funding for `quarantined`:** the sentinel wallet is
    funded by the harness and its seed is recorded on
    `LiveAgentSpec.controller_banned_sentinel_seed` for audit
    reproducibility. The sentinel seed is never used to sign anything
    in this harness — quorum 2 is unreachable without the master.

  - **Failure handling:** if the SignerListSet fails (e.g.
    `temDISABLED` because the agent account hasn't been funded long
    enough, or transient `tefPAST_SEQ`), the agent is gracefully
    degraded to `no_creds` and the original role is recorded on
    `LiveAgentSpec.degraded_from`. The summary table reflects this so
    a degraded run is never silently indistinguishable from a
    successful `CONTROLLER_BANNED` detection.

---

## 6. What's not in scope

  - Multi-sig for operational transactions (i.e., actually requiring
    N-of-M signatures on payments). The harness's SignerLists are
    published for **detection**, not for operational signing. A
    separate `xrpl_multi_sig` helper would be the right home for that.

  - Verifying that the banned address can no longer transact alone in
    the `quarantined` case (i.e., submitting a Payment signed by the
    sentinel alone and asserting it fails with `tefBAD_QUORUM`).
    Straightforward to add as a third live smoke test if desired.

  - The 50-agent scale run and the `/api/verify` endpoint from
    `docs/STRESS_TEST_v0.3.0.md` §9. Separate work items.
