# xrpl_agent_id — Executive Summary

**Identity & Trust** · XRP Ledger (XLS-70 Credentials + XLS-40d DIDs)
**Live on XRPL Testnet** · 49/49 tests passing (43 offline + 6 live) · MIT licensed

**Contact**
Justin Douglas
justindoug@gmail.com
+1 (612) 219-8861

---

> **Today, agent identity is a promise. This makes it a public fact.**

---

## The Problem

AI agents are taking real actions — spending money, calling APIs, signing contracts. But they have **no public identity** and **no portable way to prove who vouched for them**. Today, agent trust is a promise made by whoever runs the platform. That trust is invisible, vendor-locked, and impossible to audit. When an agent misbehaves, there's no public trail — only the platform vendor's private logs.

## The Solution

**xrpl_agent_id** — a Python library that gives every AI agent a public, verifiable identity on the XRP Ledger. **No new chain. No new consensus rule. No validator vote.** Just the existing XLS-70 Credential and XLS-40d DID primitives, packaged for AI developers. Anyone in the world can verify any agent's identity and credentials with a single `ledger_entry` call — no API key, no permission, no trust in a vendor's internal logs.

## How It Works

**Three on-ledger transactions:**

1. **Issuer** signs `CredentialCreate` — public attestation ("this agent passed audit X")
2. **Agent** signs `CredentialAccept` — opts in; acceptance itself becomes auditable on-ledger
3. **Agent** signs `DIDSet` — pins its W3C-compatible DID Document (URI-only pattern, fits the 256-byte on-ledger cap)

Verifiers query the ledger. They see the credential, the issuer, the timestamp, the URI to the full claim details. Done.

## What's Already Built (Live Demo)

- **Subject:** `did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf`
- **Issuer:** `did:xrpl:2:rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU`
- **CredentialCreate:** `3C03A3E9…59E7` (ledger 21123060)
- **CredentialAccept:** `580B8CD7…243A` (ledger 21123062)
- **DIDSet:** `A230469E…A3B3` (ledger 21123064)

**Library state:** Full Python package, MIT licensed. **43 offline + 6 live integration tests passing (49/49 total).** Ergonomic API: `AgentIdentity.from_seed()`, `issuer.issue_credential()`, `agent.has_credential()`, `resolve_did()`, plus trust library: `TrustRegistry.require()/deny()/check()`.

## Why XRPL

- **Both primitives already live on mainnet** — XLS-40d since 2022, XLS-70 since 2024. Zero protocol changes required.
- **3–5 second finality** — fast enough for agent-to-agent commerce
- **Sub-cent fees** — cheap enough for high-frequency agent activity
- **Native hooks for future enhancement** — credential-aware transactions, native DID resolution RPC, MPC signers are all on the roadmap without requiring forks

## Why Now

Agent-to-agent commerce is the next frontier — LangChain, MCP, Composio are all exploding. Every agent framework today inherits the same closed-trust problem. **The window for setting the standard is open — the first credible Python library wins.**

## The Ask

Looking for three things:

1. **Validation** from the XRPL community on the credential-type taxonomy (`KYC`, `EVAL`, `OPERATOR`, `COMPLIANCE`, etc.)
2. **An early issuer partner** to run a pilot — any organization willing to issue credentials against real audit data
3. **Testnet feedback** before mainnet launch

---

*Prepared 2026-09-28*
