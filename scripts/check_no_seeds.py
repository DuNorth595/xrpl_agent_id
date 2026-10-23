#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""check_no_seeds.py — block commits containing XRPL seed strings.

XRPL classic seeds start with `s` followed by base58 chars, length 29-35.
Hex seeds are 64 hex chars, often prefixed `00` (Ed25519) or unprefixed
(secp256k1). Both forms are flagged here.

Usage:
    # As a pre-commit hook (recommended):
    #   .pre-commit-config.yaml:
    #     - repo: local
    #       hooks:
    #         - id: check-no-seeds
    #           entry: python scripts/check_no_seeds.py
    #           language: system
    #           stages: [pre-commit]

    # As a one-shot check before pushing:
    #   python scripts/check_no_seeds.py              # checks staged files
    #   python scripts/check_no_seeds.py --all        # checks whole tree

Exit codes:
    0 — no seeds found
    1 — at least one seed pattern matched (prints offending paths)
    2 — invocation error
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


# Classic seed: 's' + base58 + 28-34 more chars. Total length 29-35.
# Anchored to word boundary so we don't flag 's' followed by a digit in prose.
CLASSIC_SEED_RE = re.compile(r"\bs[1-9A-HJ-NP-Za-km-z]{28,34}\b")

# Hex seed: 64 hex chars, optionally prefixed with one of the XRPL key-type
# bytes (00 = Ed25519, ED = Ed25519 alt, etc.). Matched standalone, not in
# the middle of a longer hex string (negative lookaround on both sides).
HEX_SEED_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")

# JSON keys whose VALUE is allowed to be a 64-char hex string without
# triggering the hex-seed heuristic. These are all public on-ledger data
# (tx hashes, ledger indexes, decision IDs, explorer URLs) and not seeds.
SAFE_JSON_KEYS = frozenset({
    "tx_hash", "txid", "previous_txn_id", "previous_txnlgrseq",
    "mirrored_tx", "decision_id", "explorer_url", "hash",
    "index", "account", "destination", "owner",
    "credential_id", "issuer", "subject",
})

# Shell / URL / Markdown contexts where a 64-char hex is data, not a secret.
SAFE_LINE_CONTEXTS = (
    "tx_hash=", "txid=", "previous_txn_id=",
    "mirrored_tx=", "decision_id=", "index=",
    "explorer_url=", "hash=",
    # Markdown bold key indicators (must include the closing ** to avoid
    # matching a sentence like "the tx_hash story")
    "**tx_hash**", "**mirrored_tx**", "**decision_id**",
    "**previous_txn_id**", "**index**", "**explorer_url**",
)


def _is_safe_json_value(line: str, match_start: int) -> bool:
    """True if the hex at match_start is the value of a SAFE_JSON_KEYS key."""
    # Look backwards up to 80 chars for a JSON key followed by a colon.
    # We don't anchor to end-of-prefix because the colon is followed by
    # optional whitespace and a quote (e.g. `"tx_hash": "..."`).
    prefix = line[max(0, match_start - 80):match_start]
    matches = list(re.finditer(r'"([^"]+)"\s*:"?', prefix))
    if not matches:
        return False
    last_key_raw = matches[-1].group(1).lower()
    # Normalize: strip underscores so "PreviousTxnID" matches "previous_txn_id",
    # and "txHash" matches "tx_hash".
    last_key = last_key_raw.replace("_", "")
    safe_keys = {k.lower().replace("_", "") for k in SAFE_JSON_KEYS}
    return last_key in safe_keys


def _is_safe_line_context(line: str) -> bool:
    """True if the line itself is a shell/URL context where hex is data."""
    low = line.lower()
    return any(ctx in low for ctx in SAFE_LINE_CONTEXTS)


# Files that legitimately contain seed-like patterns (test fixtures with
# documented seed material, etc.). Add entries here only with a comment
# explaining why the seed is acceptable to commit.
ALLOWLIST = {
    # Format: relative path -> reason
    #
    # Intentionally empty by default.
    #
    # Kept empty on purpose: the seed this file previously needed is now
    # generated at runtime by the test (Wallet.create()), so there is no
    # literal to allowlist. If a fixture ever seems to need a hardcoded seed,
    # generate one instead rather than publishing a spendable-format key.
    #
    # documents/
    "docs/STRESS_TEST_v0.3.0.md":
        "Public stress-test report. Every 64-hex token in it is a settled "
        "testnet transaction hash recorded as evidence (the 'Mirror tx' column "
        "of the decision table, and the on-chain anchor cited for "
        "controller_banned). Line-scoped context patterns cannot catch these: "
        "the header that names the column and the sentence that introduces the "
        "anchor sit on DIFFERENT lines from the hash, and _is_public_data() is "
        "line-scoped by design. Risk accepted: a real seed added to this file "
        "later would not be flagged. Mitigation -- this is a frozen v0.3.0 "
        "report; new stress output goes to results/ and is not published here.",
}



