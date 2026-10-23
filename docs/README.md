# Documentation

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Internal design: DID documents, credential and authorization model, submission path |
| [QUICKSTART.md](QUICKSTART.md) | Runnable first agent in under five minutes |
| [USE_CASES.md](USE_CASES.md) | Concrete scenarios the library is meant for |
| [00_executive_summary.md](00_executive_summary.md) | Project overview and scope |
| [01_issuance_flow.md](01_issuance_flow.md) | End-to-end issuance sequence |
| [03_dashboard.md](03_dashboard.md) | Local operations dashboard |
| [03_known_issues.md](03_known_issues.md) | Defects found and fixed, and why the tests missed them |
| [NOTES.md](NOTES.md) | Working notes — not maintained documentation |

Generated reports and frozen stress-test records are retained for evidence and
are not updated. New results go to `results/` in the repository root.

## Before you rely on credential authorization

Five security defects were fixed in **v0.4.3**. Two of them let a credential be
counted as valid when it should not have been. See
[03_known_issues.md](03_known_issues.md) — and check which version you actually
have installed.