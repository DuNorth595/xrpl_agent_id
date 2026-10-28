# Mainnet Stress Run — Plan (2026-10-28)

**Status:** AWAITING FUNDING. The treasury address exists and is verified;
it just needs XRP.

## 1. What this run proves

The stress run validates the **production cost and lifecycle behavior** of
`xrpl_agent_id` against real mainnet state. Specifically it exercises:

1. **Real reserve math.** The library creates ledger objects, and the
   ledger enforces an owner reserve. This run confirms the corrected
   figures in `docs/MAINNET_DECISION.md` §1 — 1 XRP base per account,
   0.2 XRP per owned object — hold for real, and that the library's
   balance checks account for them.
2. **Real fee behavior.** Base fee 10 drops, ledger median ~5,000 drops
   under load. The library must not under-fund a transaction, and
   `submit_with_retry` must recover from `telINSUF_FEE_P`.
3. **Full lifecycle on a real ledger.** `DIDSet` → `CredentialCreate` →
   `CredentialAccept` → `CredentialDelete` → `DIDDelete`, plus the
   audit mirror's 1-drop `Payment` memo, with the account returning to
   (near) zero reserve at the end.
4. **The authorization policy against real credential state**, including
   the expiry and revocation paths that ship in v0.4.x.

**What this run does NOT prove:** anything about `DepositPreauth`. The
library does not implement it (§4.5), so a mainnet success here says
nothing about protocol-level enforcement of issuer trust.

## 2. The treasury

| | |
|---|---|
| Address | `r9wvLo8R7cpWEfZpibSMwxZrTiLFr4d5eK` |
| Type | Ed25519 classic address |
| Seed | macOS keychain, service `hermes-xrpl-mainnet-stress` |
| Metadata | Local secrets dir, `xrpl_mainnet_stress_wallet.json` (public fields only) |
| Status | Valid, **unfunded** — verified via `account_info` against live mainnet |

The address is public the moment it is funded, so there is no secret
exposure in writing it here. The seed never leaves the keychain, and the
keychain service name and local paths are deliberately not recorded in
this public repository.

## 3. Funding request

**Requested: 20 XRP (~$27 at $1.33/XRP).**

That covers the **n=6 run — one agent per role** — with headroom, and
leaves the treasury usable for a larger run if the first pass surfaces
something.
Nothing is spent by the funding itself — it sits as account reserve
until the run begins.

### 3.1 Budget breakdown (live reserves, verified against mainnet)

| Item | XRP |
|---|---|
| Issuer account (1 × 1 XRP base) | 1.00 |
| Mirror + sink accounts (2 × 1 XRP base) | 2.00 |
| 6 agent accounts (6 × 1 XRP base) | 6.00 |
| 6 DIDs (6 × 0.2 XRP owner) | 1.20 |
| 6 credentials (6 × 0.2 XRP owner) | 1.20 |
| **Subtotal — reserve lockup** | **11.40** |
| Transaction fees (~50 txs × ~0.005 XRP median) | ~0.25 |
| **Total required** | **~11.7** |
| **Requested** | **20** |

The extra ~11 XRP is headroom for retry storms, a larger agent count,
and the fact that reserve is only recoverable by deleting the objects.

### 3.2 Scaling

| Agents | XRP required | XRP with headroom |
|---|---|---|
| 6 (one per role) | ~11.7 | 20 |
| 20 | ~36 | 50 |
| 100 | ~142 | 200 |

## 4. How to fund

Any of these work; the address is a standard mainnet address:

- **XRP Escrow / Payment** from any wallet or exchange
- **xrpl.org / XPM** or the native Wallet
- An exchange withdrawal (smallest fee on XRPL itself: 12 drops)
- `Payment` from the CLI: `python -m xrpl_agent_id` — or a direct
  `submit_and_wait` with a 1-XRP `Payment` to the treasury

**Verification after funding:**

```bash
curl -s -X POST https://s1.ripple.com:51234 -H "Content-Type: application/json" \
  -d '{"method":"account_info","params":[{"account":"r9wvLo8R7cpWEfZpibSMwxZrTiLFr4d5eK"}]}'
```

The `actNotFound` error is nested inside `result`, not returned as a
top-level error — so check `result.account_data` rather than the
top-level `error` key:

```bash
curl -s -X POST https://s1.ripple.com:51234 -H "Content-Type: application/json" \
  -d '{"method":"account_info","params":[{"account":"r9wvLo8R7cpWEfZpibSMwxZrTiLFr4d5eK"}]}' \
| python3 -c "import json,sys; a=json.load(sys.stdin)['result'].get('account_data'); print('balance:', int(a['balance'])/1e6, 'XRP') if a else print('not funded yet')"
```

