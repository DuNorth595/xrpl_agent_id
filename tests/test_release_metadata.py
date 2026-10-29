# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""The published version and the tag must agree.

A release whose tag says one version and whose package reports another is
a release nobody can install correctly, and the mismatch is invisible
until someone checks the installed metadata against the source.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# tomllib is 3.11+. The CI matrix includes 3.10, so read the values we need
# with a regex rather than adding a dependency to parse a TOML file.
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
INIT = (ROOT / "xrpl_agent_id" / "__init__.py").read_text(encoding="utf-8")


def _pyproject_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, re.M)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def _module_version() -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', INIT, re.M)
    assert match, "__init__.py has no __version__"
    return match.group(1)


def test_pyproject_and_module_versions_agree():
    assert _pyproject_version() == _module_version(), (
        f"pyproject says {_pyproject_version()} but the module says "
        f"{_module_version()}"
    )


def test_the_tag_matches_the_package_version():
    """The released tag and the version the package reports must be the same.

    This is the check that was missing when v0.5.3 was tagged against a
    package still declaring 0.5.2.
    """
    import subprocess

    version = _module_version()
    tag = f"v{version}"

    # The tag must exist upstream and point at a commit that carries this
    # version. If the tag is missing, the release has not happened yet --
    # which is not a failure of this codebase, so it is skipped.
    exists = subprocess.run(
        ["git", "tag", "--list", tag],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if not exists.stdout.strip():
        import pytest

        pytest.skip(f"{tag} is not tagged yet")

    tagged = subprocess.run(
        ["git", "show", f"{tag}:xrpl_agent_id/__init__.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if tagged.returncode != 0:
        import pytest

        pytest.skip(f"{tag} is not available locally")

    match = re.search(r'^__version__\s*=\s*"([^"]+)"', tagged.stdout, re.M)
    assert match, f"{tag} has no __version__"
    assert match.group(1) == version, (
        f"the {tag} tag declares {match.group(1)} but the current source "
        f"declares {version}"
    )


def test_the_changelog_has_an_entry_for_this_version():
    version = _module_version()
    changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{version}]" in changelog, f"CHANGELOG has no entry for {version}"


def test_dependencies_are_pinned_to_a_tag():
    """A git dependency on a moving branch can change under an install."""
    for dep in re.findall(r'"([^"]*git\+[^"]*)"', PYPROJECT):
        assert "@v" in dep, f"git dependency is not pinned to a tag: {dep}"
