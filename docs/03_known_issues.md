# Known Issues

Disclosed defects in `xrpl_agent_id`, with the version that fixed each one.

Everything listed here shipped in **v0.4.3** (2026-10-23). All five are fixed.
If you are on `v0.4.2` or earlier, you have every one of them.

This page exists so adopters can see what was wrong, when it was found, and why
the tests missed it — rather than having to rediscover any of it.

## Fixed in v0.4.3

### 1. Credentials were treated as accepted when they were not

**Status:** fixed in v0.4.3
**Component:** `credential.py`, `identity.py`, `authority.py`

XRPL distinguishes a credential existing from a credential being accepted. When
an issuer creates a credential it exists on the ledger immediately, but the
`lsfAccepted` flag is only set when the subject signs `CredentialAccept`. XLS-70
states plainly that a credential "should not be considered 'valid' until it has
been accepted."

The library checked that a credential existed and treated that as acceptance. A
credential the subject never accepted could be counted as held.

`credential_is_accepted()` now reads the `lsfAccepted` bit (`0x00010000`) from
the raw ledger node and fails closed — anything unparseable is treated as not
accepted, so a malformed response cannot widen access.

### 2. Credential expiry was never checked on the real path

**Status:** fixed in v0.4.3
**Component:** `registry.py`, `authorization.py`

`_parse_credential_obj` did not carry the ledger's `Expiration` field onto the
`Credential` object. `authorization.py` gates on:

```python
if cred.expiration is not None:   # always False for ledger-parsed credentials
```

The `CREDENTIAL_EXPIRED` branch was unreachable for any credential read from the
ledger, and an expired credential still counted as valid.

`Expiration` is a real optional `UInt32` field per XLS-70, and is now parsed.

### Why the test suite stayed green

Both existing tests build `Credential` objects directly instead of reading them
back from a ledger. They exercised a path that behaves differently from
production, so both bugs were invisible to CI.

`tests/test_regression_security.py` now covers both and fails against the
pre-fix tree. That is the general lesson: a test that constructs its own inputs
proves nothing about how the library behaves on real ledger data.

### 3. DID documents were not validated

**Status:** fixed in v0.4.3
**Component:** `did.py`

Two unvalidated-input paths:

- `did_from_account()` interpolated any string into a DID, so
  `did_from_account("../../etc/passwd")` produced a well-formed-looking DID
  pointing at an attacker-chosen path.
- A DID document was accepted whatever its `id` claimed. Account A could publish
  a document whose `id` was Account B's DID, and any consumer keying on
  `doc.id` would attribute B's keys to A — identity spoofing requiring no key
  material. The document's `id` must now match the DID being resolved.

### 4. A missing ban file silently allowed everyone

**Status:** fixed in v0.4.3
**Component:** `banned.py`

A missing ban file returned an empty deny-list in silence. A typo, a moved file,
or running from a different working directory meant every banned agent was
quietly allowed — a security control failing open with no indication anything was
wrong.

The default path now resolves against the user's home directory rather than the
current working directory, and a missing file emits a `RuntimeWarning` naming the
resolved path.

A `strict=True` option is available for anyone using the ban list as a real
gate; it makes a missing or corrupt file an error instead. It is opt-in by
design — `banned.json` is not tracked, so a fresh clone has no file and defaulting
strict to `True` would raise `FileNotFoundError` on first use for every new
adopter.

### 5. The dashboard reported unverifiable decisions as verified

**Status:** fixed in v0.4.3
**Component:** `dashboard/server.py`

Three issues in the local operations dashboard:

- The file browser returned absolute paths, disclosing the operator's home
  directory layout to any viewer. Relative paths are all it needs.
- The seed denylist was three exact filenames. A name-based list is defeated by
  renaming — and this walk had already surfaced seed-bearing artifacts such as
  `results/stress_harness_live_seeds.json`. Now pattern-based across seeds,
  secrets, wallets, keystores, mnemonics, keys, and PEM files.
- The decision endpoint returned `verified: true` for a caller-supplied memo.
  `decision_id` is an **unsalted** SHA-256 content hash
  (`audit.compute_decision_id`), so anyone who can read a decision can compute
  its id and hand-craft a memo that reproduces it. Nothing about that path
  demonstrates anything reached the ledger. `verified` is now true only when
  `on_chain_proof` is present — a memo actually fetched from a transaction. The
  separate `local_record_matched` field still reports the local match, so the
  distinction stays visible.

The third is the same shape as items 1 and 2: a green check that did not mean
what its name claimed.

## Reporting a vulnerability

See [`SECURITY.md`](../SECURITY.md).