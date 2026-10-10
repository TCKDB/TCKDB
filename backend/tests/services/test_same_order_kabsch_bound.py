"""The same-order Kabsch comparison of the no-optimisation duplicate rule is bounded (#692).

What was wrong
--------------
``_merge_rigidly_equal_geometries`` ran a same-order Kabsch comparison over every
same-size pair of a record's energy geometries: O(k^2 n) work that was not charged
to the record's :class:`SearchBudget`. 300 distinct 200-atom geometries took about
4.7 s for one record.

What these tests hold
---------------------
* **Same verdicts.** Over a fixture set of near-duplicates (rigid moves, relabellings,
  rounding noise, a mirror image, a moved atom, a torsion change, an unrelated
  molecule) and over randomised perturbations, every pair gets the verdict the rule
  gave before the change, and the whole record merges into the same representatives.
  The "before" is a verbatim copy of the old function body, kept here as the oracle.
* **The bound.** 300 distinct large geometries cost no Kabsch call and no budget;
  geometries engineered to share a radius of gyration (so the cheap test cannot
  separate them) are charged, and stop at the budget.
"""

from __future__ import annotations

import random
import time
from collections import Counter

import numpy as np
import pytest

from app.chemistry import permuted_rigid_match as prm
from app.chemistry.permuted_rigid_match import PreparedGeometry, SearchBudget, match_prepared
from app.chemistry.torsion_fingerprint import kabsch_rmsd
from app.services import calculation_levels as levels
from tests.services.test_permuted_rigid_match import (
    TOL,
    _butane,
    _fake_geometry,
    _moved_and_permuted,
    _random_atoms,
    _round,
)


def _reference_same_structure(a, b, budget=None, prepared=None) -> bool:
    """``_same_polyatomic_structure`` as it was before #692: same-order Kabsch on every pair."""
    tolerance = levels._rigid_motion_tolerance(a.decimals, b.decimals)
    if a.species == b.species and kabsch_rmsd(a.coordinates, b.coordinates) <= tolerance:
        return True
    if Counter(a.species) != Counter(b.species):
        return False
    first, second = prepared or (
        PreparedGeometry(a.coordinates, a.species),
        PreparedGeometry(b.coordinates, b.species),
    )
    return match_prepared(first, second, tolerance=tolerance, budget=budget).outcome == "matched"


def _reference_merge(geometries) -> dict[int, int]:
    """``_merge_rigidly_equal_geometries`` as it was before #692, over the reference rule."""
    root = {g.id: g.id for g in geometries}
    budget = SearchBudget()
    prepared: dict[int, PreparedGeometry] = {}

    def tables(gid, atoms):
        if gid not in prepared:
            prepared[gid] = PreparedGeometry(atoms.coordinates, atoms.species)
        return prepared[gid]

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    by_size: dict[int, list] = {}
    for g in geometries:
        by_size.setdefault(g.natoms, []).append(g)
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        atoms = {g.id: levels._atoms_of(g) for g in same_size}
        for i, first in enumerate(same_size):
            for second in same_size[i + 1 :]:
                if find(first.id) == find(second.id):
                    continue
                a, b = atoms[first.id], atoms[second.id]
                if a is None or b is None:
                    continue
                if _reference_same_structure(a, b, budget, (tables(first.id, a), tables(second.id, b))):
                    root[find(second.id)] = find(first.id)
    return {g.id: find(g.id) for g in geometries}


def _mirrored(atoms):
    return [(el, (-x, y, z)) for el, (x, y, z) in atoms]


def _displaced(atoms, index: int, amount: float):
    return [(el, (x, y + (amount if i == index else 0.0), z)) for i, (el, (x, y, z)) in enumerate(atoms)]


def _fixture_set():
    """Named geometries, each rounded to six decimals as a deposit would be."""
    base = _round(_butane(180.0))
    return {
        "base": base,
        "moved": _moved_and_permuted(base, seed=1, permute=False),
        "relabelled": _moved_and_permuted(base, seed=2, permute=True),
        "noisy_within_tolerance": _round(_displaced(base, 5, 2.5 * TOL)),
        "noisy_relabelled": _moved_and_permuted(_displaced(base, 5, 2.5 * TOL), seed=3),
        "mirror_image": _round(_mirrored(base)),
        "one_atom_moved": _round(_displaced(base, 5, 1e-3)),
        "gauche": _round(_butane(60.0)),
        "gauche_relabelled": _moved_and_permuted(_butane(60.0), seed=4),
        "other_molecule": _round([*_butane(180.0)[:-1], ("C", (9.0, 9.0, 9.0))]),
    }


def _fixture_geometries():
    named = _fixture_set()
    sizes = {len(atoms) for atoms in named.values()}
    assert sizes == {14}, sizes
    return named, [_fake_geometry(i, atoms) for i, atoms in enumerate(named.values())]


