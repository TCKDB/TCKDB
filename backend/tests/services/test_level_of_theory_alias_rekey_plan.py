"""Randomised lifecycles of ``d0a7c3b91e4f``'s hash assignment (#618, #602).

The revision moves curated method aliases and dispersion/solvent/solvent-model
case into the hash. Its holder choice follows ``c8424fe82997``'s exactly, and
the two properties that revision's test pins hold here too, over rows that
differ in the things *this* revision keys:

* **No crash.** After the merge script has merged duplicates (a merged row
  keeps its hash), the downgrade must never need a hash a merged row holds.
* **Exact round trip.** Upgrade then downgrade restores every hash, with or
  without merges in between.

The generator builds states as they exist at ``c8424fe82997``: in each group
of rows sharing the pre-revision hash, one row may hold it and the others
carry a stale hash; the merge script may have merged some of the others.

This file also holds the revision's frozen rules to the application and its
``assign_hashes`` to the parent revision's.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import random
from collections import defaultdict
from types import SimpleNamespace

import pytest
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.lot_component_names import component_identity_key
from app.chemistry.method_names import method_identity_key
from app.services.calculation_resolution import _level_of_theory_hash
from tests.services.test_basis_identity_key import _all_spellings
from tests.services.test_lot_component_identity_key import _ALL as _COMPONENT_SPELLINGS
from tests.services.test_method_identity_key import SAME_METHOD, _all_methods

_VERSIONS = pathlib.Path(__file__).parents[2] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "d0a7c3b91e4f_key_level_of_theory_method_aliases.py"
_PARENT = _VERSIONS / "c8424fe82997_key_level_of_theory_method_by_identity.py"

_METHODS = ["wb97xd", "wb97x-d", "WB97XD", "m06-2x", "M062X", "b3lyp-d3(bj)", "b3lyp-gd3bj", "MP2", "mp2"]
_DISPERSIONS = [None, "d3bj", "D3BJ"]
_SOLVENTS = [None, "water", "Water"]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mig():
    return _load("_mig_d0a7c3b91e4f_plan", _MIGRATION)


@pytest.fixture(scope="module")
def parent():
    return _load("_mig_c8424fe82997_for_d0a7", _PARENT)


def _row(row_id, method, dispersion, solvent, lot_hash, basis="def2-tzvp"):
    return SimpleNamespace(
        _mapping={
            "id": row_id,
            "public_ref": f"lot_{row_id}",
            "lot_hash": lot_hash,
            "method": method,
            "basis": basis,
            "aux_basis": None,
            "cabs_basis": None,
            "dispersion": dispersion,
            "solvent": solvent,
            "solvent_model": "smd" if solvent else None,
            "keywords": None,
            "spin_treatment": None,
        }
    )


def _stale(row_id):
    return hashlib.sha256(f"stale:{row_id}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# The frozen rules match the application
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", _all_methods())
def test_frozen_method_rule_matches_the_application(mig, spelling):
    assert mig._method_identity_key(spelling) == method_identity_key(spelling)


@pytest.mark.parametrize("spelling", [*_all_spellings()])
def test_frozen_basis_rule_matches_the_application(mig, spelling):
    assert mig._basis_identity_key(spelling) == basis_identity_key(spelling)


@pytest.mark.parametrize(
    "spelling", [None, "", "  ", *(s for _, s, _ in _COMPONENT_SPELLINGS), " Water\t"]
)
def test_frozen_component_rule_matches_the_application(mig, spelling):
    assert mig._component_identity_key(spelling) == component_identity_key(spelling)


def test_the_frozen_alias_tables_list_what_the_application_lists(mig):
    """A curated alias added to the application without a revision would split
    the deployed hashes from new uploads; this fails until a revision says so."""
    from app.chemistry.method_names import NAME_ALIASES, SUFFIX_RULES

    assert mig._NAME_ALIASES == {a.alias: a.canonical for a in NAME_ALIASES}
    assert [(p.pattern, r) for p, r in mig._SUFFIX_RULES] == list(SUFFIX_RULES)


@pytest.mark.parametrize(
    ("method", "dispersion", "solvent"),
    [
        (m, d, s)
        for m in _all_methods()[:40]
        for d, s in ((None, None), ("D3BJ", "Water"), ("gd3bj", None))
    ],
)
def test_frozen_hash_matches_the_application(mig, method, dispersion, solvent):
    fields = {
        "method": method,
        "basis": "Def2TZVP",
        "aux_basis": "def2/J",
        "cabs_basis": None,
        "dispersion": dispersion,
        "solvent": solvent,
        "solvent_model": "SMD" if solvent else None,
        "keywords": "Opt",
        "spin_treatment": "unrestricted",
    }
    row = SimpleNamespace(_mapping=fields)
    assert mig._lot_hash(row, aliased=True) == _level_of_theory_hash(LevelOfTheoryRef(**fields))


def test_frozen_pre_revision_formula_is_the_parent_revisions(mig, parent):
    """The downgrade target is exactly what ``c8424fe82997`` left."""
    for method in _all_methods():
        row = _row(1, method, "D3BJ", "Water", None)
        assert mig._lot_hash(row, aliased=False) == parent._lot_hash(row, keyed_method=True)


def test_rule_three_smallest_unmerged_id_takes_the_key_when_nobody_holds_it(mig):
    """Neither row holds the target nor its previous formula's key: smallest id wins."""

    def key(r):
        return mig._lot_hash(r, aliased=True)

    def prior(r):
        return mig._lot_hash(r, aliased=False)

    rows = [_row(9, "wb97x-d", None, None, _stale(9)), _row(4, "WB97XD", None, None, _stale(4))]
    updates, groups, blocked = mig.assign_hashes(rows, key, prior, set())
    assert updates == {4: key(rows[1])}
    assert [(h._mapping["id"], [m._mapping["id"] for m in o]) for h, o in groups] == [(4, [9])]
    assert blocked == []
    # A merged smaller id is skipped: the next smallest unmerged row takes it.
    rows = [_row(2, "wb97x-d", None, None, _stale(2)), *rows]
    updates, _groups, _blocked = mig.assign_hashes(rows, key, prior, {2})
    assert list(updates) == [4]


