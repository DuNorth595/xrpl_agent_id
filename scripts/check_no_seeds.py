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
    # Hardcoded classic seed used as a TEST FIXTURE for error-path testing
    # in submit_one(). The string itself is well-known XRPL test material
    # (sEd... format) and the test mocks submit_and_wait() so this seed
    # never signs anything real. Verified against xrpl-py that this is
    # not a wallet derived from any real ledger account.
    "tests/test_stress_harness_throughput.py":
        "test fixture for error-path handling; never signs real txs",
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
LEGACY_ALLOWLIST = {
    # Format: (relative_path, line_number) -> reason
    ("docs/01_issuance_flow.md", 63): "Testnet explorer URL, tx hash",
    ("docs/01_issuance_flow.md", 84): "Testnet explorer URL, tx hash",
    ("docs/01_issuance_flow.md", 112): "Testnet explorer URL, tx hash",
    ("docs/CONTROLLER_BANNED_MULTISIG.md", 88): "table cell with SignerList ID",
    ("docs/CONTROLLER_BANNED_MULTISIG.md", 89): "table cell with SignerList ID",
    ("docs/STRESS_TEST_v0.3.0.md", 170): "JSON-RPC request body in shell example",
    ("docs/STRESS_TEST_v0.3.0.md", 226): "table cell with reason code + decision_id",
    ("docs/STRESS_TEST_v0.3.0.md", 227): "table cell with reason code + decision_id",
    ("docs/STRESS_TEST_v0.3.0.md", 228): "table cell with reason code + decision_id",
    ("docs/STRESS_TEST_v0.3.0.md", 229): "table cell with reason code + decision_id",
    ("docs/STRESS_TEST_v0.3.0.md", 230): "table cell with reason code + decision_id",
    ("docs/STRESS_TEST_v0.3.0.md", 246): "inline code block with tx hash",
}


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
    """Return every tracked file in the working tree."""
    out = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, check=True,
    )
    root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip())
    return [root / p for p in out.stdout.splitlines() if p]


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
    ap = argparse.ArgumentParser(description="Block commits containing XRPL seeds.")
    ap.add_argument("--all", action="store_true",
                    help="scan every tracked file (default: staged only)")
    args = ap.parse_args()

    if args.all:
        paths = _all_tracked_paths()
        scope = "all tracked files"
    else:
        paths = _staged_paths()
        scope = "staged files"

    root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip())

    total_hits = 0
    legacy_hits = 0
    for p in paths:
        rel = p.relative_to(root)
        if str(rel) in ALLOWLIST:
            continue
        hits = _scan_file(p)
        for lineno, line, kind in hits:
            if (str(rel), lineno) in LEGACY_ALLOWLIST:
                legacy_hits += 1
                continue
            # Truncate the line so we don't print the secret back to stdout.
            preview = line[:60] + ("..." if len(line) > 60 else "")
            print(f"{rel}:{lineno}: [{kind}] {preview}", file=sys.stderr)
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
