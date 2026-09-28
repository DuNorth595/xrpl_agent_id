# Agent ID Issuance Flow — XRPL Testnet Walkthrough

**Date:** 2026-09-28 (UTC)
**Network:** XRPL Testnet (`s.altnet.rippletest.net:51234`)
**xrpl_agent_id version:** 0.0.0 (post-live-wire-up)
**Driver:** `scripts/issue_agent_id.py`
**Raw artifacts:** `results/issuance_testnet_20260928_173724.json`

---

## TL;DR — The thesis

Agent ID on XRPL is **public on-ledger metadata attached to two ledger objects**:

1. A **DID** (XLS-40d) — a `DIDSet` transaction creates a `DID` ledger object bound to the agent's account. The `DID` object's `URI` field can point at the full W3C DID Document off-chain (recommended because of the 256-byte cap).
2. A **Verifiable Credential** (XLS-70) — a `CredentialCreate` transaction signed by an Issuer creates an unsigned offer; the Subject then submits a `CredentialAccept` to opt in. Both transactions are public.

Anyone in the world can verify an agent's identity and credentials by calling `ledger_entry` on the public XRPL node. **No registry. No third party. No privacy.**

---

## Wallets used in this run

| Role | DID | Address | Public Key (compressed) |
|---|---|---|---|
| **Subject (Agent)** | `did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` | `rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` | `ED2D6E...` |
| **Issuer (Authority)** | `did:xrpl:2:rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU` | `rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU` | `EDB36E...` |

Both were funded with 100 XRP from the public testnet faucet at the start of the run.

---

## Step-by-step issuance

### Step 1 — Wallet funding (off-ledger event)

| | |
|---|---|
| Method | `POST https://faucet.altnet.rippletest.net/accounts` |
| Output | Classic address + master seed + 100 XRP initial balance |

Not a ledger transaction — the faucet creates and funds the account out of band. The actual genesis payment is the first ledger transaction in the account's history (not surfaced here).

### Step 2 — `CredentialCreate` (Issuer signs)

The Issuer submits a transaction that creates a `Credential` ledger object. This is an *offer* until the Subject accepts.

| Field | Value |
|---|---|
| **Transaction type** | `CredentialCreate` |
| **Account (signer)** | `rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU` (Issuer) |
| **Subject** | `rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` |
| **CredentialType** | `6167656E745F6964656E746974795F7631` (hex of `agent_identity_v1`) |
| **URI** | hex of `ipfs://bafkreigh2akisc3d4dh5d4kpqj3u4w4k4k4k4k4k4k4k4k4k4k4k4k4k4k` |
| **Fee** | 15 drops (minimum for CredentialCreate) |
| **Flags** | `0` (not accepted yet) |

| | |
|---|---|
| **tx_hash** | `3C03A3E98889CB707237D5892A54E8938DB2A49C27A3BDCE32ECDC93EF2959E7` |
| **tx_result** | `tesSUCCESS` |
| **ledger_index** | `21123060` |
| **Testnet explorer** | https://test.bithomp.com/explorer/3C03A3E98889CB707237D5892A54E8938DB2A49C27A3BDCE32ECDC93EF2959E7 |

**On-ledger effect:** A `Credential` ledger object is created, indexed by `(Subject, Issuer, CredentialType)`. Before `CredentialAccept`, this object's `Flags` field does NOT have `lsfAccepted` (0x00010000) set.

### Step 3 — `CredentialAccept` (Subject signs)

The Subject submits a transaction that flips the `lsfAccepted` flag on the existing `Credential` object.

| Field | Value |
|---|---|
| **Transaction type** | `CredentialAccept` |
| **Account (signer)** | `rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` (Subject) |
| **Issuer** | `rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU` |
| **CredentialType** | `6167656E745F6964656E746974795F7631` |
| **Fee** | 15 drops |