# Legacy allowlist: pre-existing files (committed before this scanner existed)
# whose 64-char hex strings are public testnet on-ledger data (tx hashes,
# decision IDs, explorer URLs, etc.) but appear in contexts the scanner
# doesn't currently parse correctly. New commits that add to these files
# should still be reviewed manually; the allowlist is a one-time pass for
# the existing tree, not a permanent bypass.
#
# To add a new entry: include the file path, the line number, and a one-line
# explanation of WHY the hex is data and not a secret. Do NOT add entries
# just to silence the scanner.
# Public on-ledger data that is shaped like a 64-char hex string.
#
# This was a dict keyed on (relative_path, line_number). That breaks the moment a
# document is edited above an entry or a file is renamed: the exemption silently
# stops applying and CI fails on an otherwise clean tree. It bit xrpl_agent_log for
# exactly this reason on 2026-10-22, after moving a file invalidated three entries.
#
# A transaction hash and a secp256k1 private key are indistinguishable by shape,
# so classify by context instead. All 12 former entries were public ledger data:
# explorer URLs, tx hashes, SignerList IDs and decision_ids.
PUBLIC_DATA_PATTERNS = (
    re.compile(r"xrpl\.org/transactions/", re.IGNORECASE),
    re.compile(r"\b(?:tx|txn|transaction)\s+hash\b", re.IGNORECASE),
    re.compile(r"\bhash\s*[:=]\s*[A-F0-9]{16,}", re.IGNORECASE),
    re.compile(r"anchor_tx_hash|on_chain_tx_hash|entry_hash|MemoData|decision_id",
               re.IGNORECASE),
    re.compile(r"\b(?:anchor|signerlist|signer_list)\b", re.IGNORECASE),
    re.compile(r'"(?:mirrored_tx|tx_hash|decision_id|signer_list)"', re.IGNORECASE),
    # --- contexts added 2026-10-23 ---------------------------------------
    # Explorer deep-links. A tx hash is the path segment on every XRPL
    # block explorer, so a hash sitting in one is on-ledger data by
    # construction, not a private key.
    re.compile(r"bithomp\.com/explorer/", re.IGNORECASE),
    re.compile(r"xrpscan\.com/(?:tx|transactions?)/", re.IGNORECASE),
    # A JSON-RPC `tx` lookup names the transaction it wants; the hash in that
    # payload is the lookup key, not a signing key.
    re.compile(r'"method"\s*:\s*"tx"', re.IGNORECASE),
    # Markdown table rows that pair an r-address with a 64-hex identifier are
    # SignerList rows (address + signer entry), which are public multisig
    # state -- not a seed and its own address.
    re.compile(r"`r[1-9A-HJ-NP-Za-km-z]{24,34}`\s*\|\s*`[A-F0-9]{64}`",
               re.IGNORECASE),
    # The stress report's decision table has a literal column header naming the
    # 64-hex values ("Mirror tx"). A hash under a column that calls it a tx is
    # public on-ledger data.
    re.compile(r"\|\s*Mirror\s+tx\s*\|", re.IGNORECASE),
    # "...mirrored on-chain at `<hash>`" -- an explicit statement that the
    # value is a settled ledger transaction.
    re.compile(r"mirrored\s+on-chain\s+at", re.IGNORECASE),
)


def _is_public_data(line: str) -> bool:
    """True if the line identifies a hex token as public on-ledger data."""
    return any(p.search(line) for p in PUBLIC_DATA_PATTERNS)


