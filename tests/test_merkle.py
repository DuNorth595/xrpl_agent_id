# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""Tests for the Merkle batch-anchor tree.

The tree is the thing that makes on-chain auditing cheap: one
transaction per *batch* instead of per decision. That only works if an
auditor can independently verify that a given decision was in the
anchored batch. These tests prove that, and prove the tree rejects
tampering.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from xrpl_agent_id.merkle import (
    EMPTY_ROOT,
    leaf_hash,
    merkle_proof,
    merkle_root,
    verify_inclusion,
)


def _ids(n: int) -> list[str]:
    """n distinct decision ids, in the same shape the audit log uses."""
    return [
        hashlib.sha256(f"decision-{i}".encode()).hexdigest().upper()
        for i in range(n)
    ]


# -----------------------------------------------------------------------
# Root computation
# -----------------------------------------------------------------------


def test_empty_batch_root() -> None:
    """An empty batch has a distinct, well-defined root."""
    assert merkle_root([]) == EMPTY_ROOT
    # It must not collide with any single-leaf root.
    assert merkle_root([]) != merkle_root(_ids(1))


def test_single_leaf_root_is_the_leaf() -> None:
    """A one-leaf batch needs no tree -- its root is the leaf."""
    ids = _ids(1)
    assert merkle_root(ids) == leaf_hash(ids[0]).hex().upper()


def test_root_is_order_dependent() -> None:
    """The root commits to the batch order."""
    ids = _ids(4)
    assert merkle_root(ids) != merkle_root(list(reversed(ids)))


def test_root_is_deterministic() -> None:
    """Same batch -> same root, every time."""
    ids = _ids(16)
    assert merkle_root(ids) == merkle_root(ids)


def test_root_changes_when_a_leaf_changes() -> None:
    """Tampering with any leaf changes the root."""
    ids = _ids(8)
    original = merkle_root(ids)
    ids[3] = hashlib.sha256(b"tampered").hexdigest().upper()
    assert merkle_root(ids) != original


def test_root_is_64_hex_chars() -> None:
    """The root fits in an XRPL memo (256 bytes) with room to spare."""
    root = merkle_root(_ids(1000))
    assert len(root) == 64
    int(root, 16)  # valid hex


# -----------------------------------------------------------------------
# Inclusion proofs -- the whole point
# -----------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 100, 1000])
def test_every_leaf_verifies(size: int) -> None:
    """Every leaf in a batch of any size verifies against the root."""
    ids = _ids(size)
    root = merkle_root(ids)
    for i in range(size):
        proof = merkle_proof(ids, i)
        assert verify_inclusion(ids[i], proof, root), f"leaf {i} of {size}"


@pytest.mark.parametrize("size", [2, 3, 5, 8, 17, 100])
def test_proof_is_minimal(size: int) -> None:
    """The proof is O(log n), not O(n)."""
    ids = _ids(size)
    proof = merkle_proof(ids, 0)
    # ceil(log2(n)) levels, minus the promoted-odd-node cases
    assert len(proof) <= max(1, (size - 1).bit_length())


def test_single_leaf_has_empty_proof() -> None:
    """A one-leaf batch is proven by the leaf alone."""
    ids = _ids(1)
    assert merkle_proof(ids, 0) == []
    assert verify_inclusion(ids[0], [], merkle_root(ids))


def test_wrong_leaf_fails_verification() -> None:
    """A leaf that is not in the batch does not verify."""
    ids = _ids(8)
    root = merkle_root(ids)
    stranger = hashlib.sha256(b"not-in-batch").hexdigest().upper()
    proof = merkle_proof(ids, 3)
    assert not verify_inclusion(stranger, proof, root)


def test_wrong_root_fails_verification() -> None:
    """A correct leaf+proof against the wrong root fails."""
    ids = _ids(8)
    proof = merkle_proof(ids, 3)
    wrong_root = merkle_root(_ids(9))
    assert not verify_inclusion(ids[3], proof, wrong_root)


def test_tampered_proof_fails_verification() -> None:
    """Flipping a sibling hash breaks the proof."""
    ids = _ids(8)
    root = merkle_root(ids)
    proof = list(merkle_proof(ids, 2))
    # Replace the sibling hash with a different valid 32-byte value.
    proof[0] = proof[0][0] + "00" * 32
    assert not verify_inclusion(ids[2], proof, root)


