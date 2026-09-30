"""Randomised lifecycles of ``c8424fe82997``'s hash assignment (#585 review).

The revision's holder choice decides which row of a duplicate group takes the
identity-keyed hash. Two properties have to hold for any data the #582 and
#585 tooling can produce, and a hand-picked seed only ever covers a few
shapes:

* **No crash.** After the merge script has merged duplicates (a merged row
  keeps its hash), the downgrade must never need a hash a merged row holds.
* **Exact round trip.** Upgrade then downgrade restores every hash, with or
  without merges in between (for states where every group has a holder, as
  ``38b06819f099`` leaves them).

The generator builds states as they exist at ``38b06819f099``: in each group
of rows sharing the pre-revision hash, one row may hold it (the #582 holder)
and the others carry a stale hash; the #582 script may have merged some of
the others. Rows differ in the case of the method and the spelling of the
basis, the two things the two revisions key.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import random
from collections import defaultdict
from types import SimpleNamespace

import pytest

_MIGRATION = (
    pathlib.Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "c8424fe82997_key_level_of_theory_method_by_identity.py"
)

_METHODS = ["MP2", "mp2", "Mp2", "CCSD(T)", "ccsd(t)"]
_BASES = ["cc-pVDZ", "cc-pvdz", "ccpvdz", "Def2TZVP", "def2-tzvp", None]


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("_mig_c8424fe82997_plan", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(row_id, method, basis, lot_hash):
    return SimpleNamespace(
        _mapping={
            "id": row_id,
            "public_ref": f"lot_{row_id}",
            "lot_hash": lot_hash,
            "method": method,
            "basis": basis,
            "aux_basis": None,
            "cabs_basis": None,
            "dispersion": None,
            "solvent": None,
            "solvent_model": None,
            "keywords": None,
            "spin_treatment": None,
        }
    )


def _stale(row_id):
    return hashlib.sha256(f"stale:{row_id}".encode()).hexdigest()


def _parent_state(mig, rng, holder_probability):
    """Rows and merged ids as they can exist at ``38b06819f099``.

    ``38b06819f099`` gives every group a holder (``holder_probability=1``). A
    group with none exists only where something else wrote ``lot_hash``, such
    as the demo seed; its stale hash is not recoverable, so only uniqueness
    is asserted for those.
    """
    pairs = rng.sample(
        [(m, b) for m in _METHODS for b in _BASES], k=rng.randint(2, 9)
    )
    ids = rng.sample(range(1, 40), k=len(pairs))
    rows = [_row(i, m, b, None) for i, (m, b) in zip(ids, pairs, strict=True)]
    by_prior = defaultdict(list)
    for row in rows:
        by_prior[mig._lot_hash(row, keyed_method=False)].append(row)
    merged: set[int] = set()
    for prior, members in by_prior.items():
        holder = rng.choice(members) if rng.random() < holder_probability else None
        for row in members:
            row._mapping["lot_hash"] = prior if row is holder else _stale(row._mapping["id"])
        if holder is not None:
            merged |= {
                r._mapping["id"] for r in members if r is not holder and rng.random() < 0.5
            }
    return rows, merged


def _apply(rows, updates):
    for row in rows:
        if row._mapping["id"] in updates:
            row._mapping["lot_hash"] = updates[row._mapping["id"]]


def _snapshot(rows):
    return {r._mapping["id"]: r._mapping["lot_hash"] for r in rows}


def _unique(rows):
    hashes = [r._mapping["lot_hash"] for r in rows]
    return len(hashes) == len(set(hashes))


@pytest.mark.parametrize("holder_probability", [1.0, 0.7])
def test_random_lifecycles_never_crash_and_round_trip_exactly(mig, holder_probability):
    rng = random.Random(585)
    with_merges = without_merges = rekeyed = blocked_total = down_blocked = 0
    for _ in range(4000):
        rows, merged = _parent_state(mig, rng, holder_probability)
        original = _snapshot(rows)
        assert _unique(rows)

        updates, groups, blocked = mig.plan_rekey(rows, merged)
        blocked_total += len(blocked)
        rekeyed += len(updates)
        _apply(rows, updates)
        assert _unique(rows), "upgrade violated uq_level_of_theory_lot_hash"

        # The #582 script, run after the upgrade: it merges each group's
        # other members into the holder.
        merged_now = set(merged)
        by_ref = {r._mapping["public_ref"]: r._mapping["id"] for r in rows}
        for _holder_ref, others in groups:
            for ref in others:
                if rng.random() < 0.5:
                    merged_now.add(by_ref[ref])
        before_downgrade = _snapshot(rows)

        down, _g, down_skipped = mig.plan_unkey(rows, merged_now)
        down_blocked += len(down_skipped)
        _apply(rows, down)
        assert _unique(rows), "downgrade violated uq_level_of_theory_lot_hash"
        for row_id in merged_now:  # an alias is never re-hashed
            assert _snapshot(rows)[row_id] == before_downgrade[row_id]

        if merged_now:
            with_merges += 1
        else:
            without_merges += 1
        if holder_probability == 1.0:
            # Exact with merges too: an alias keeps its hash through both
            # directions and every holder gets its previous hash back.
            assert _snapshot(rows) == original, "inexact round trip"
    # Not vacuous: the generator reaches the shapes the properties are about.
    assert rekeyed > 2000
    assert with_merges > 500
    assert without_merges > 500
    assert blocked_total == 0  # unreachable for data the merge script made
    # The downgrade's "NOT re-hashed" branch is exercised, and stays exact.
    assert down_blocked > 20