**Note:** `s1.ripple.com` is a Clio server, which only serves *validated*
data. That is what you want here — a funded-but-not-validated account
should read as unfunded until the transaction closes.

## 5. Execution plan

### Step 1 — Preflight (no funds spent)

1. Confirm the treasury balance ≥ 20 XRP.
2. Confirm the library's mainnet client reaches the ledger and the
   current fee/reserve settings match §1.
3. Confirm no seed material is inside the repository or any log output.
4. Confirm the run writes its results to `results/` and not anywhere
   public.

### Step 2 — The run

The existing `scripts/stress_harness_live.py` is **hardwired to testnet**
— it calls the testnet faucet to fund each agent wallet, and the faucet
does not exist on mainnet. So the mainnet run needs a variant where:

- wallets are funded **from the treasury** (a `Payment` from the
  treasury to each freshly-created agent account), not from a faucet;
- `network` is `mainnet` throughout (`Authority.from_seed(...,
  network="mainnet")`, `AgentIdentity.from_seed(...,
  network="mainnet")`, `get_client("mainnet")`);
- the mirror gets a **real sink** — the harness already documents that
  XRPL rejects self-payments for XRP, so the destination must be a
  separate funded address;
- the harness keeps its mixed states.

**Target: n=6 agents, one per role.** The harness defines six roles —
`valid`, `banned`, `controller_banned`, `no_creds`, `wrong_issuer`,
`pending` — and assigns them round-robin (`roles[i % len(roles)]`).
At **n=4 only four of the six roles are exercised**, so `wrong_issuer`
and one other never run. **n=6 is the minimum that covers every role**,
and costs ~1.4 XRP more than n=4.

**Budget at n=6:**

| Item | XRP |
|---|---|
| Issuer account | 1.00 |
| Mirror + sink accounts | 2.00 |
| 6 agent accounts | 6.00 |
| 6 DIDs | 1.20 |
| 6 credentials | 1.20 |
| **Subtotal — reserve lockup** | **11.40** |
| Transaction fees (~50 txs × ~0.005 XRP median) | ~0.25 |
| **Total required** | **~11.7** |

Still comfortably inside the 20 XRP request. Note that the
`controller_banned` role needs a **multi-sig** account, which means an
extra `SignerListSet` and an extra signer — slightly more setup cost
than a plain account, and the sink/mirror split still applies.

### Step 3 — Verify the output

For every transaction in the run, independently confirm against the
public ledger (not against the harness's own report):

- the transaction is `validated`;
- the `Credential` object exists with the expected `Issuer`, `Subject`,
  and `CredentialType`;
- the `lsfAccepted` flag is set on the accepted credentials and **not**
  on the deliberately-unaccepted one;
- the audit memo's `decision_id` matches the decision the library
  recorded, and its transaction signer is the mirror address;
- the reserve lockup observed matches the predicted numbers above.

### Step 4 — Cleanup and accounting

1. Delete the credentials and DIDs (`CredentialDelete`, `DIDDelete`).
2. Delete the agent accounts to release the owner reserve.
3. Report the final treasury balance and the total XRP actually spent
   in fees.
4. **Discard the keychain entry** — the treasury has served its
   purpose, and a funded mainnet seed that no longer has a job is pure
   risk. The address stays as a public record of what happened.

## 6. Risks and limits

| Risk | Mitigation |
|---|---|
| Run consumes more than planned | Reserve math verified above; 20 XRP against an ~8.8 XRP requirement is >2× headroom |
| A bug writes a malformed transaction | The library's own validation and `autofill` are the gate; every tx is independently verified in Step 3 |
| Mainnet funds are irrecoverable | This is a **throwaway** address, never a treasury you depend on. Never fund an address you can't afford to lose |
| The harness assumes a faucet | Step 2 replaces the faucet with treasury-funded wallets |
| Public ledger exposes the test | **Intended** — that is the point. The addresses are public, the DIDs are meaningless, and the credentials are revoked in cleanup |

## 7. Decision record

This run is the last substantive item in the v0.5.0 mainnet gate. The
other two are closed: §3 expiry shipped in v0.4.0, and §4 (audit-memo
provenance) was closed by decision on 2026-10-27 in commit `c598974`.

**Funding this address is the approval to transact on mainnet.** Nothing
happens until XRP arrives, and the amount sent determines the scope —
20 XRP buys the n=4 run described here.
