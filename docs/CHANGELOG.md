# Changelog

All notable changes to `xrpl_agent_id` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`AgentIdentity.delete_did()`** — delete this agent's DID Document from
  the ledger via `DIDDelete`, and return the transaction hash. `DIDDelete`
  takes only the common transaction fields; the target is always the
  sender's own DID, so there is nothing for the caller to specify.

  Deleting the DID Document does **not** delete the account's credentials.
  The two are separate ledger objects: a DID is keyed by its owner
  account, and a credential by (issuer, subject, credential_type).
  rippled's `DIDDelete` erases only the DID object. To withdraw an
  authorization, revoke the credential too.

  If the account has no DID, the transaction fails with `tecNO_ENTRY`.



## [0.5.2] - 2026-10-28

Trust policy ergonomics. Four fixes to `TrustRegistry` and `TrustPolicy`,
found by reviewing the API rather than by a failing test.

### Fixed

- **A require rule with a set of issuers is reported once.** It was
  expanded into one `missing_required` entry per issuer, which read as
  though the agent needed a credential from *every* issuer in the set.
  A rule with three issuers is satisfied by a credential from any of
  them, so the result now reports one entry per unsatisfied rule.
- **An issuer-agnostic require rule can now pass.** The
  `issuer=None` branch of the satisfaction check returned `False`
  unconditionally, so `require(issuer=None, credential_type=...)` could
  never be satisfied. It is now answered from the same authoritative
  ledger read that `deny` rules use.
- **`credential_type` accepts `str`.** Every caller writes a URI, and
  `b"https://..."` was a papercut at every call site. Both forms are
  accepted and stored as bytes. `TrustPolicy.credential_type_bytes` is
  the normalized accessor.

### Added

- **`TrustRegistry.ban(subject, description=...)`** — a first-class ban
  rule. A `deny` rule is about credentials; a ban is about the agent.
  Ban rules are evaluated first, and the result reports `banned=True`
  and `ban_reason`. A ban with no credential type matches on the
  subject alone, regardless of what the agent holds.
- `TrustCheckResult.banned` and `TrustCheckResult.ban_reason`.
- `tests/test_trust_policy.py` — 24 tests covering all of the above.

### Fixed

- **A ban could fail open.** The ban branch populated
  `all_credentials_found` from the ledger before returning. If the
  ledger was unreachable or slow, that read raised and the ban was
  never enforced — the one rule that must always apply was the one
  rule that could fail open. A ban is now evaluated with no ledger
  access at all, so it holds regardless of ledger availability.
  `all_credentials_found` is empty for a banned agent, because the
  answer does not depend on it.

- **The controller-ban gap is now documented.** `TrustRegistry.ban()`
  bans the *subject*. It does not ban the agent's controllers — the
  signers on a multi-sig account. The internal `BannedAgentRegistry`
  module supports controller bans and `authorization.AuthorizationPolicy`
  consults it, but `AuthorizationPolicy` is not a public export and
  `TrustRegistry` does not read a ban list, so a controller ban is not
  reachable from the public API today. This is stated in `ban()`'s
  docstring and in `PUBLIC_API.md` rather than left implicit.

- **A ban written as a DID did not match.** `ban()` accepted an address
  or a DID, and the check compares against the subject's *address*. A
  DID-form ban was stored as the DID string and therefore never matched
  -- the agent was allowed through, silently. DIDs are now reduced to
  their address when the rule is created. Covered by three regression
  tests.

### Compatibility

Additive. `require`, `deny`, and `check` keep their signatures. The
`missing_required` grouping change alters the *contents* of that list
for rules with multiple issuers: it now has one entry per rule rather
than one per issuer. That is a more accurate report, but code that
counted entries would see a different number.

## [0.5.1] - 2026-10-28

Documentation and metadata accuracy. No change to the public API surface —
all 24 exported names are unchanged.

### Fixed
- **The documented examples did not run.** The README's "Authorization policy
  and audit" example and the QUICKSTART ban example used literal placeholder
  addresses (`"rAgent..."`, `"rIssuer..."`, `"rBadAgent..."`). Copying either
  and running it raised `ValueError: not a valid XRP classic address`. The
  examples had been checked for API existence but never executed end to end.
  They now use valid, unfunded, clearly-synthetic addresses.
- **The public-API contract listed symbols that do not exist.**
  `xrpl_agent_id/PUBLIC_API.md` described `Publisher`, `AcceptancePolicy`,
  `LedgerAuditLog`, `Journal`, `AgentRecord`, and `Registry` as internal-only.
  None of them exist in the package. It also omitted ten that do — the
  submodule exports — so a reader following the contract found nothing.
  Rewritten as two accurate tables: what is importable but unpinned, and what
  does not exist.
- **`SECURITY.md` described shipped features as future plans.** Its "Known
  limitations" said v0.5.0 *would introduce* `WalletProvider` and *would sign*
  `decision_id`. Both shipped in v0.5.0. The limitations now describe the
  actual current state, including that `ExternalSignerProvider` is only as
  trustworthy as the signing callable behind it.
