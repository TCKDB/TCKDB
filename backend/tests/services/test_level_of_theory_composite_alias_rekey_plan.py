"""Randomised lifecycles of ``b9e4c2a7d153``'s hash assignment (ADR 0021).

The revision moves the named composite methods' alias spellings into the hash.
Its holder choice follows ``d0a7c3b91e4f``'s exactly, and the two properties
that revision's test pins hold here too, over rows that differ in the thing
*this* revision keys:

* **No crash.** After the merge script has merged duplicates (a merged row
  keeps its hash), the downgrade must never need a hash a merged row holds.
* **Exact round trip.** Upgrade then downgrade restores every hash, with or
  without merges in between.

The generator builds states as they exist at ``f3b8d5a1c702``: in each group of
rows sharing the previous hash, one row may hold it and the others carry a
stale hash; the merge script may have merged some of the others.

This file also holds the revision's frozen rules to the application and its
``assign_hashes`` to the parent revision's, and its previous formula to
``f3b8d5a1c702``'s (dispersion synonyms and folded dispersion kept).
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
from app.chemistry.method_names import NAME_ALIASES, SUFFIX_RULES, method_identity_key
from app.services.calculation_resolution import _level_of_theory_hash
from tests.services.test_basis_identity_key import _all_spellings
from tests.services.test_lot_component_identity_key import _ALL as _COMPONENT_SPELLINGS
from tests.services.test_method_identity_key import SAME_METHOD, _all_methods

_VERSIONS = pathlib.Path(__file__).parents[2] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "b9e4c2a7d153_key_level_of_theory_composite_method_aliases.py"
_PARENT = _VERSIONS / "f3b8d5a1c702_key_level_of_theory_dispersion_synonyms.py"

_METHODS = [
    "CBS-QB3", "cbsqb3", "CBSQB3", "cbs-qb3", "rocbsqb3", "ROCBS-QB3",
    "G4(MP2)", "g4mp2", "G4MP2", "g3(mp2)", "G3MP2", "g3(mp2)b3", "g3mp2b3",
    "W1BD", "W1-BD", "w1bd", "wb97xd", "wb97x-d", "MP2", "mp2",
    "b3lyp", "B3LYP-GD3BJ", "b3lyp-d3(bj)", "b3lyp-d3bj", "cbsqb3-d3bj", "pbe0-d3zero",
]
#: Real dispersion spellings, so a partial re-freeze of #630's rules fails: the
#: column synonyms, Gaussian's route form, the D3(BJ) form and zero-damping.
_DISPERSIONS = [
    None, "d3bj", "D3BJ", "GD3BJ", "EmpiricalDispersion=GD3BJ", "gd3", "D3(BJ)", "d30", "gd2",
]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mig():
    return _load("_mig_b9e4c2a7d153_plan", _MIGRATION)


@pytest.fixture(scope="module")
def parent():
    return _load("_mig_d0a7c3b91e4f_for_b9e4", _PARENT)


def _row(row_id, method, dispersion, lot_hash, basis="def2-tzvp"):
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
            "solvent": None,
            "solvent_model": None,
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
    assert mig._NAME_ALIASES == {a.alias: a.canonical for a in NAME_ALIASES}
    assert [(p.pattern, r) for p, r in mig._SUFFIX_RULES] == list(SUFFIX_RULES)


def test_the_previous_alias_table_is_the_parent_revisions(mig, parent):
    assert mig._PRIOR_NAME_ALIASES == parent._NAME_ALIASES
    assert [(p.pattern, r) for p, r in mig._SUFFIX_RULES] == [
        (p.pattern, r) for p, r in parent._SUFFIX_RULES
    ]


def test_the_frozen_dispersion_rules_are_the_parent_revisions_and_the_applications(mig, parent):
    """Dropping #630 from the formula would silently revert it; both must hold."""
    from app.chemistry.dispersion_names import DISPERSION_RULES, FOLDED_PATTERN

    assert [(p.pattern, r) for p, r in mig._DISPERSION_RULES] == [
        (p.pattern, r) for p, r in parent._DISPERSION_RULES
    ]
    assert [(p.pattern, r) for p, r in mig._DISPERSION_RULES] == list(DISPERSION_RULES)
    assert mig._FOLDED_PATTERN.pattern == parent._FOLDED_PATTERN.pattern == FOLDED_PATTERN


@pytest.mark.parametrize(
    ("method", "dispersion"),
    [(m, d) for m in _all_methods() for d in _DISPERSIONS],
)
def test_frozen_hash_matches_the_application(mig, method, dispersion):
    fields = {
        "method": method,
        "basis": "Def2TZVP",
        "aux_basis": "def2/J",
        "cabs_basis": None,
        "dispersion": dispersion,
        "solvent": "Water" if dispersion else None,
        "solvent_model": "SMD" if dispersion else None,
        "keywords": "Opt",
        "spin_treatment": "unrestricted",
    }
    row = SimpleNamespace(_mapping=fields)
    assert mig._lot_hash(row, aliased=True) == _level_of_theory_hash(LevelOfTheoryRef(**fields))


