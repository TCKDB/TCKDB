"""Randomised lifecycles of ``f3b8d5a1c702``'s hash assignment (#630).

The revision moves dispersion-column synonyms and a folded dispersion
(``b3lyp-d3bj``) into the hash. Its holder choice follows ``d0a7c3b91e4f``'s
exactly, and the two properties that revision's test pins hold here too, over
rows that differ in the things *this* revision keys:

* **No crash.** After the merge script has merged duplicates (a merged row
  keeps its hash), the downgrade must never need a hash a merged row holds.
* **Exact round trip.** Upgrade then downgrade restores every hash, with or
  without merges in between.

The generator builds states as they exist at ``d0a7c3b91e4f``: in each group
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
from app.chemistry.dispersion_names import (
    DISPERSION_RULES,
    FOLDED_PATTERN,
    dispersion_identity_key,
    level_identity_keys,
)
from app.chemistry.lot_component_names import component_identity_key
from app.chemistry.method_names import NAME_ALIASES, SUFFIX_RULES, method_identity_key
from app.services.calculation_resolution import _level_of_theory_hash
from tests.services.test_basis_identity_key import _all_spellings
from tests.services.test_dispersion_identity_key import _ALL_PAIRS, DISPERSION
from tests.services.test_method_identity_key import _all_methods

_VERSIONS = pathlib.Path(__file__).parents[2] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "f3b8d5a1c702_key_level_of_theory_dispersion_synonyms.py"
_PARENT = _VERSIONS / "d0a7c3b91e4f_key_level_of_theory_method_aliases.py"

_METHODS = [
    "b3lyp", "b3lyp-d3bj", "b3lyp-d3(bj)", "B3LYP-GD3BJ", "wb97x-d3bj", "wb97x",
    "m06-2x-d3zero", "M062X", "mp2",
]
_DISPERSIONS = [None, "d3bj", "gd3bj", "EmpiricalDispersion=GD3BJ", "D3BJ", "d3zero", "gd3"]
_SOLVENTS = [None, "water"]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mig():
    return _load("_mig_f3b8d5a1c702_plan", _MIGRATION)


@pytest.fixture(scope="module")
def parent():
    return _load("_mig_d0a7c3b91e4f_for_f3b8", _PARENT)


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


@pytest.mark.parametrize("spelling", [None, "", "  ", "Water", " WATER\t"])
def test_frozen_component_rule_matches_the_application(mig, spelling):
    assert mig._component_identity_key(spelling) == component_identity_key(spelling)


@pytest.mark.parametrize("spelling", [s for _, s, _ in DISPERSION] + ["d3", "pfd", "b3lyp-d3bj"])
def test_frozen_dispersion_rule_matches_the_application(mig, spelling):
    assert mig._dispersion_identity_key(spelling) == dispersion_identity_key(spelling)


@pytest.mark.parametrize(("method", "dispersion"), _ALL_PAIRS, ids=str)
def test_frozen_level_keys_match_the_application(mig, method, dispersion):
    assert mig._level_identity_keys(method, dispersion) == level_identity_keys(method, dispersion)


@pytest.mark.parametrize("method", _all_methods()[:60])
def test_frozen_level_keys_match_on_every_method_spelling(mig, method):
    for dispersion in (None, "d3bj", "gd3"):
        assert mig._level_identity_keys(method, dispersion) == level_identity_keys(
            method, dispersion
        )


def test_the_frozen_rule_tables_list_what_the_application_lists(mig):
    """A curated entry added to the application without a revision would split
    the deployed hashes from new uploads; this fails until a revision says so."""
    assert [(p.pattern, r) for p, r in mig._DISPERSION_RULES] == list(DISPERSION_RULES)
    assert mig._FOLDED_PATTERN.pattern == FOLDED_PATTERN
    assert mig._NAME_ALIASES == {a.alias: a.canonical for a in NAME_ALIASES}
    assert [(p.pattern, r) for p, r in mig._SUFFIX_RULES] == list(SUFFIX_RULES)


@pytest.mark.parametrize(
    ("method", "dispersion"),
    [(m, d) for m in [*_METHODS, *_all_methods()[:20]] for d in _DISPERSIONS],
)
def test_frozen_hash_matches_the_application(mig, method, dispersion):
    fields = {
        "method": method,
        "basis": "Def2TZVP",
        "aux_basis": "def2/J",
        "cabs_basis": None,
        "dispersion": dispersion,
        "solvent": "Water",
        "solvent_model": "SMD",
        "keywords": "Opt",
        "spin_treatment": "unrestricted",
    }
    row = SimpleNamespace(_mapping=fields)
    assert mig._lot_hash(row, split=True) == _level_of_theory_hash(LevelOfTheoryRef(**fields))


@pytest.mark.parametrize(("method", "dispersion"), _ALL_PAIRS, ids=str)
def test_frozen_pre_revision_formula_is_the_parent_revisions(mig, parent, method, dispersion):
    """The downgrade target is exactly what ``d0a7c3b91e4f`` left."""
    row = _row(1, method, dispersion, "Water", None)
    assert mig._lot_hash(row, split=False) == parent._lot_hash(row, aliased=True)


def test_rule_three_smallest_unmerged_id_takes_the_key_when_nobody_holds_it(mig):
    """Neither row holds the target nor its previous formula's key: smallest id wins."""

    def key(r):
        return mig._lot_hash(r, split=True)

    def prior(r):
        return mig._lot_hash(r, split=False)

    rows = [
        _row(9, "b3lyp-d3bj", None, None, _stale(9)),
        _row(4, "b3lyp", "GD3BJ", None, _stale(4)),
    ]
    updates, groups, blocked = mig.assign_hashes(rows, key, prior, set())
    assert updates == {4: key(rows[1])}
    assert [(h._mapping["id"], [m._mapping["id"] for m in o]) for h, o in groups] == [(4, [9])]
    assert blocked == []
    # A merged smaller id is skipped: the next smallest unmerged row takes it.
    rows = [_row(2, "b3lyp", "gd3bj", None, _stale(2)), *rows]
    updates, _groups, _blocked = mig.assign_hashes(rows, key, prior, {2})
    assert list(updates) == [4]


