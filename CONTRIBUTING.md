# Contributing to xrpl_agent_id

Thanks for your interest. This is a small library and contributions are welcome —
including bug reports, which are the most valuable kind.

## Where things live

| Path | Purpose |
|---|---|
| `xrpl_agent_id/` | the library (13 modules + `dashboard/`) |
| `tests/` | 24 test modules, 360 offline tests |
| `docs/` | architecture, changelog, internal session notes |
| `scripts/check_no_seeds.py` | pre-commit seed scanner — **never disable it** |

## Getting set up

```bash
git clone https://github.com/DuNorth595/xrpl_agent_id
cd xrpl_agent_id
pip install -e ".[dev]"
pre-commit install
```

## Before you open a pull request

```bash
pytest tests/ -q          # must be 360 passed, 6 skipped (the 6 are live-testnet, opt-in)
reuse lint                # must pass — this is enforced in CI
pre-commit run --all-files
```

CI runs a Python matrix (3.10–3.13) plus a `reuse lint` gate. Both must be green.

## House rules

- **Never commit a seed or private key.** `scripts/check_no_seeds.py` blocks
  classic (`s...`) and hex seeds at commit time. If it fires on something benign,
  add a justified entry to `LEGACY_ALLOWLIST` in that script — do not `--no-verify`.
- **Don't widen the public API silently.** `__all__` is pinned by
  `tests/test_public_api.py` and documented in `xrpl_agent_id/PUBLIC_API.md`.
  If you add a name, update both, and say so in the PR description.
- **Documentation claims are tested by humans.** Every number, signature, and file
  path in the README is checked against the code before release. If you change an
  API, update the README in the same commit — the two drifting apart is the bug we
  are trying to avoid.
- **Version bumps go through the release process**, not a hand edit of
  `pyproject.toml` alone. See the "Releases" section below.

## Adding an SPDX header

New files need copyright + license information. Easiest way:

```bash
reuse annotate --copyright "2026 Justin Douglas" --license MIT <file>
```

`.reuse/dep5` covers the tree as a fallback, so an unannotated file still lints —
but prefer explicit headers on source files.

## Releases

Version metadata lives in four places and must agree:

1. `pyproject.toml` — `version = "x.y.z"`
2. `xrpl_agent_id/__init__.py` — `__version__`
3. `README.md` — cover page, install examples, release table
4. `xrpl_agent_id/PUBLIC_API.md`

`tests/test_dashboard_version_routes.py` asserts the dashboard reports the same
version, so CI will catch a partial bump.

## Security issues

Do **not** open a public issue. See [SECURITY.md](SECURITY.md) for the private
reporting path.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).