@pytest.mark.parametrize(
    ("method", "dispersion"), [(m, d) for m in _all_methods() for d in _DISPERSIONS]
)
def test_frozen_previous_formula_is_the_parent_revisions(mig, parent, method, dispersion):
    """The downgrade target is exactly what ``f3b8d5a1c702`` left (#630 included)."""
    row = _row(1, method, dispersion, None)
    assert mig._lot_hash(row, aliased=False) == parent._lot_hash(row, split=True)


def test_the_downgrade_target_keeps_630_in_it(mig):
    """The reviewer's regression: re-pointing only ``down_revision`` re-hashed these
    three back to their pre-#630 hashes. They are one level, before and after."""
    a = _row(1, "B3LYP", "GD3BJ", None)
    b = _row(2, "b3lyp", "EmpiricalDispersion=GD3BJ", None)
    c = _row(3, "b3lyp-gd3bj", None, None)
    for aliased in (True, False):
        assert (
            mig._lot_hash(a, aliased=aliased)
            == mig._lot_hash(b, aliased=aliased)
            == mig._lot_hash(c, aliased=aliased)
        )


def test_the_composite_aliases_are_what_changes_the_hash(mig):
    for method in ("cbsqb3", "G4(MP2)", "g3(mp2)b3", "rocbsqb3", "cbs4m", "cbsapno", "g3(mp2)"):
        row = _row(1, method, None, None)
        assert mig._lot_hash(row, aliased=True) != mig._lot_hash(row, aliased=False), method
    for method in ("cbs-qb3", "g4mp2", "W1-BD", "w1bd", "cbs-qb3-paraskevas", "wb97x-d", "b3lyp"):
        row = _row(1, method, None, None)
        assert mig._lot_hash(row, aliased=True) == mig._lot_hash(row, aliased=False), method


def test_rule_three_smallest_unmerged_id_takes_the_key_when_nobody_holds_it(mig):
    """Neither row holds the target nor its previous formula's key: smallest id wins."""

    def key(r):
        return mig._lot_hash(r, aliased=True)

    def prior(r):
        return mig._lot_hash(r, aliased=False)

    rows = [_row(9, "cbsqb3", None, _stale(9)), _row(4, "CBS-QB3", None, _stale(4))]
    updates, groups, blocked = mig.assign_hashes(rows, key, prior, set())
    assert updates == {4: key(rows[1])}
    assert [(h._mapping["id"], [m._mapping["id"] for m in o]) for h, o in groups] == [(4, [9])]
    assert blocked == []
    # A merged smaller id is skipped: the next smallest unmerged row takes it.
    rows = [_row(2, "cbsqb3", None, _stale(2)), *rows]
    updates, _groups, _blocked = mig.assign_hashes(rows, key, prior, {2})
    assert list(updates) == [4]


@pytest.mark.parametrize("holder_probability", [1.0, 0.7])
def test_assign_hashes_is_the_parent_revisions(mig, parent, holder_probability):
    """At 0.7 some groups have no holder, so the smallest-id fallback is reached."""
    rng = random.Random(2021)
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
    """Rows and merged ids as they can exist at ``f3b8d5a1c702``."""
    # A few methods per state, so spellings of one recipe meet in a state often.
    methods = rng.sample(_METHODS, k=4)
    combos = [(m, d) for m in methods for d in _DISPERSIONS]
    picked = rng.sample(combos, k=rng.randint(2, 10))
    ids = rng.sample(range(1, 40), k=len(picked))
    rows = [_row(i, m, d, None) for i, (m, d) in zip(ids, picked, strict=True)]
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
    rng = random.Random(2021)
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


def test_the_generator_makes_composite_alias_duplicates(mig):
    """The seeds the lifecycle draws from must contain each kind of alias duplicate."""
    families = set()
    rng = random.Random(1)
    for _ in range(500):
        rows, _merged = _parent_state(mig, rng, 1.0)
        by_key = defaultdict(list)
        for row in rows:
            by_key[mig._lot_hash(row, aliased=True)].append(row._mapping)
        for members in by_key.values():
            if len({m["method"].lower() for m in members}) > 1:
                families.add(mig._method_identity_key(members[0]["method"]))
    assert {"cbs-qb3", "g4mp2", "g3mp2b3"} <= families


def test_alias_spellings_of_the_table_are_joined_by_the_frozen_rule(mig):
    for _method, spellings, key in SAME_METHOD:
        for _program, spelling in spellings:
            assert mig._method_identity_key(spelling) == key
