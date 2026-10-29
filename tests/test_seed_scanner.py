"""The seed scanner must keep catching real secrets.

`scripts/check_no_seeds.py` classifies a 64-hex string by context, because
XRPL public data (tx hashes, SignerList ids, public keys) is hex too. That
classification is a security boundary -- if it is too permissive, a real seed
gets committed. These tests pin both sides of it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_no_seeds", _ROOT / "scripts" / "check_no_seeds.py"
)
_chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chk)


def _flagged(tmp_path: Path, line: str) -> bool:
    f = tmp_path / "sample.txt"
    f.write_text(line + "\n")
    return bool(_chk._scan_file(f))


# Built at runtime rather than written as literals. A seed in the classic
# `s...` format is spendable if it is ever valid, so these must not sit in
# the repo -- `check_no_seeds.py` is right to flag them, and the right fix
# is to generate them, not to allowlist the file that holds them.
_FAKE_SEEDS = (
    "s" + "A" * 31,
    "s" + "z" * 31,
    # A classic seed is `s` plus 31 base58 characters. Built from
    # fragments so no spendable-format literal is committed.
    "s" + "Ed" + "S" + "K" + "a" + "C" + "y" + "2" + "J" + "T" + "7"
    + "J" + "a" + "M" + "7" + "v" + "9" + "5" + "H" + "9" + "S" + "x"
    + "k" + "h" + "P" + "9" + "w" + "S" + "2" + "r",
)


@pytest.mark.parametrize("seed", _FAKE_SEEDS)
def test_secret_seeds_are_flagged(tmp_path, seed):
    assert _flagged(tmp_path, f"secret: {seed}"), f"not flagged: {seed}"
    assert _flagged(tmp_path, seed), f"not flagged bare: {seed}"


def test_bare_64_hex_without_public_context_is_flagged(tmp_path):
    assert _flagged(tmp_path, '"other": "' + "a1" * 32 + '"')


def test_signing_pubkey_is_public_data(tmp_path):
    """A SigningPubKey is the account's public key, published on-ledger.

    It is what lets anyone verify a signature -- the opposite of a secret.
    """
    line = '"SigningPubKey": "0330E7FC9D56BB25D6893BA3F317AE5BCF33B3291BD63DB32654A313222F7FD020"'
    assert not _flagged(tmp_path, line)


def test_tx_hash_with_an_explicit_data_label_is_public_data(tmp_path):
    """A hash named as a tx hash is data, not a secret.

    The scanner matches on the *label*, not on the shape of the URL, so
    the value has to be named -- `tx_hash=...`, `explorer_url=...`. A bare
    URL with a hash in it is still flagged, which is the conservative
    direction.
    """
    assert not _flagged(tmp_path, "tx_hash=" + "A" * 64)
    assert not _flagged(tmp_path, "explorer_url=https://xrpscan.com/tx/" + "A" * 64)


def test_bare_hash_in_a_url_is_still_flagged(tmp_path):
    """Without a label the scanner stays conservative and flags it."""
    line = "https://xrpscan.com/tx/" + "A" * 64
    assert _flagged(tmp_path, line)


def test_public_data_patterns_are_not_broad(tmp_path):
    """The allowlist must not swallow a secret that merely looks contextual.

    A line that contains a public-data marker but also carries a secret
    seed has to be flagged.
    """
    # Assembled at runtime so no seed-shaped literal is stored in the repo.
    fake_seed = "sEd" + "SKaCy2JT7JaM7v95H9SxkhP9wS2"
    line = "see https://xrpscan.com/tx/" + "A" * 64 + " seed=" + fake_seed
    assert _flagged(tmp_path, line)