- **The mainnet gate was still recorded as closed.** `docs/MAINNET_DECISION.md`
  read "NO-GO for mainnet" and listed the stress run as pending. The run
  executed on 2026-10-28 — 23 validated transactions, every funded account
  deleted, treasury `OwnerCount` 0 — and the document now records it.
- **Documented counts had drifted.** `SECURITY.md` listed `0.4.x` as the
  latest supported version, the README claimed a 19-name public surface
  against an actual 24, and three documents still quoted 341 tests.
- **The dashboard pointed at a path that exists on one machine.** Its server,
  monitor, and backfill scripts defaulted their SQLite path to an absolute
  local directory. They now default to `~/.xrpl_agent_id/dashboard.db`.

### Added
- **`tests/test_public_api.py` now pins the document, not just the code.**
  The existing test checked the exports against a hand-maintained list but
  never checked `PUBLIC_API.md`, which is how six invented names got in.
- **`tests/test_documented_counts.py`** — eight tests that derive every
  documented number from the repository and assert the documents agree: the
  public-surface size against `__all__`, module and test-module counts
  against the filesystem, test counts against the suite itself, and the
  supported-versions table against `pyproject.toml`.
- **`xrpl_agent_id/py.typed`** — the package is fully annotated but shipped no
  PEP 561 marker, so mypy and pyright ignored it.
- **README `Usage` section** covering the five real workflows, with all
  seventeen API calls verified against their real signatures.

### Changed
- The README now follows the convention established by `XRPLF/xrpl-py`:
  "Installation and supported versions" near the top, a Usage section, and
  Contributing and License sections. `docs/README.md` became a reader-path
  index.

## [0.5.0] - 2026-10-28

Mainnet readiness. `WalletProvider` and the `submit_with_retry` rewrite
shipped; the fee ceiling, batch anchoring, and the corrected cost model
landed with them.

### Fixed
- **The cost of using agent ID was documented as if it were a charge.** The
  README led with *"locks roughly 18 XRP of reserve, **never refunded**"* and
  *"costs about **1.4 XRP**."* Reserve is a balance requirement, not an
  expense — XRPL releases it when the objects are deleted — and the real
  spend per identity is ~0.02 XRP. The corrected figures are now the lead
  number, and the misleading "never refunded" claim is gone.
- **`docs/CHANGELOG.md` and the root `CHANGELOG.md` had diverged.** The root
  copy was current; the `docs/` copy stopped at 0.4.2 and never had the
  0.4.3 notes — and the README linked to the stale one twice while the
  current one was linked from nowhere. There is now one changelog, at
  `docs/CHANGELOG.md`, and the root file points at it.
- **Stale test counts.** The README and the executive summary both said
  "255 passed" against a suite that now runs 341.
- **`docs/ARCHITECTURE.md` was pinned to v0.3.2** — four versions behind, and
  missing `wallet.py`, `tx_submit.py`, `merkle.py`, `anchor_batch()`,
  `batch_proof()`, and the fee ceiling entirely.

### Added
- **`tests/test_read_only_authorization.py`** pins the invariant that the
  authorization and trust paths never submit a transaction. It fails if a
  future change makes the hot path write to the ledger.
- **`docs/ARCHITECTURE.md` §2.12–§2.13** document `tx_submit.py` and
  `merkle.py`.


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

### Added
- **Batch anchoring — one transaction per batch instead of per decision.**
  `xrpl_agent_id/merkle.py` provides a Merkle tree (domain-separated
  leaves, `SHA256(left || right)` interior nodes, odd node promoted
  unchanged) with `merkle_root`, `merkle_proof` and `verify_inclusion`.
  `AuditLog.anchor_batch()` collects the decisions that have not been
  anchored and puts them in a single transaction carrying the batch's
  Merkle root; `AuditLog.batch_proof()` returns the inclusion proof for
  any anchored decision.

  A verifier needs only the leaf, the proof path, and the anchored root
  — not the operator's database — so the batch is an audit rather than a
  claim. The proof path encodes direction, so a reordered or truncated
  path fails verification.

  At one million decisions a day this is the difference between roughly
  $6,650/day and roughly $6.65/day at the median fee. See
  `XRPL_COST_MODEL.md`.

- **`Authority.issue_credential_cheapest()`** — issues a credential
  without the optional DID object, for two transactions instead of
  three. XLS-70 defines the `Credential` object's `Subject` and `Issuer`
  as `AccountID`s; a DID object is *recommended* for W3C VC compliance,
  not required, and the DID is derivable from the address with no
  ledger access. A DID object can still be published later in one
  transaction.

  33% fewer setup fees, and 1.2 XRP of lockup instead of 1.4 XRP.