def test_rule_one_the_row_already_in_column_form_holds_its_hash(mig):
    """``b3lyp`` + ``d3bj`` already holds the key: the folded spellings join it."""

    def key(r):
        return mig._lot_hash(r, split=True)

    def prior(r):
        return mig._lot_hash(r, split=False)

    column = _row(7, "b3lyp", "d3bj", None, None)
    column._mapping["lot_hash"] = prior(column)
    assert prior(column) == key(column)
    folded = _row(1, "b3lyp-d3bj", None, None, None)
    folded._mapping["lot_hash"] = prior(folded)
    gaussian = _row(2, "b3lyp", "gd3bj", None, None)
    gaussian._mapping["lot_hash"] = prior(gaussian)
    updates, groups, _ = mig.assign_hashes([folded, gaussian, column], key, prior, set())
    assert updates == {}
    assert [(h._mapping["id"], sorted(m._mapping["id"] for m in o)) for h, o in groups] == [
        (7, [1, 2])
    ]


@pytest.mark.parametrize("holder_probability", [1.0, 0.7])
def test_assign_hashes_is_the_parent_revisions(mig, parent, holder_probability):
    """At 0.7 some groups have no holder, so the smallest-id fallback is reached."""
    rng = random.Random(630)
    fallbacks = 0
    for _ in range(300):
        rows, merged = _parent_state(mig, rng, holder_probability)

        def key(row):
            return mig._lot_hash(row, split=True)

        def prior(row):
            return mig._lot_hash(row, split=False)

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
    """Rows and merged ids as they can exist at ``d0a7c3b91e4f``."""
    combos = [(m, d, s) for m in _METHODS for d in _DISPERSIONS for s in _SOLVENTS]
    picked = rng.sample(combos, k=rng.randint(2, 10))
    ids = rng.sample(range(1, 40), k=len(picked))
    rows = [_row(i, m, d, s, None) for i, (m, d, s) in zip(ids, picked, strict=True)]
    by_prior = defaultdict(list)
    for row in rows:
        by_prior[mig._lot_hash(row, split=False)].append(row)
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
    rng = random.Random(630)
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
    assert rekeyed > 1000
    assert joined_groups > 1000
    assert with_merges > 500
    assert without_merges > 500
    assert blocked_total == 0  # unreachable for data the merge script made
    assert down_blocked > 0


def test_the_generator_makes_folded_and_column_dispersion_duplicates(mig):
    """The seeds the lifecycle draws from must contain each kind of duplicate."""
    kinds = set()
    rng = random.Random(1)
    for _ in range(500):
        rows, _merged = _parent_state(mig, rng, 1.0)
        by_key = defaultdict(list)
        for row in rows:
            by_key[mig._lot_hash(row, split=True)].append(row._mapping)
        for members in by_key.values():
            if len(members) < 2:
                continue
            if any("-d3" in m["method"].lower() for m in members) and any(
                m["dispersion"] for m in members
            ):
                kinds.add("folded vs column")
            if len({(m["dispersion"] or "").lower() for m in members if m["dispersion"]}) > 1:
                kinds.add("dispersion synonym")
            if len({m["method"].lower() for m in members}) > 1:
                kinds.add("method spelling")
    assert kinds == {"folded vs column", "dispersion synonym", "method spelling"}
