# xrpl_agent_id — Executive Summary

**Track:** Identity & Trust
**Network:** XRP Ledger (XLS-70 Credentials + XLS-40d DIDs)
**Status:** Live on XRPL Testnet · 31/31 tests passing
**License:** MIT

---

## The Problem

AI agents are taking real actions — spending money, calling APIs, signing contracts. But they have **no public identity** and **no portable way to prove who vouched for them**. Today, agent trust is a promise made by whoever runs the platform. That's invisible, vendor-locked, and impossible to audit.

## The Solution

**`xrpl_agent_id`** — a Python library that gives every AI agent a public, verifiable identity on the XRP Ledger. No new chain. No new consensus rule. No validator vote. Just the existing XLS-70 Credential and XLS-40d DID primitives, packaged for AI developers.

Anyone in the world can verify any agent's identity and credentials with a single `ledger_entry` call — no API key, no permission, no trust in a vendor's internal logs.

## How It Works

Three on-ledger transactions:

1. **Issuer** signs `CredentialCreate` — public attestation ("this agent passed audit X")
2. **Agent** signs `CredentialAccept` — opts in, makes acceptance itself auditable
3. **Agent** signs `DIDSet` — pins its W3C-compatible DID Document (URI-only pattern, fits in the 256-byte on-ledger cap)

Verifiers query the ledger. They see the credential, the issuer, the timestamp, the URI to the full claim details. Done.

## What's Already Built (Working Demo)

- Full Python library: `xrpl_agent_id` (open-source, MIT licensed)
- **Live testnet demo** — all three transactions confirmed on XRPL testnet
  - Subject: `did:xrpl:2:rPNGAyKowBrtpbeVjzDkHG4uJBKdV1q3cf`
  - Issuer: `did:xrpl:2:rHDfNdLUEd7tBXZCaUuuK5wMcKzhtpNDBU`
- 27 offline tests passing, 4 live integration tests passing
- Standard library ergonomics: `AgentIdentity.from_seed()`, `issuer.issue_credential()`, `agent.has_credential()`

## Why XRPL

- **Both primitives already live on mainnet** (XLS-40d since 2022, XLS-70 since 2024) — zero protocol changes required
- **3–5 second finality** — fast enough for agent-to-agent commerce
- **Sub-cent fees** — cheap enough for high-frequency agent activity
- **Native hooks for future enhancement** — credential-aware transactions, native DID resolution RPC, MPC signers are all on the roadmap without requiring forks

## Why Now

- Agent-to-agent commerce is the next frontier (LangChain, MCP, Composio — all exploding)
- Every agent framework today inherits the same closed-trust problem
- The window for setting the standard is open — first credible Python library wins

## The Ask

Looking for:
- **Validation** from the XRPL community on the credential-type taxonomy (KYC, EVAL, OPERATOR, COMPLIANCE, etc.)
- **An early issuer partner** to run a pilot: any organization willing to issue credentials against real audit data
- **Testnet feedback** before mainnet launch

---

## One Line

> **Today, agent identity is a promise. This makes it a public fact.**

---

**Contact:** Sam · @Sam_Intelligence_Bot · GitHub: [repo pending publication]