| | |
|---|---|
| **tx_hash** | `580B8CD7BF932A5EB241A1C9956BF777A0A28AC85D3FB9DC9983D5929BC2243A` |
| **tx_result** | `tesSUCCESS` |
| **ledger_index** | `21123062` |
| **Testnet explorer** | https://test.bithomp.com/explorer/580B8CD7BF932A5EB241A1C9956BF777A0A28AC85D3FB9DC9983D5929BC2243A |

**On-ledger effect:** The `Credential` object's `Flags` field now has `lsfAccepted = 65536 (0x00010000)` set. Subsequent `CredentialAccept` calls for the same `(Issuer, Subject, CredentialType)` triple become no-ops.

### Step 4 — `DIDSet` (Subject signs)

The Subject writes a `DID` ledger object bound to its account.

**Size constraint observed:** A canonical W3C DID Core 1.0 document with a `Multikey` verificationMethod serialized to JSON is **~495 bytes** — over XLS-40d's 256-byte cap for `DIDSet.DIDDocument`. The XLS-40d-recommended pattern is therefore to:

1. Write a minimal stub on-ledger (or skip `DIDDocument` entirely), and
2. Put the full document behind `URI` (HTTPS or IPFS).

In this run we used the **URI-only pattern** — set `URI` to where the full DID Document can be fetched, leave `DIDDocument` empty:

| Field | Value |
|---|---|
| **Transaction type** | `DIDSet` |
| **Account (signer)** | `rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` (Subject) |
| **DIDDocument** | (empty) |
| **URI** | hex of `https://xrpl-agent-id.example/did/rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf` |
| **Fee** | 15 drops |

| | |
|---|---|
| **tx_hash** | `A230469E469496CF1811C9F1279E9D5F2058AAE9890730020CC2A9EC1098A3B3` |
| **tx_result** | `tesSUCCESS` |
| **ledger_index** | `21123064` |
| **Testnet explorer** | https://test.bithomp.com/explorer/A230469E469496CF1811C9F1279E9D5F2058AAE9890730020CC2A9EC1098A3B3 |

**On-ledger effect:** A `DID` ledger object is created, indexed by Subject's `AccountID`. `PreviousTxnID` and `PreviousTxnLgrSeq` track the latest `DIDSet` for this DID.

### Step 5 — Verification (anyone can do this)

A third-party verifier does NOT need any keys, accounts, or special software. Just a JSON-RPC client:

#### 5a. Resolve the DID

```python
client.request(LedgerEntry(did="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf"))
```

Returned `DID` ledger object:
- `DIDDocument`: empty (we used URI-only pattern)
- `URI`: hex-encoded pointer to off-chain document
- `Account`: `rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf`
- `LedgerEntryType`: `"DID"`

The URI decodes to `https://xrpl-agent-id.example/did/rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf`. A real verifier would fetch that URL to retrieve the full W3C DID Document.

#### 5b. Verify the credential

```python
client.request(LedgerEntry(credential=LedgerCredential(
    subject="rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
    issuer="rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU",
    credential_type="6167656E745F6964656E746974795F7631",
)))
```

Returned `Credential` ledger object (excerpt):
```json
{
  "CredentialType": "6167656E745F6964656E746974795F7631",
  "Flags": 65536,
  "Issuer": "rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU",
  "LedgerEntryType": "Credential",
  "Subject": "rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf",
  "URI": "697066733A2F2F6261666B726569676832616B69736333643464683564346B70716A33753477346B346B346B346B346B346B346B346B346B346B346B346B346B346B",
  "index": "5991D51E513B2686862DF3D4320455D03325950E87BDFAB0829B0C87C09E629A"
}
```

**Key checks for the verifier:**
- `Flags & 0x00010000 == 0x00010000` → `lsfAccepted` is set → Subject has explicitly opted in
- `CredentialType` matches what the agent claims
- `Issuer` is the address the agent claims vouched for them
- `URI` (decoded) points to the full claim details (IPFS in this run)

---

## What's visible on-ledger (and what isn't)

