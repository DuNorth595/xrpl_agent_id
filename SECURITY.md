# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| `0.4.x` (latest) | ✅ |
| `0.3.x` | ⚠️ Critical fixes only |
| `< 0.3` | ❌ No longer supported |

## Reporting a vulnerability

**Do not file a public GitHub issue for security bugs.**

Email: `S_DevLabs@outlook.com` (PGP not currently configured — cleartext
accepted for now, switch to a PGP-encrypted channel if you need one and
I'll add a key). Expect an acknowledgement within 72 hours.

Please include:
- Reproduction steps (a failing test is the best case)
- Impact assessment (what an attacker can do)
- XRPL testnet vs. mainnet
- Any associated transaction hashes (testnet is fine to share)

## Threat model

`xrpl_agent_id` is a **client-side library** that signs XRPL transactions
locally using `xrpl-py`. It does not run a server, does not hold user funds
on the user's behalf, and does not custody keys.

| Asset | Where it lives | What this library does with it |
|---|---|---|
| Issuer seed | Operator's machine (or KMS, in v0.5.0) | Uses it to sign `CredentialCreate` transactions via `xrpl-py`. **Never logs it. Never writes it to disk.** |
| Subject seed | Agent's machine | Uses it to sign `CredentialAccept` and `DIDSet`. **Never logs it. Never writes it to disk.** |
| DID Document | Public on-ledger | Reads via `ledger_entry`. Writes via `DIDSet` signed by subject. |
| Credential object | Public on-ledger | Reads via `ledger_entry`. Writes via `CredentialCreate` signed by issuer. |
| Audit memo | Public on-ledger | Writes via `Payment` transaction (memo field). Plaintext by design — anyone can verify. |
| Trust decision result | Local file (`results/*.json`) | Written by test/stress scripts; not transmitted. |

## Seed-handling rules (for contributors)

1. **Never log a seed.** `print(seed)`, `logger.info(seed)`, `repr(wallet)`,
   `f"wallet={wallet}"`, `traceback.print_exception()` on a wallet — all forbidden.
   `xrpl-py`'s `Wallet` class does not override `__repr__`, so it leaks the seed
   in default reprs. Always log the **classic address** (`wallet.classic_address`),
   never the wallet object itself.
2. **Never commit a seed.** Even for test fixtures. The pre-commit hook
   `scripts/check_no_seeds.py` greps staged blobs for known seed patterns.
   If you need a wallet for a test, generate one inside the test:
   `Wallet.create()`.
3. **Persist test scripts' seeds with `chmod 600`.** Pattern from
   `docs/STRESS_TEST_v0.3.0.md` — write to `~/.xrpl_testnet_seeds.json` with
   `os.chmod(path, 0o600)`, never to a world-readable path.
4. **Use environment variables, never hardcoded literals.** `os.environ["ISSUER_SEED"]`,
   not `Seed.from_string("sEd...")`. The env var should be set by the operator
   in a shell that's not logged.
5. **No seeds in error messages.** When wrapping exceptions, log
   `e.args[0]` (the human message), not the full exception object that
   might contain a seed attached by an upstream library.
6. **Memory hygiene is out of scope for this project.** Python strings are
   immutable; you can't reliably scrub a seed from process memory after
   use. Don't try — instead, minimize the seed's lifetime: load, sign,
   drop the reference, `gc.collect()` is overkill but reasonable in
   long-lived processes.

## Cryptographic choices

- **Signing algorithm:** `xrpl-py` uses ECDSA over secp256k1 for transaction
  signing. Library inherits this — no custom crypto.
- **Key encoding:** Classic seed format (`s...`) for human-readable backups,
  hex seed format (`00...`) for machine use. Both are supported by
  `xrpl-py`'s `Wallet.from_seed()`. Library does not introduce a new format.
- **Hashing:** SHA-256 via `hashlib` for `decision_id` computation. No
  custom hash construction.
- **No post-quantum crypto.** Plan: revisit if/when XRPL adopts a PQC
  signing scheme. Not on the v0.5.0 roadmap.

## Dependencies

`xrpl_agent_id` has **one runtime dependency** (`xrpl-py>=4.5.0`) and
two optional dependency groups:

| Extra | What it adds | Why it's optional |
|---|---|---|
| `mcp` | Model Context Protocol server wrapper | Only needed if you're exposing the library to an LLM agent host |
| `dev` | `pytest>=8.0` | Only needed for running the test suite |

`xrpl-py` is a well-maintained library from the XRP Ledger Foundation.
We pin a minimum version (`>=4.5.0`) but do not pin the patch version.
For supply-chain hardening, run `pip-audit` against your installed
environment; we plan to add this to CI in a future release.

## Known limitations

1. **No KMS / hardware wallet support yet.** v0.5.0 will introduce a
   `WalletProvider` interface that delegates signing to external HSMs
   (AWS KMS, YubiHSM, Ledger). Today, seeds live in process memory or
   the filesystem.
2. **No signed audit memos yet.** `XRPLMirror.write()` writes plaintext
   audit memos. Anyone can rewrite a memo (the ledger won't reject it)
   — the immutability is only "nobody changed the original tx hash",
   not "this is the canonical audit decision." v0.5.0 will sign
   `decision_id` with the operator's wallet so verifiers can confirm
   provenance.
3. **Live integration tests skip by default.** `tests/test_integration_ledger_live.py`
   is gated on `RUN_LIVE=1` because it creates real funded wallets on
   the public XRPL testnet. This is intentional — CI doesn't have
   funded testnet wallets. If you fork this library and run those tests
   yourself, be aware that the test wallets are ephemeral but the
   issued credentials persist on the testnet ledger indefinitely.

## Bug bounty

Not currently offered. For responsible disclosure of significant bugs,
email `S_DevLabs@outlook.com` — credit will be given in the CHANGELOG
and the README contributors section, and the fix will be coordinated
with you before public disclosure.

## Acknowledgements

This policy is modelled on the [GitHub Security Lab's guide to writing
a SECURITY.md](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository)
and [xkcd 538](https://xkcd.com/538/).
