# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-10-27

This section covers the in-progress v0.5.0 work. Steps A and B of the v0.5.0
track are described below; the `WalletProvider` interface and the
`submit_with_retry` rewrite are still in design (see
`WALLETPROVIDER_SPEC_2026-10-27.md`).

### Breaking changes
- **Python 3.9 is no longer supported.** `requires-python` is now `>=3.10` and
  the `Programming Language :: Python :: 3.9` classifier is removed. The
  `xrpl-py` dependency is pinned to `>=5.0,<6`, and xrpl-py 5.x itself requires
  Python >= 3.10.

  *Why now:* the dependency was declared `xrpl-py>=4.5.0` — unbounded. Because
  xrpl-py 5.x requires Python >= 3.10, a fresh install on Python 3.9 resolved to
  xrpl-py 4.x while an install on Python 3.10+ resolved to 5.x. The same release
  of this package therefore ran against **two major versions of its only
  dependency**, and only one of them was ever tested. An institution
  standardizing on a supported Python got xrpl-py 5.x; the 3.9 path was an
  untested minority.

  This is a *dependency* constraint, not a syntax one: every module using
  `X | None` annotations carries `from __future__ import annotations`, so those
  annotations are never evaluated at runtime.

  *Migration:* upgrade to Python 3.10 or newer and reinstall. No code changes
  are required — the package's own API is unchanged by this release.

### Changed
- **`xrpl-py` is pinned to `>=5.0,<6`.** The upper bound is deliberate: a major
  bump in the dependency is a breaking change for this package and should be
  made deliberately, not absorbed silently by the resolver.
- **CI matrix is now Python 3.10, 3.11, 3.12, and 3.13** — matching xrpl-py 5.x's
  supported range exactly. 3.9 was removed; 3.13 was added, so an adopter on a
  current Python is not the first to find a break.
- **A new `resolve` CI job reports the resolved dependency versions**, so the
  xrpl-py version actually in use is visible in the CI log rather than implied.
- **README badges** now read "Python 3.10+" and "xrpl-py 5.0+".
- **`CONTRIBUTING.md`** documents the 3.10–3.13 matrix. Its expected test count
  was also corrected from 201 to 227, which was stale.

### Fixed
- **A test asserted on CPython's exact error wording and broke on Python 3.14.**
  `tests/_smoke/test_quickstart_api.py` asserted that a `TypeError` message
  contained the string `"can't be used in 'await' expression"`. CPython 3.14
  changed that message to `"'MagicMock' object can't be awaited"`, so the
  quickstart smoke tests errored on 3.14 even though the library behaved
  correctly. The assertion now checks the *intent* — that the `TypeError`
  came from awaiting the mock rather than from the library's own argument
  validation — instead of a CPython-version tripwire.

  No library behaviour changed. The 227-test suite passes on Python 3.11 and
  3.14 with xrpl-py 5.2.0.

## [0.4.3] - 2026-10-23

### Security
- **Credentials were treated as accepted when they were not.** XRPL separates a
  credential *existing* from a credential being *accepted*: `lsfAccepted` is only
  set when the subject signs `CredentialAccept`, and XLS-70 says plainly that a
  credential "should not be considered 'valid' until it has been accepted." The
  library checked existence and called it acceptance, so a credential the subject
  never accepted could be counted as held. `credential_is_accepted()` now reads
  the `lsfAccepted` bit (`0x00010000`) from the raw ledger node and fails closed —
  an unparseable response is treated as not accepted, so malformed data cannot
  widen access.
- **Credential expiry was never checked on the real path.** `_parse_credential_obj`
  did not carry the ledger's `Expiration` field onto the `Credential` object, so
  `authorization.py`'s `if cred.expiration is not None` guard was always False for
  any ledger-parsed credential and the `CREDENTIAL_EXPIRED` branch was
  unreachable. An expired credential still counted as valid. `Expiration` is a
  real optional `UInt32` per XLS-70 and is now parsed.
- **DID documents were not validated.** `did_from_account()` interpolated any
  string into a DID, so `did_from_account("../../etc/passwd")` produced a
  well-formed DID pointing at an attacker-chosen path. Separately, a DID document
  was accepted whatever its `id` claimed — account A could publish a document
  whose `id` was account B's, and any consumer keying on `doc.id` would attribute
  B's keys to A. That is identity spoofing requiring no key material. The
  document's `id` must now match the DID being resolved.
- **A missing ban file silently allowed everyone.** A missing `banned.json`
  returned an empty deny-list with no indication anything was wrong, so a typo, a
  moved file, or running from a different working directory meant every banned
  agent was allowed — a security control failing open. The default path now
  resolves against the user's home directory rather than the CWD, and a missing
  file emits a `RuntimeWarning` naming the resolved path. A `strict=True` option
  raises instead of warning; it is opt-in because the file is untracked and
  defaulting strict would break first use for every new adopter.
- **The dashboard reported unverifiable decisions as verified.** `decision_id` is
  an *unsalted* SHA-256 content hash, so anyone who could read a decision could
  compute its id and hand-craft a memo reproducing it — the endpoint returned
  `verified: true` for a caller-supplied memo, demonstrating nothing had reached
  the ledger. `verified` is now true only when `on_chain_proof` is present. The
  separate `local_record_matched` field still reports the local match, so the
  distinction stays visible. The same commit stops `/api/files` returning absolute
  paths (which disclosed the operator's home directory layout) and replaces the
  three-filename seed denylist with pattern-based matching across seeds,
  secrets, wallets, keystores, mnemonics, keys, and PEM files.

Full disclosure and reasoning: `docs/03_known_issues.md`.

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