### Fixed
- **Mainnet reserve and fee figures were stale by ~100x.** `docs/MAINNET_DECISION.md`
  §1 and the README stated a 200 XRP base reserve and 2 XRP per owned ledger
  object. Those were the pre-2024 values. Reserves were lowered 10x by validator
  vote on 2024-12-02 to **1 XRP base / 0.2 XRP per object** (see
  [Lower Reserves Are In Effect](https://xrpl.org/blog/2024/lower-reserves-are-in-effect)).
  Figures are now verified against the live mainnet ledger via `server_info`
  (`reserve_base_xrp: 1.0`, `reserve_inc_xrp: 0.2`, `base_fee_xrp: 1e-05`).
  The per-transaction cost is quoted as **10 drops base** with a note to budget
  against the ledger median rather than the base. A one-agent identity costs
  ~1.4 XRP, not the ~2,080 XRP the old numbers implied for a ten-agent
  deployment. `MAINNET_READINESS_AUDIT_2026-10-27.md` §1.2 is corrected too --
  it had asserted the old figure was a protocol constant.

### Added
- **`on_chain_proof.signer`** — the dashboard's `/api/verify` now names the
  account that signed the transaction carrying the memo, so the response is
  self-contained and a reviewer does not have to open the explorer to find
  out who produced it. `_fetch_memo_from_tx` returns `(memo_hex, signer)`.

  The signer is the **audit mirror key**, not the credential issuer: the
  mirror deliberately runs on its own key so the issuer's key stays out of
  a high-volume logging path. A memo proves audit-service provenance, not
  issuer intent.

### Added
- **`WalletProvider` — a pluggable signer for XRPL transactions.** New public
  names: `WalletProvider` (the protocol), `SeedWalletProvider` (wraps an
  in-process `xrpl.wallet.Wallet`), and `ExternalSignerProvider` (delegates to
  a caller-supplied signing callable, for a KMS, HSM, or hardware wallet).

  The interface is three members — `classic_address`, `public_key`, and
  `sign(tx)` — and is structural, so any object with those members satisfies
  it. A KMS-backed provider therefore needs no key material in this process:
  it signs the canonical XRPL signing bytes and returns a signature.

  This is what makes the library usable in an institutional setting, where
  the signing key lives in a key management system rather than in the
  application. `xrpl-py` binds signing to a concrete `Wallet` object and
  offers no such interface; the ledger side already supported it, because
  `rippled`'s `submit` accepts a pre-signed `tx_blob` and `xrpl-py`'s
  `submit_and_wait` accepts an already-signed transaction with `wallet=None`.

- **`ExternalSignerProvider`** takes a signing callable and does the
  XRPL-specific encoding. It performs no internal retry and caches no
  signatures, so there is exactly one retry story rather than two.

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
- **`AgentIdentity.wallet` is now `AgentIdentity.provider`.** The field holds
  a `WalletProvider` rather than a concrete `Wallet`, because a provider that
  signs externally has no wallet to hold. The old name still works and emits
  a `DeprecationWarning`; it will be **removed in v0.6.0**, giving two
  releases of overlap. `AgentIdentity.from_seed` is unchanged in behaviour —
  it still produces an identity backed by an in-process wallet.

- **`AgentIdentity.seed` is deprecated** and will be removed in v0.6.0. A
  provider that signs externally has no seed at all, so this property cannot
  be part of the identity contract — only of the in-process-wallet case. On a
  `SeedWalletProvider` it still works, with a warning; on any other provider
  it raises `AttributeError` with a message that says so.

- **`XRPLMirror` (in `xrpl_agent_id.audit`) holds a provider** under the
  attribute name `provider`, for the same reason. Its `from_seed` is
  unchanged.

- **Provider-mode submission re-autofills and re-signs on every attempt.**
  This is a behavioural fix, not just a refactor. `xrpl.transaction.submit_and_wait`
  autofills an *unsigned* transaction on every call, so resubmitting the same
  transaction object is safe. But its internal `_get_signed_tx` returns
  immediately when a transaction is already signed — and a provider signs
  before submitting. A retry would therefore have resubmitted the identical
  stale blob, with the original `LastLedgerSequence` still baked in, failing
  with `tefMAX_LEDGER` exactly when the retry was most needed. Measured
  autofill horizon on public testnet: ~19 ledgers (~76 s) against a retry
  backoff of 2 s then 4 s — normally inside the window, but not on a
  congested ledger, which is when transient errors occur.

  This is the `tefPAST_SEQ` failure mode of v0.4.2 reappearing in a new
  place, and it is now covered by a regression test.

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
- **A quickstart smoke test depended on CPython's exact error wording.** It
  asserted that a `TypeError` message contained `"can't be used in 'await'
  expression"`; CPython 3.14 changed that to `"'MagicMock' object can't be
  awaited"`, so the test errored on 3.14 despite correct library behaviour.
  The assertion now checks the intent — that the `TypeError` came from
  awaiting the mock rather than from the library's own argument validation.

- **The same smoke test no longer relies on a bare `MagicMock` standing in
  for a network client.** With a provider, the library autofills and signs
  before submitting, and autofill asks the client for `account_info`,
  `server_info`, fee and ledger data — so a bare mock is no longer a usable
  stand-in. The test now stubs the submission directly, which is both
  simpler and more honest about what the test actually checks (the documented
  call shape, not reaching the ledger).

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