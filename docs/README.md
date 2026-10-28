# Documentation

## Start here

| If you want to… | Read |
|---|---|
| **Get it running** | [`QUICKSTART.md`](QUICKSTART.md) — a working agent in under five minutes |
| **Understand the model** | [`ARCHITECTURE.md`](ARCHITECTURE.md) — DIDs, credentials, authorization, submission path |
| **See what it's for** | [`USE_CASES.md`](USE_CASES.md) — five concrete scenarios |
| **Read the API contract** | [`xrpl_agent_id/PUBLIC_API.md`](../xrpl_agent_id/PUBLIC_API.md) — every public name and its stability policy |
| **Know the cost** | [`MAINNET_DECISION.md`](MAINNET_DECISION.md) §1 — fees, reserve, and the lockup/release rules |
| **Decide about mainnet** | [`MAINNET_DECISION.md`](MAINNET_DECISION.md) — the go/no-go reasoning, plus the 2026-10-28 validation run and its results |
| **Review before trusting it** | [`03_known_issues.md`](03_known_issues.md) — five defects that shipped, and why the tests missed them |

## Reference

| Document | What it covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Internal design: DID documents, credential and authorization model, submission path, Merkle anchoring |
| [`QUICKSTART.md`](QUICKSTART.md) | Runnable first agent, plus the `WalletProvider` section |
| [`USE_CASES.md`](USE_CASES.md) | Concrete scenarios the library is meant for |
| [`MAINNET_DECISION.md`](MAINNET_DECISION.md) | Fee and reserve model, the mainnet gate, and the recorded decisions |
| [`03_known_issues.md`](03_known_issues.md) | Defects found and fixed, and why the tests missed them |
| [`01_issuance_flow.md`](01_issuance_flow.md) | End-to-end issuance sequence |
| [`CONTROLLER_BANNED_MULTISIG.md`](CONTROLLER_BANNED_MULTISIG.md) | Detecting a banned co-signer on a multi-sig account |
| [`02_testing_log.md`](02_testing_log.md) | Test counts across versions, and how to run the suite |
| [`00_executive_summary.md`](00_executive_summary.md) | Project overview and scope (also rendered to PDF) |
| [`03_dashboard.md`](03_dashboard.md) | Local operations dashboard |
| [`CHANGELOG.md`](CHANGELOG.md) | Full release history |

## Not maintained documentation

These are retained for the record. They are **not** kept current, and they
are not a guide to using the library.

| Document | Why it is here |
|---|---|
| [`internal/NOTES.md`](internal/NOTES.md) | Working session notes — retained for the record, not maintained |
| [`STRESS_TEST_v0.3.0.md`](STRESS_TEST_v0.3.0.md) | A frozen testnet stress-test record |

Generated reports and frozen stress-test records are retained for evidence
and are not updated. New results go to `results/` in the repository root.

## Before you rely on credential authorization

Five security defects were fixed in **v0.4.3**. Two of them let a credential
be counted as valid when it should not have been. See
[`03_known_issues.md`](03_known_issues.md) — and check which version you
actually have installed.