def test_every_pair_of_the_fixture_set_gets_the_verdict_it_got_before() -> None:
    named, geometries = _fixture_geometries()
    atoms = [levels._atoms_of(g) for g in geometries]
    verdicts = {}
    for i, a in enumerate(atoms):
        for j, b in enumerate(atoms):
            if i == j:
                continue
            new = levels._same_polyatomic_structure(a, b, SearchBudget())
            old = _reference_same_structure(a, b, SearchBudget())
            assert new == old, (list(named)[i], list(named)[j], new, old)
            verdicts[i, j] = new
    # Not vacuous: the set holds duplicates and distinct structures, and at least one
    # distinct pair the cheap test cannot separate (equal radius of gyration), so the
    # Kabsch comparison still decides it.
    assert any(verdicts.values()) and not all(verdicts.values())
    names = list(named)
    mirror, base = names.index("mirror_image"), names.index("base")
    assert verdicts[base, mirror] is False
    assert abs(atoms[base].gyration_radius - atoms[mirror].gyration_radius) <= TOL


def test_the_whole_record_merges_into_the_same_representatives() -> None:
    _named, geometries = _fixture_geometries()
    assert levels._merge_rigidly_equal_geometries(geometries) == _reference_merge(geometries)
    assert len(set(_reference_merge(geometries).values())) > 1


@pytest.mark.parametrize("scale", [0.0, 0.5, 1.0, 3.0, 100.0], ids=lambda s: f"{s}xtol")
def test_randomised_perturbations_get_the_same_verdicts(scale: float) -> None:
    rng = np.random.default_rng(int(scale * 10))
    shuffler = random.Random(5)
    base = _round(_butane(180.0))
    for case in range(25):
        noise = rng.normal(size=(len(base), 3)) * scale * TOL
        moved = [(el, tuple(float(v) for v in np.asarray(c) + n)) for (el, c), n in zip(base, noise, strict=True)]
        if case % 2:
            order = list(range(len(moved)))
            shuffler.shuffle(order)
            moved = [moved[i] for i in order]
        other = _round(moved)
        a = levels._atoms_of(_fake_geometry(1, base))
        b = levels._atoms_of(_fake_geometry(2, other))
        assert levels._same_polyatomic_structure(a, b, SearchBudget()) == _reference_same_structure(a, b, SearchBudget())


# ---- the bound -----------------------------------------------------------------------------------------------


def test_distinct_large_geometries_cost_no_kabsch_and_no_budget(monkeypatch) -> None:
    """300 distinct 200-atom geometries: 44,850 pairs, each answered by two floats."""
    spent: list[SearchBudget] = []
    real_budget = levels.SearchBudget

    def spying_budget():
        budget = real_budget()
        spent.append(budget)
        return budget

    kabsch_calls = []
    real_kabsch = levels.kabsch_rmsd
    monkeypatch.setattr(levels, "SearchBudget", spying_budget)
    monkeypatch.setattr(levels, "kabsch_rmsd", lambda *a, **k: kabsch_calls.append(1) or real_kabsch(*a, **k))

    rng = np.random.default_rng(0)
    geometries = [_fake_geometry(g, _random_atoms(rng, 200)) for g in range(300)]
    started = time.perf_counter()
    roots = levels._merge_rigidly_equal_geometries(geometries)
    elapsed = time.perf_counter() - started

    assert roots == {g.id: g.id for g in geometries}
    assert kabsch_calls == [], f"{len(kabsch_calls)} Kabsch comparisons for 300 distinct shapes"
    assert spent[0].work_left == prm.MAX_WORK_PER_RECORD and spent[0].fits_left == prm.MAX_FITS_PER_RECORD
    assert elapsed < 2.0, f"{elapsed:.2f} s (4.7 s before the change)"


def _same_radius_distinct_shapes(n: int, count: int):
    """``count`` distinct shapes of ``n`` atoms with one radius of gyration: no cheap test separates them."""
    rng = np.random.default_rng(11)
    base = np.asarray([c for _, c in _random_atoms(rng, n)])
    target = levels._gyration_radius([tuple(row) for row in base])
    out = []
    for g in range(count):
        stretched = base * rng.uniform(0.7, 1.3, size=3)
        rows = [tuple(float(v) for v in row) for row in stretched]
        scaled = [tuple(v * target / levels._gyration_radius(rows) for v in row) for row in rows]
        out.append(_fake_geometry(g, [(el, c) for (el, _), c in zip(_random_atoms(np.random.default_rng(11), n), scaled, strict=True)]))
    return out


def test_pairs_the_cheap_test_cannot_separate_are_charged_and_stop_at_the_budget(monkeypatch) -> None:
    n, comparisons = 100, 400
    monkeypatch.setattr(prm, "MAX_WORK_PER_RECORD", levels._KABSCH_UNITS_PER_ATOM * n * comparisons)
    kabsch_calls = []
    real_kabsch = levels.kabsch_rmsd
    monkeypatch.setattr(levels, "kabsch_rmsd", lambda *a, **k: kabsch_calls.append(1) or real_kabsch(*a, **k))

    geometries = _same_radius_distinct_shapes(n, 120)  # 7,140 pairs
    radii = {round(levels._atoms_of(g).gyration_radius, 9) for g in geometries}
    assert len(radii) == 1, "the geometries must be indistinguishable by radius of gyration"

    levels._merge_rigidly_equal_geometries(geometries)

    assert 0 < len(kabsch_calls) <= comparisons, len(kabsch_calls)
    assert len(kabsch_calls) < 7_140


def test_an_uncapped_direct_comparison_is_unchanged() -> None:
    """Without a budget (a direct call) the rule is exactly the old one."""
    _named, geometries = _fixture_geometries()
    a, b = (levels._atoms_of(g) for g in geometries[:2])
    assert levels._same_polyatomic_structure(a, b) is True