| Field | Visibility | Notes |
|---|---|---|
| Subject's XRPL address | **Public** | In every credential it receives |
| Issuer's XRPL address | **Public** | In every credential it issues |
| Credential type | **Public** | Encoded as hex on-ledger |
| URI pointing to claim details | **Public** | Anyone can fetch |
| Subject's DID | **Public** | Bound to subject's account |
| Subject's full DID Document | **Off-ledger** | Stored at URI (HTTPS / IPFS) |
| Subject's master public key | **Public** (derivable) | From the account's signing history |
| Credential acceptance state | **Public** | `lsfAccepted` flag is on-ledger |
| Subject's seed / private key | **Off-ledger** (never leaves wallet) | Signed transactions are public, seeds are not |
| Subject's KYC / real-world identity | **Off-ledger** | Up to Issuer to attach KYC data to the claim at URI |

---

## How an Agent ID is "issued" — short version

Three on-ledger transactions, in order:

```
Issuer --[CredentialCreate]--> XRPL   (creates Credential offer)
Subject --[CredentialAccept]--> XRPL   (subject opts in, lsfAccepted set)
Subject --[DIDSet]--> XRPL             (creates DID ledger object, optional URI)
```

The first is signed by the Issuer. The second and third are signed by the Subject.

After this, anyone can:
- Resolve `did:xrpl:<network-id>:<subject-address>` via `ledger_entry(did=...)`
- Verify the credential via `ledger_entry(credential={subject, issuer, credential_type})`

No on-chain registry. No off-chain attestation service. **The ledger IS the registry.**

---

## Why transparency is built-in

Because verification is just `ledger_entry` lookups:

1. **No trust in any single party.** A verifier doesn't need to trust an issuer's website or a registry's uptime. They query the same XRPL the issuer used.
2. **No key custody.** The library never logs, stores, or transmits seeds beyond what `xrpl-py` does for signing.
3. **Cryptographically attested.** Every transaction is signed by the wallet whose keys produce the address — non-repudiable by the signer.
4. **Public but pseudonymous.** Subject identity = XRPL address. Anyone the Issuer has done business with can map it; otherwise it's pseudonymous.

The cost of transparency is: every Agent ID is publicly linkable. There's no privacy layer. If you want private credentials, that's a different primitive (ZK proofs over `CredentialHash` instead of `URI`).

---

## What we learned (and what's next)

### What worked
- `xrpl-py 4.5.0` CredentialCreate/Accept/DIDSet work exactly per spec.
- The `ledger_entry(credential={subject, issuer, credential_type})` lookup pattern is clean — same triple indexes all three fields.
- Hex encoding `credential_type` (as `bytes.hex().upper()`) is straightforward.

### What surprised us
- The 256-byte cap on `DIDSet.DIDDocument` is real and bites immediately on canonical W3C docs. The URI-only pattern is the workaround; we documented it.
- `Credential` ledger objects use `Flags` field for `lsfAccepted` (bit 0x10000), not a separate boolean — worth noting for anyone hand-parsing.

### What's next
- Add `tests/test_integration_ledger_live.py` execution to CI with a cron-driven weekly testnet run (currently `--run-live` gated)
- Add a `--save-seeds` option to `scripts/issue_agent_id.py` so users can re-use the funded wallets across runs instead of always going to the faucet
- Document the `CredentialHash` (zero-knowledge) variant for private credentials
- MCP server wrapper (`xrpl_agent_id.mcp`) so an LLM agent can use the same primitives through the `mcp` Python SDK

---

## Raw artifacts

- `results/issuance_testnet_20260928_173724.json` — full structured output of the live run (tx hashes, raw ledger entries, decoded fields)
- `results/issuance_testnet_latest.json` — symlink-style copy, always points at the most recent run

Both contain:
- All transaction hashes, results, and ledger indexes
- Raw `DID` and `Credential` ledger entries as returned by `ledger_entry`
- The full canonical W3C DID Document that was built (for reference; it didn't fit on-ledger)