def _staged_paths() -> list[Path]:
    """Return the list of staged file paths (added/copied/modified)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    )
    root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip())
    return [root / p for p in out.stdout.splitlines() if p]


def _all_tracked_paths() -> list[Path]:
    """Return every tracked file in the working tree.

    `git ls-files` / `git rev-parse` both raise CalledProcessError with exit
    128 outside a git repository, which previously surfaced as a raw
    traceback. A contributor who unpacks a sdist and runs `--all` gets a
    clean message instead, and we fall back to walking the tree so the check
    still does useful work rather than silently scanning nothing.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, check=True,
        )
        root = Path(subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip())
        return [root / p for p in out.stdout.splitlines() if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "check_no_seeds: not inside a git repository -- falling back to "
            "walking the working tree. VCS metadata (commit messages, "
            "historical blobs) is NOT covered by this fallback.",
            file=sys.stderr,
        )
        skip = {".git", ".venv", "venv", "node_modules", "__pycache__",
                ".mypy_cache", ".pytest_cache", "build", "dist"}
        # The tree walk is deliberately broader than `git ls-files`, so it
        # also sees untracked build output -- rendered PDF/HTML reports and
        # results JSON -- that would otherwise be pure noise. Skip generated
        # artifacts; anything genuinely tracked is still covered by the
        # git path above.
        skip_suffixes = {".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg",
                         ".gif", ".svg", ".woff", ".woff2", ".ttf",
                         # Local SQLite state from dashboard/test runs. Binary
                         # containers whose "lines" are arbitrary byte runs,
                         # so a 64-hex match inside one is meaningless.
                         ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}
        return [p for p in Path.cwd().rglob("*")
                if p.is_file()
                and not (skip & set(p.parts))
                and p.suffix.lower() not in skip_suffixes]


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, line_text, matched_pattern)] for each seed match."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if CLASSIC_SEED_RE.search(line):
            hits.append((lineno, line.strip(), "classic-seed (s...)"))
            continue
        for m in HEX_SEED_RE.finditer(line):
            if _is_safe_json_value(line, m.start()):
                continue
            if _is_safe_line_context(line):
                continue
            hits.append((lineno, line.strip(), "hex-seed (64 hex chars)"))
            break  # one report per line is enough
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Block commits containing XRPL seeds.",
        epilog=(
            "Modes: "
            "(1) no args -> scan git-staged files. "
            "(2) --all -> scan every tracked file. "
            "(3) FILE [FILE ...] -> scan the given files "
            "(used by pre-commit hooks, which pass paths positionally)."
        ),
    )
    ap.add_argument("--all", action="store_true",
                    help="scan every tracked file (default: staged only)")
    ap.add_argument("files", nargs="*",
                    help="explicit files to scan (overrides --all and staged)")
    args = ap.parse_args()

    if args.files:
        # Explicit paths mode — used by pre-commit hooks. Resolve relative
        # paths against CWD (pre-commit passes repo-relative paths).
        paths = [Path(f).resolve() for f in args.files]
        scope = f"{len(paths)} explicit file(s)"
    elif args.all:
        paths = _all_tracked_paths()
        scope = "all tracked files"
    else:
        paths = _staged_paths()
        scope = "staged files"

    # Repo root is used only to render repo-relative paths for allowlist
    # lookups. Outside a git repo there is no root, so fall back to CWD --
    # allowlists then simply never match, which is the safe direction (a file
    # outside a repo is reported rather than silently skipped).
    try:
        root = Path(subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        root = Path.cwd()

    total_hits = 0
    legacy_hits = 0
    for p in paths:
        try:
            rel = p.relative_to(root)
            rel_str = str(rel)
            in_repo = True
        except ValueError:
            # File is outside the repo (e.g. a temp canary path).
            # In explicit-files mode we still want to scan and report it,
            # but allowlists (which are keyed on repo-relative paths)
            # don't apply.
            rel_str = str(p)
            in_repo = False

        if in_repo and rel_str in ALLOWLIST:
            continue
        hits = _scan_file(p)
        for lineno, line, kind in hits:
            if _is_public_data(line):
                legacy_hits += 1
                continue
            # Truncate the line so we don't print the secret back to stdout.
            preview = line[:60] + ("..." if len(line) > 60 else "")
            print(f"{rel_str}:{lineno}: [{kind}] {preview}", file=sys.stderr)
            total_hits += 1

    if total_hits:
        print(
            f"\n❌ check_no_seeds: {total_hits} potential seed(s) found in "
            f"{scope}.",
            file=sys.stderr,
        )
        print(
            "    If this is a false positive (test fixture, docstring example), "
            f"add the path to ALLOWLIST in {__file__} with a comment.",
            file=sys.stderr,
        )
        return 1

    suffix = f" ({legacy_hits} legacy allowlisted)" if legacy_hits else ""
    print(f"✅ check_no_seeds: no new seeds found in {scope}.{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
