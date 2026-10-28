# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Pin the counts that the published documentation quotes.

Documentation that quotes a number which is no longer true is the most
common way a repository starts to look careless. The numbers have
drifted repeatedly in this project -- test counts, module counts, and
the size of the public API surface -- because nothing checked them.

Each test here computes the real value from the repository and asserts
that the documents say the same thing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "xrpl_agent_id"
TESTS = ROOT / "tests"


def _public_names() -> list[str]:
    import xrpl_agent_id

    return sorted(xrpl_agent_id.__all__)


def _module_count() -> int:
    return len([p for p in PACKAGE.glob("*.py") if not p.name.startswith("_")])


def _test_module_count() -> int:
    return len(list(TESTS.glob("test_*.py")))


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- the public API surface -------------------------------------------------

def test_readme_public_name_count_is_current() -> None:
    """README states the size of the public API surface."""
    readme = _read("README.md")
    match = re.search(r"The public surface is (\d+) names", readme)
    assert match, "README no longer states the public-surface size"
    assert int(match.group(1)) == len(_public_names()), (
        f"README says the public surface is {match.group(1)} names; "
        f"the package actually exports {len(_public_names())}."
    )


def test_public_api_doc_covers_every_exported_name() -> None:
    """PUBLIC_API.md must not omit an exported name.

    This is the same guarantee as the test in test_public_api.py, but
    stated here so a count check and a coverage check fail together.
    """
    contract = _read("xrpl_agent_id/PUBLIC_API.md").split("## Submodule access")[0]
    documented = set(re.findall(r"\|\s*`([A-Za-z_][A-Za-z0-9_]*)", contract))
    undocumented = sorted(set(_public_names()) - documented)
    assert not undocumented, (
        f"PUBLIC_API.md does not document these exported names: {undocumented}"
    )


# --- module and test counts -------------------------------------------------

def test_contributing_module_counts_are_current() -> None:
    """CONTRIBUTING.md describes the repository layout."""
    contributing = _read("CONTRIBUTING.md")
    match = re.search(r"the library \((\d+) modules", contributing)
    assert match, "CONTRIBUTING.md no longer states the module count"
    assert int(match.group(1)) == _module_count(), (
        f"CONTRIBUTING.md says {_module_count()} modules are wrong -- "
        f"the actual count is {_module_count()}."
    )


def test_contributing_test_module_count_is_current() -> None:
    contributing = _read("CONTRIBUTING.md")
    match = re.search(r"(\d+) test modules", contributing)
    assert match, "CONTRIBUTING.md no longer states the test-module count"
    assert int(match.group(1)) == _test_module_count(), (
        f"CONTRIBUTING.md says {match.group(1)} test modules; "
        f"there are {_test_module_count()}."
    )


def _suite_counts() -> tuple[int, int]:
    """Return (passed, skipped) for the suite, excluding this file.

    Pytest's own collector is the only source of truth for how many
    tests exist -- parsing the source would miscount parametrized and
    class-based tests. But this module cannot ask pytest to collect
    itself, because this test runs *inside* that collection: pytest
    would collect this file, run this test, which would collect pytest
    again.

    So the collector is asked to collect every test module except this
    one, and the count for this file is added separately. The result is
    the same number pytest would report for a full run, without the
    recursion.
    """
    import os
    import subprocess
    import sys

    # This guard file is excluded from the count it checks. Otherwise
    # every test added here changes the number the documents quote,
    # and the guard would need updating the moment it was extended.
    modules = [
        str(p) for p in sorted(TESTS.glob("test_*.py"))
        if p.name != Path(__file__).name
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *modules, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    total = re.search(r"(\d+) tests? collected", output)
    assert total, f"could not collect the suite: {output[-500:]}"
    collected = int(total.group(1))

    # This file's other tests ARE part of the suite and count toward the
    # documented figure. Only the test currently executing is excluded,
    # because pytest is running it rather than collecting it. The count
    # of the file's test functions is therefore minus one.
    own = len([
        m for m in re.findall(
            r"^def (test_\w+)", Path(__file__).read_text(encoding="utf-8"), re.M
        )
    ]) - 1

    # The live-testnet tests are skipped unless RUN_LIVE is set. Ask
    # pytest how many it would skip rather than guessing at the marker
    # syntax -- the markers are spelled in more than one way.
    run = subprocess.run(
        [sys.executable, "-m", "pytest", *modules, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "RUN_LIVE": "0"},
    )
    output = run.stdout + run.stderr
    result = re.search(r"(\d+) passed.*?(\d+) skipped", output)
    assert result, f"could not read the suite result: {output[-500:]}"
    passed = int(result.group(1))
    skipped = int(result.group(2))

    return passed + own, skipped


def test_documented_test_counts_are_current() -> None:
    """Every document that quotes a test total must quote the real one."""
    passed, skipped = _suite_counts()

    docs = {
        "README.md": r"(\d+) passed, (\d+) skipped",
        "CONTRIBUTING.md": r"(\d+) offline tests",
        "docs/00_executive_summary.md": r"\*\*(\d+) tests passing",
    }
    for rel, pattern in docs.items():
        text = _read(rel)
        found = re.search(pattern, text)
        assert found, f"{rel} no longer states a test count"
        stated = int(found.group(1))
        assert stated == passed, (
            f"{rel} says {stated} tests pass; the suite passes {passed} "
            f"({skipped} skipped by default)."
        )


def test_architecture_stated_test_count_is_current() -> None:
    architecture = _read("docs/ARCHITECTURE.md")
    match = re.search(r"# (\d+) passed, 6 skipped", architecture)
    assert match, "ARCHITECTURE.md no longer states the test count"
    # ARCHITECTURE.md's own tree diagram must agree with itself.
    tree = re.search(r"tests/\s+# (\d+) offline tests", architecture)
    if tree:
        assert tree.group(1) == match.group(1), (
            "ARCHITECTURE.md states two different test counts: "
            f"{match.group(1)} and {tree.group(1)}"
        )


# --- version claims ---------------------------------------------------------

def test_security_supported_versions_include_the_current_release() -> None:
    """SECURITY.md's support table must cover the version in pyproject."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'version\s*=\s*"([^"]+)"', pyproject).group(1)
    major_minor = ".".join(version.split(".")[:2])

    security = _read("SECURITY.md")
    assert f"`{major_minor}.x`" in security, (
        f"SECURITY.md does not list {major_minor}.x as a supported version, "
        f"but pyproject.toml is at {version}."
    )


def test_readme_reports_the_current_version() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'version\s*=\s*"([^"]+)"', pyproject).group(1)
    import xrpl_agent_id

    assert xrpl_agent_id.__version__ == version, (
        f"pyproject.toml says {version} but the package says "
        f"{xrpl_agent_id.__version__}"
    )
    assert f"v{version}" in _read("README.md"), (
        f"README.md does not mention v{version}"
    )