@pytest.mark.parametrize("holder_probability", [1.0, 0.7])
def test_assign_hashes_is_the_parent_revisions(mig, parent, holder_probability):
    """At 0.7 some groups have no holder, so the smallest-id fallback is reached."""
    rng = random.Random(618)
    fallbacks = 0
    for _ in range(300):
        rows, merged = _parent_state(mig, rng, holder_probability)

        def key(row):
            return mig._lot_hash(row, aliased=True)

        def prior(row):
            return mig._lot_hash(row, aliased=False)

        ours = mig.assign_hashes(rows, key, prior, merged)
        theirs = parent.assign_hashes(rows, key, prior, merged)
        ids = lambda result: (  # noqa: E731
            result[0],
            [(h._mapping["id"], [m._mapping["id"] for m in o]) for h, o in result[1]],
            [b._mapping["id"] for b in result[2]],
        )
        assert ids(ours) == ids(theirs)
        for holder, others in ours[1]:
            if not any(
                m._mapping["lot_hash"] in (key(m), prior(m)) for m in [holder, *others]
            ):
                fallbacks += 1
    if holder_probability < 1.0:
        assert fallbacks > 5  # the fallback is exercised, not skipped


# ---------------------------------------------------------------------------
# Randomised lifecycles
# ---------------------------------------------------------------------------


def _parent_state(mig, rng, holder_probability):
    """Rows and merged ids as they can exist at ``c8424fe82997``."""
    combos = [(m, d, s) for m in _METHODS for d in _DISPERSIONS for s in _SOLVENTS]
    picked = rng.sample(combos, k=rng.randint(2, 10))
    ids = rng.sample(range(1, 40), k=len(picked))
    rows = [_row(i, m, d, s, None) for i, (m, d, s) in zip(ids, picked, strict=True)]
    by_prior = defaultdict(list)
    for row in rows:
        by_prior[mig._lot_hash(row, aliased=False)].append(row)
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
    rng = random.Random(618)
    with_merges = without_merges = rekeyed = blocked_total = down_blocked = 0
    joined_groups = 0
    for _ in range(4000):
        rows, merged = _parent_state(mig, rng, holder_probability)
        original = _snapshot(rows)
        assert _unique(rows)

        updates, groups, blocked = mig.plan_rekey(rows, merged)
        blocked_total += len(blocked)
        rekeyed += len(updates)
        joined_groups += len(groups)
        _apply(rows, updates)
        assert _unique(rows), "upgrade violated uq_level_of_theory_lot_hash"

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
            assert _snapshot(rows) == original, "inexact round trip"
    # Not vacuous: the generator reaches the shapes the properties are about.
    assert rekeyed > 2000
    assert joined_groups > 1000
    assert with_merges > 500
    assert without_merges > 500
    assert blocked_total == 0  # unreachable for data the merge script made
    assert down_blocked > 0


def test_the_generator_makes_alias_and_case_duplicates(mig):
    """The seeds the lifecycle draws from must contain each kind of duplicate."""
    kinds = set()
    rng = random.Random(1)
    for _ in range(500):
        rows, _merged = _parent_state(mig, rng, 1.0)
        by_key = defaultdict(list)
        for row in rows:
            by_key[mig._lot_hash(row, aliased=True)].append(row._mapping)
        for members in by_key.values():
            if len(members) < 2:
                continue
            if len({m["method"].lower() for m in members}) > 1:
                kinds.add("method alias")
            if len({m["dispersion"] for m in members}) > 1:
                kinds.add("dispersion case")
            if len({m["solvent"] for m in members}) > 1:
                kinds.add("solvent case")
    assert kinds == {"method alias", "dispersion case", "solvent case"}


def test_alias_spellings_of_the_table_are_joined_by_the_frozen_rule(mig):
    for _method, spellings, key in SAME_METHOD:
        for _program, spelling in spellings:
            assert mig._method_identity_key(spelling) == key
