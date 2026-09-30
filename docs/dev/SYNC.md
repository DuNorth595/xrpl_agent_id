# Doc sync checklist

Run this every time you bump the version, before tagging a release, and after
any commit that renames files or refactors public APIs. Public-facing docs that
fall out of sync with the code are the #1 source of "the README says one thing,
the code does another" issues.

## 1. Find every place a version string appears

```bash
cd ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID
grep -rn -E "0\.[0-9]+\.[0-9]+" --include="*.md" --include="*.py" --include="*.toml" \
  --exclude-dir=.git --exclude-dir=.publish-venv --exclude-dir=.install-test \
  --exclude-dir=dist --exclude-dir=build --exclude-dir=__pycache__ \
  | grep -v "^Binary" | grep -vE "\.pdf:|\.html:"
```

Expected files: `xrpl_agent_id/__init__.py`, `pyproject.toml`, `README.md`,
`docs/CHANGELOG.md`, `docs/QUICKSTART.md`, `docs/MAINNET_DECISION.md`,
`docs/NOTES.md`, `xrpl_agent_id/PUBLIC_API.md`, possibly `xrpl_agent_id/dashboard/server.py`
(the dashboard pins its own `server_version` string).

Update every match that is meant to reflect the new version. **Do not touch**
historical changelog entries — those should keep the version they describe.

## 2. Find every place a commit SHA is mentioned

```bash
git log --all --oneline | head -50    # pick the SHAs you need to cite
grep -rn -E "\b[0-9a-f]{7,40}\b" --include="*.md" docs/
```

This catches both real SHAs and *fabricated* ones — six-character and seven-character
hex strings in `docs/NOTES.md` or `docs/MAINNET_DECISION.md` are almost always
meant to be SHAs. Look each one up in `git log` and substitute the real value
or remove it.

**Anti-pattern that caused real bugs in v0.4.0 prep:** pasting SHAs from a
previous session's summary without verifying they exist. The summary said
`44a59db`, the real commit was `3b98dda`. Always look it up.

## 3. Verify version auto-sync surfaces

The library exposes the version in three places, and a test enforces that two
of them stay in sync:

```bash
grep -nE "^__version__|XRPL_AGENT_ID_VERSION" xrpl_agent_id/__init__.py
python -m xrpl_agent_id --version
# expected: "xrpl_agent_id 0.X.Y"
```

The relevant test (`tests/test_version_alias_consistency.py`) checks that
`xrpl_agent_id.__version__ == xrpl_agent_id.XRPL_AGENT_ID_VERSION`. As long as
both derive from the same constant, they can't drift.

## 4. Verify dashboard version surface

The dashboard has its own `server_version` string in
`xrpl_agent_id/dashboard/server.py`. It tracks the library version, but with
a small offset in some releases — check the CHANGELOG entry for whether to
bump it.

```bash
grep -n "server_version" xrpl_agent_id/dashboard/server.py
# Then, with the dashboard running:
curl -s http://localhost:<port>/api/version | python -m json.tool
```

Expected output shape:

```json
{
  "package":         "xrpl_agent_id",
  "package_version": "0.X.Y",
  "server_version":  "0.X.Z",
  "python_version":  "...",
  "xrpl_py_version": "4.5.0",
  "now":             ...,
  "ok":              true
}
```

If `package_version` doesn't match `xrpl_agent_id/__init__.py`, something is
importing the wrong module — investigate before tagging.

## 5. Verify test counts

Run from **the project root, never from `/tmp` clones**:

```bash
cd ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected output: `N passed, M skipped, 0 failed` where M is the number of live
XRPL testnet integration tests that need funded wallets
(`tests/test_integration_ledger_live.py`).

**Critical:** some tests assert filesystem path suffixes (e.g.,
`test_files_api_lists_project` checks `result["root"].endswith("XRPL_AGENT_ID")`).
Run from any other directory and they fail with confusing messages. This has
caused false "1 failed" reports in the past.

Update the test-count line in `docs/NOTES.md`, `README.md`, and the GitHub
Release notes to match.

## 6. Spot-check changelog claims

For each "### Fixed" / "### Added" / "### Changed" item in the new CHANGELOG
entry, grep for the file:line it claims to change.

```bash
# Example: CHANGELOG says CREDENTIAL_EXPIRED is emitted at authorization.py:312
grep -n CREDENTIAL_EXPIRED xrpl_agent_id/authorization.py
```

If the line number is off by one, update the CHANGELOG. If the symbol doesn't
exist at all, the claim is fabricated — fix it before tagging.

## Pitfalls

- **Never claim test counts you haven't just run.** 158/158 was a number from
  a previous session's stale notes that ended up in `docs/NOTES.md` for v0.4.0.
  Always re-run from project root.
- **Never claim commit SHAs you haven't looked up.** `git log --oneline | head`
  is two commands, not a guess.
- **Never trust a README example output.** Verify by running the CLI or
  hitting the endpoint, not by copy-pasting from a previous release's notes.
- **Never edit `CHANGELOG.md` historical entries** to make current claims
  "consistent." The whole point of a changelog is that past versions' entries
  describe those versions' state. If you broke something in 0.3.x, the 0.3.x
  entry is true — fix the current version's entry to reflect reality.

## When this is done

- All version strings consistent across files
- All commit SHAs in docs verified against `git log`
- `python -m xrpl_agent_id --version` matches expected
- Dashboard `/api/version` JSON matches expected
- Test count in docs matches `pytest tests/` output from project root
- CHANGELOG file:line references resolve
