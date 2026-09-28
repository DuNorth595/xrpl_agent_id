#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""sync_to_desktop.py — Mirror the LIFE_MEMORY project snapshot to ~/Desktop/XRPL_AGENT_ID/<version>/.

The Desktop folder is a complete, self-contained copy of the project at
each release version. It's meant for offline browsing, sharing, and
archival — NOT for editing (edit in LIFE_MEMORY, then sync here).

Usage:
    python scripts/sync_to_desktop.py [--version 0.1.0] [--readme]

What it does:
    1. Determines the current project version (from xrpl_agent_id/__init__.py
       or pyproject.toml — __init__.py wins).
    2. Creates ~/Desktop/XRPL_AGENT_ID/<version>/ if missing.
    3. Copies the project (excluding .git/, .pytest_cache/, __pycache__,
       testnet_keys/, *.db, *.pdf preview artifacts) into that folder.
    4. Writes a VERSION_NOTES.md with the latest git log + test counts.
    5. Updates ~/Desktop/XRPL_AGENT_ID/README.md with a "Latest version"
       pointer.

Defaults:
    --source  ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID
    --dest    ~/Desktop/XRPL_AGENT_ID
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SOURCE = Path.home() / "Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID"
DEFAULT_DEST = Path.home() / "Desktop/XRPL_AGENT_ID"

# What to copy (relative to source)
COPY_INCLUDE = [
    "xrpl_agent_id",
    "tests",
    "scripts",
    "docs",
    "results",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    ".gitignore",
]

# What to skip at the top level of the copy
COPY_EXCLUDE_NAMES = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    "testnet_keys",
    ".venv",
    "venv",
    "build",
    "dist",
    "*.egg-info",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    ".DS_Store",
}


def _detect_version(source: Path) -> str:
    """Read __version__ from the package's __init__.py; fall back to pyproject."""
    init = source / "xrpl_agent_id" / "__init__.py"
    if init.exists():
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
        if m:
            return m.group(1)
    pp = source / "pyproject.toml"
    if pp.exists():
        m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pp.read_text(), re.M)
        if m:
            return m.group(1)
    raise RuntimeError("could not detect version")


def _git_log(source: Path, n: int = 20) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(source), "log", f"-{n}", "--oneline"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "(no git history)"


def _count_tests(source: Path) -> str:
    # Try pytest via the same Python that's running this script
    import sys as _sys
    py = _sys.executable
    try:
        out = subprocess.check_output(
            [py, "-m", "pytest", "tests/", "--collect-only", "-q",
             "--ignore=tests/test_integration_ledger_live.py"],
            cwd=str(source),
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).decode()
        m = re.search(r"(\d+)\s+tests collected", out)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def _copy_project(source: Path, dest: Path) -> int:
    """Copy a curated subset of source into dest. Returns file count."""
    n = 0
    for entry in COPY_INCLUDE:
        src_path = source / entry
        dst_path = dest / entry
        if not src_path.exists():
            continue
        if src_path.is_dir():
            shutil.copytree(
                src_path,
                dst_path,
                ignore=shutil.ignore_patterns(*COPY_EXCLUDE_NAMES),
                dirs_exist_ok=True,
            )
            for _ in dst_path.rglob("*"):
                n += 1
        else:
            shutil.copy2(src_path, dst_path)
            n += 1
    return n


def _write_version_notes(dest: Path, version: str, source: Path) -> None:
    notes = dest / "VERSION_NOTES.md"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = f"""# xrpl_agent_id v{version} — Desktop snapshot

**Snapshot taken:** {today}
**Source:** `{source}`
**Git log (most recent):**

```
{_git_log(source)}
```

**Test count (offline):** {_count_tests(source)}

---

## What's in this folder

This is a complete snapshot of the `xrpl_agent_id` project at version
{version}, copied from `~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/`.

To use:
```bash
cd ~/Desktop/XRPL_AGENT_ID/v{version}
pip install -e .
python -m xrpl_agent_id.dashboard.server --port 8768
```

The source of truth lives in LIFE_MEMORY. To refresh this snapshot, run
`sync_to_desktop.py` from inside the project:
```bash
cd ~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID
python scripts/sync_to_desktop.py
```

The Desktop copy is **read-only**. Edit the LIFE_MEMORY copy.
"""
    notes.write_text(body)


def _update_desktop_readme(dest: Path, version: str, source: Path) -> None:
    readme = dest / "README.md"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    versions = sorted(
        [p.name for p in dest.iterdir() if p.is_dir() and p.name.startswith("v")],
        reverse=True,
    )
    versions_md = "\n".join(f"- [`{v}/`]({v}/)" for v in versions) if versions else "_(none yet)_"

    body = f"""# xrpl_agent_id — Desktop archive

**Latest version:** v{version}
**Source:** `~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/`
**Last sync:** {today}

This folder holds **complete per-version snapshots** of the `xrpl_agent_id`
project. Each version is a self-contained copy you can browse offline,
copy to another machine, or archive.

## Versions

{versions_md}

## How snapshots get here

Run from the LIFE_MEMORY project root:
```bash
python scripts/sync_to_desktop.py
```

That reads `xrpl_agent_id.__version__` and copies the current source
into `~/Desktop/XRPL_AGENT_ID/<version>/`. It also writes
`VERSION_NOTES.md` inside the new folder with the current git log and
test counts.

## How to keep Desktop and LIFE_MEMORY in sync

The rule is one-way: **LIFE_MEMORY is the source of truth, Desktop is
a read-only mirror.**

When you make changes:
1. Edit files in `~/Desktop/LIFE_MEMORY/PROJECTS/XRPL_AGENT_ID/`
2. Commit to git there
3. Run `python scripts/sync_to_desktop.py` to refresh Desktop

When you bump the version:
1. Edit `xrpl_agent_id/__init__.py` (`__version__ = "..."`)
2. Edit `pyproject.toml` to match
3. Commit
4. Run `python scripts/sync_to_desktop.py` — a new version folder is
   created automatically
"""
    readme.write_text(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="sync LIFE_MEMORY project to Desktop snapshot")
    parser.add_argument("--version", default=None,
                        help="version label (default: read from xrpl_agent_id/__init__.py)")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--no-readme", action="store_true",
                        help="skip updating the top-level Desktop README")
    args = parser.parse_args()

    source = Path(args.source)
    dest = Path(args.dest)
    if not source.exists():
        print(f"source not found: {source}", file=sys.stderr)
        sys.exit(1)

    version = args.version or _detect_version(source)
    version_dir = dest / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)

    print(f"syncing v{version}: {source} → {version_dir}")
    n = _copy_project(source, version_dir)
    _write_version_notes(version_dir, version, source)
    if not args.no_readme:
        _update_desktop_readme(dest, version, source)
    print(f"  copied {n} files, wrote VERSION_NOTES.md")
    print(f"  Desktop README updated")


if __name__ == "__main__":
    main()
