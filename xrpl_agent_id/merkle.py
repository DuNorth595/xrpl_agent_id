# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
r"""Merkle-tree batch anchoring for audit records.

**Why this exists.** XRPL's story is fast and cheap. A per-decision
on-chain memo spends a transaction fee on *every* authorization
decision, and at any real volume that dominates the cost — at one
million decisions a day it is roughly \$6,650/day at the median fee,
versus about \$6.65/day if the decisions are anchored in batches. Worse,
it means the *audit* fee is stacked on top of the fee the work already
paid, which is exactly the "fees on top of fees" nobody would touch.

So: one transaction per **batch**, not per decision. The transaction
carries the Merkle root of the batch; the individual decisions stay
off-chain but become *provably* included in the anchored root.

The tree here is deliberately minimal and standard:

- leaves are the existing ``decision_id`` values (uppercase hex
  SHA-256 of the decision);
- interior nodes are ``SHA256(left || right)`` over raw bytes;
- odd nodes at a level are promoted unchanged to the next level
  (the "duplicate-last" convention, which keeps the tree
  deterministic without padding attacks);
- the root of a single-leaf batch is that leaf.

**Verification** is the point, so it is provided: given a leaf and a
proof path, :func:`verify_inclusion` recomputes the root and compares.
That is what lets a third party confirm "this decision was in the
batch anchored by transaction X" without trusting the operator's
database.

This module is self-contained -- no ledger dependency -- so the tree
can be tested and audited on its own.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

__all__ = [
    "EMPTY_ROOT",
    "merkle_root",
    "merkle_proof",
    "verify_inclusion",
    "leaf_hash",
]


#: The root of an empty batch. Distinct from any real root, so an empty
#: batch can never be mistaken for a batch of one.
EMPTY_ROOT = hashlib.sha256(b"").hexdigest().upper()


def leaf_hash(decision_id: str) -> bytes:
    """Hash a leaf for the tree.

    The leaf is domain-separated from interior nodes so a leaf can
    never be replayed as an interior node (the classic second-preimage
    problem in Merkle trees).

    Args:
        decision_id: the uppercase hex SHA-256 of a decision, as
            produced by :func:`xrpl_agent_id.audit.compute_decision_id`.

    Returns:
        The 32-byte leaf hash.
    """
    return hashlib.sha256(b"\x00" + bytes.fromhex(decision_id)).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    """Hash an interior node, domain-separated from leaves."""
    return hashlib.sha256(b"\x01" + left + right).digest()


def _levels(leaves: Sequence[bytes]) -> list[list[bytes]]:
    """Build the tree's level lists, bottom-up.

    Returns a list where index 0 is the leaf level and the last index
    holds the single root node. An odd node at any level is promoted
    unchanged to the next level rather than being duplicated.
    """
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        current = levels[-1]
        parent: list[bytes] = []
        for i in range(0, len(current), 2):
            if i + 1 < len(current):
                parent.append(_node_hash(current[i], current[i + 1]))
            else:
                # Odd node: promote unchanged.
                parent.append(current[i])
        levels.append(parent)
    return levels


def merkle_root(decision_ids: Iterable[str]) -> str:
    """Compute the Merkle root of a batch of decision ids.

    Args:
        decision_ids: the decision ids in the batch, in order. Order
            matters -- the root commits to it.

    Returns:
        The uppercase hex root. An empty batch returns
        :data:`EMPTY_ROOT`.
    """
    ids = list(decision_ids)
    if not ids:
        return EMPTY_ROOT
    leaves = [leaf_hash(d) for d in ids]
    if len(leaves) == 1:
        return leaves[0].hex().upper()
    return _levels(leaves)[-1][0].hex().upper()


def merkle_proof(decision_ids: Sequence[str], index: int) -> list[str]:
    """Build the inclusion proof for the leaf at ``index``.

    Args:
        decision_ids: the full batch, in the same order used for the
            root.
        index: the position of the leaf to prove.

    Returns:
        The sibling hashes from leaf level up to the root's child, in
        that order. Each entry is a direction prefix (``"L"`` or
        ``"R"``) followed by the uppercase hex sibling hash, so the
        verifier knows which side the sibling is on. An empty list means
        the batch had a single leaf, which is its own root.

    Raises:
        IndexError: if ``index`` is out of range.
    """
    ids = list(decision_ids)
    if not 0 <= index < len(ids):
        raise IndexError(f"index {index} out of range for batch of {len(ids)}")

    leaves = [leaf_hash(d) for d in ids]
    if len(leaves) == 1:
        return []

    levels = _levels(leaves)
    proof: list[str] = []
    idx = index
    for level in levels[:-1]:
        if idx % 2 == 0:
            # Left child: sibling is the right node, if it exists.
            if idx + 1 < len(level):
                proof.append("R" + level[idx + 1].hex().upper())
            # An odd node promoted unchanged has no sibling at this
            # level; the verifier must not add one either.
        else:
            proof.append("L" + level[idx - 1].hex().upper())
        idx //= 2
    return proof


def verify_inclusion(
    decision_id: str,
    proof: Sequence[str],
    root: str,
) -> bool:
    """Verify that ``decision_id`` is included under ``root``.

    This is the operation an auditor performs. It uses only the leaf,
    the proof path, and the anchored root -- no trust in the operator's
    database.

    Args:
        decision_id: the leaf to verify.
        proof: the sibling path from :func:`merkle_proof`.
        root: the anchored root, uppercase hex.

    Returns:
        True if the leaf hashes to the root under the given path.
    """
    try:
        current = leaf_hash(decision_id)
    except ValueError:
        # Not a valid hex decision id.
        return False

    for step in proof:
        if not step:
            return False
        direction, sibling_hex = step[0], step[1:]
        try:
            sibling = bytes.fromhex(sibling_hex)
        except ValueError:
            return False
        if len(sibling) != 32:
            return False
        if direction == "R":
            current = _node_hash(current, sibling)
        elif direction == "L":
            current = _node_hash(sibling, current)
        else:
            return False

    return current.hex().upper() == root.upper()