def test_reordered_proof_fails_verification() -> None:
    """Swapping proof steps breaks it -- direction is encoded."""
    ids = _ids(8)
    root = merkle_root(ids)
    proof = list(merkle_proof(ids, 2))
    if len(proof) >= 2:
        proof[0], proof[1] = proof[1], proof[0]
        assert not verify_inclusion(ids[2], proof, root)


def test_truncated_proof_fails_verification() -> None:
    """Dropping a proof step breaks it."""
    ids = _ids(8)
    root = merkle_root(ids)
    proof = list(merkle_proof(ids, 2))
    if proof:
        assert not verify_inclusion(ids[2], proof[:-1], root)


def test_garbage_proof_is_rejected_not_raised() -> None:
    """A malformed proof returns False rather than raising.

    An auditor-facing function must not blow up on hostile input.
    """
    ids = _ids(4)
    root = merkle_root(ids)
    assert not verify_inclusion(ids[0], ["ZZZZ"], root)
    assert not verify_inclusion(ids[0], [""], root)
    assert not verify_inclusion(ids[0], ["X" + "00" * 32], root)  # bad direction
    assert not verify_inclusion(ids[0], ["R" + "00"], root)  # short hash
    assert not verify_inclusion(ids[0], ["R" + "ZZ" * 32], root)  # bad hex


def test_garbage_decision_id_is_rejected_not_raised() -> None:
    """A malformed leaf returns False rather than raising."""
    assert not verify_inclusion("not-hex-at-all", [], EMPTY_ROOT)
    assert not verify_inclusion("zzzz", [], EMPTY_ROOT)


def test_out_of_range_index_raises() -> None:
    """An out-of-range index is a programming error, and says so."""
    ids = _ids(4)
    with pytest.raises(IndexError):
        merkle_proof(ids, 4)
    with pytest.raises(IndexError):
        merkle_proof(ids, -1)


# -----------------------------------------------------------------------
# Randomized cross-check against an independent implementation
# -----------------------------------------------------------------------


def _reference_root(ids: list[str]) -> str:
    """A deliberately different implementation, for cross-checking.

    Same *convention* as the production tree (domain-separated leaves,
    left||right interior nodes, odd node promoted unchanged) but written
    independently: it builds the tree by repeatedly folding adjacent
    pairs in a list comprehension rather than by tracking levels, and
    it recomputes the leaf hash inline rather than calling
    :func:`leaf_hash`. Agreement between the two is meaningful because
    the code paths differ; agreement between two copies of the same
    function would not be.
    """
    if not ids:
        return EMPTY_ROOT
    nodes = [
        hashlib.sha256(b"\x00" + bytes.fromhex(i)).digest() for i in ids
    ]
    while len(nodes) > 1:
        nodes = [
            hashlib.sha256(b"\x01" + nodes[i] + nodes[i + 1]).digest()
            if i + 1 < len(nodes)
            else nodes[i]
            for i in range(0, len(nodes), 2)
        ]
    return nodes[0].hex().upper()


def test_matches_independent_implementation() -> None:
    """The production tree agrees with an independently-written one."""
    for size in (1, 2, 3, 4, 5, 8, 13, 32, 100):
        ids = _ids(size)
        assert merkle_root(ids) == _reference_root(ids), f"size {size}"


def test_randomized_inclusion() -> None:
    """Random batches, random leaves: every proof verifies."""
    rng = random.Random(20261028)
    for _ in range(50):
        size = rng.randint(1, 64)
        ids = [hashlib.sha256(rng.randbytes(32)).hexdigest().upper() for _ in range(size)]
        root = merkle_root(ids)
        for _ in range(min(size, 5)):
            i = rng.randrange(size)
            assert verify_inclusion(ids[i], merkle_proof(ids, i), root)


# -----------------------------------------------------------------------
# Cross-batch independence -- the property that stops replay
# -----------------------------------------------------------------------


def test_same_leaf_in_different_batches_gives_different_roots() -> None:
    """A decision anchored in two different batches has two roots.

    This is what makes the batch anchor meaningful: the root commits
    to the whole batch, so an operator cannot cherry-pick which
    decisions to reveal by re-batching.
    """
    ids = _ids(4)
    alt = ids + [hashlib.sha256(b"extra").hexdigest().upper()]
    assert merkle_root(ids) != merkle_root(alt)
