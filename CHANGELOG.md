# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Live-ledger tests failed roughly 1 run in 4 on public Testnet and Devnet**
  with `tefPAST_SEQ`. A ledger closed between autofill and validation, so the
  transaction's 75-ledger `LastLedgerSequence` window expired before it landed.
  This was reproducible on freshly cloned `main`, not a local-worktree artifact.
  New `xrpl_agent_id/tx_submit.py` provides `submit_with_retry()` — wired into
  all six submit sites across `identity.py`, `authority.py`, and `audit.py`.
  Transient ledger races retry with linear backoff (2s, 4s); permanent errors
  (`telINSUF_FEE_P`, `temBAD_AMOUNT`, …) still raise immediately.
  Retry is meaningful because `xrpl.transaction.submit_and_wait` autofills on
  every call, re-deriving `Sequence` and `LastLedgerSequence`; a regression test
  asserts that default so the retry cannot silently degrade into resubmitting a
  dead window forever. Measured after the fix: 7/7 clean live runs on Testnet
  (was 5/6), and failing runs no longer burn ~130s in a retry loop.
- **A spendable-format XRPL seed was published in a public test.**
  `tests/test_stress_harness_throughput.py` hardcoded a valid-format ed25519
  seed, which resolves to a real `r...` address. The account has never been
  funded (`actNotFound` on testnet), so no funds were ever at risk — but
  anyone could fund it, and the literal tripped secret scanners. The fixture now
  generates its seed at runtime with `Wallet.create()`, so there is nothing to
  steal and nothing to allowlist. The `ALLOWLIST` entry that existed only to
  excuse it is gone. (The seed literal is deliberately not reproduced here —
  quoting it would reintroduce exactly what this entry removes.)

### Added
- This CHANGELOG. `xrpl_agent_log` had one; this package did not.
- `tests/test_tx_submit_retry.py` — 10 tests covering transient-vs-permanent
  error classification, retry counts, backoff monotonicity, the
  `submit_and_wait`-autofills invariant, and a guard that fails if any library
  module reintroduces a direct un-retried `submit_and_wait` call.

## [0.4.2] - 2026-10-22

- Version metadata synced with `pyproject.toml`; changelog added.

## [0.4.1] - 2026-10-01

- CI pinned to `ubuntu-24.04` to avoid `ubuntu-latest` drift.
- PII scrub: absolute path and Telegram identifier redacted from the tree.
- `tests`: version and path assertions made portable across clone locations.

## [0.4.0] - 2026-09-30

- Enforced XLS-70 credential expiration.
- Import cleanup and doc consistency pass; `pyproject.toml`, `README.md`, and
  `.gitignore` prepared for release.

## [0.3.0]

- Stress-test tooling and the throughput/error-classification harness.

## [0.2.4]

## [0.2.0]