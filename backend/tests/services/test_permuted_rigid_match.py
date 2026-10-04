"""Relabelling search behind the no-optimisation duplicate rule (#679).

``find_matching_permutation`` answers one question: is the second geometry the
first moved rigidly, with its atoms listed in another order? These tests drive
it on its own, on geometries built to be hard in the specific ways that matter:
symmetric (several valid relabellings), chiral (a mirror image that every
distance agrees with), isotopically labelled (same coordinates, different
atoms), and large (the cost bound).

The rule that uses it (``calculation_levels``) is covered end to end in
``tests/api/test_api_permuted_duplicate_sp.py``.
"""

from __future__ import annotations

import math
import random
import time

import numpy as np
import pytest

from app.chemistry import permuted_rigid_match as prm
from app.chemistry.permuted_rigid_match import SearchBudget, find_matching_permutation
from app.chemistry.torsion_fingerprint import kabsch_rmsd

#: The tolerance two six-decimal deposits earn (``calculation_levels._rigid_motion_tolerance(6, 6)``).
TOL = math.sqrt(3.0) * 1e-6


def _rotation(seed: int) -> np.ndarray:
    q = np.random.default_rng(seed).normal(size=4)
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _moved_and_permuted(atoms, *, seed: int, permute: bool = True, decimals: int = 6):
    """``atoms`` rotated, shifted, listed in a shuffled order and rounded as a deposit would be."""
    rotation = _rotation(seed)
    order = list(range(len(atoms)))
    if permute:
        random.Random(seed).shuffle(order)
    out = []
    for i in order:
        element, position = atoms[i]
        moved = rotation @ np.asarray(position) + np.array([1.0, -2.0, 0.5])
        out.append((element, tuple(round(float(v), decimals) for v in moved)))
    return out


def _round(atoms, decimals: int = 6):
    return [(el, tuple(round(v, decimals) for v in c)) for el, c in atoms]


def _search(first, second, *, tolerance: float = TOL, budget=None):
    return find_matching_permutation(
        [c for _, c in first],
        [el for el, _ in first],
        [c for _, c in second],
        [el for el, _ in second],
        tolerance=tolerance,
        budget=budget,
    )


def _assert_is_the_relabelling(first, second, found):
    """The returned permutation really lays ``second`` on ``first`` and keeps every atom's element."""
    assert found.outcome == "matched", found
    perm = found.permutation
    assert sorted(perm) == list(range(len(first)))
    assert [second[j][0] for j in perm] == [el for el, _ in first]
    relabelled = [second[j][1] for j in perm]
    assert kabsch_rmsd([c for _, c in first], relabelled) <= TOL


# ---- geometries -------------------------------------------------------------------------------------------------

_WATER = [("O", (0.0, 0.0, 0.117301)), ("H", (0.0, 0.757143, -0.469207)), ("H", (0.0, -0.757143, -0.469207))]


def _embedded(smiles: str, seed: int = 3):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=seed)
    conf = mol.GetConformer()
    return [(a.GetSymbol(), tuple(float(v) for v in conf.GetAtomPosition(a.GetIdx()))) for a in mol.GetAtoms()]


def _methane():
    """Ideal tetrahedral methane: 24 proper-and-improper symmetries, 12 proper; every H equivalent."""
    h = 0.629
    return [("C", (0.0, 0.0, 0.0))] + [("H", (sx * h, sy * h, sz * h)) for sx, sy, sz in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1))]


def _staggered_ethane():
    """Ideal staggered ethane (D3d): the three H of each CH3 are equivalent, and the two methyls are too."""
    z_c, z_h, rho = 0.77, 0.77 + 1.09 * math.cos(math.radians(70.5)), 1.09 * math.sin(math.radians(70.5))
    atoms = [("C", (0.0, 0.0, z_c)), ("C", (0.0, 0.0, -z_c))]
    for k in range(3):
        a = math.radians(120 * k)
        atoms.append(("H", (rho * math.cos(a), rho * math.sin(a), z_h)))
    for k in range(3):
        a = math.radians(120 * k + 60)
        atoms.append(("H", (rho * math.cos(a), rho * math.sin(a), -z_h)))
    return atoms


def _chbrclf(mirror: bool = False):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("[C@H](F)(Cl)Br"))
    AllChem.EmbedMolecule(mol, randomSeed=11)
    conf = mol.GetConformer()
    sign = -1.0 if mirror else 1.0
    atoms = []
    for a in mol.GetAtoms():
        p = conf.GetAtomPosition(a.GetIdx())
        atoms.append((a.GetSymbol(), (sign * float(p.x), float(p.y), float(p.z))))
    return atoms


def _butane(torsion_deg: float):
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdMolTransforms

    mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    rdMolTransforms.SetDihedralDeg(mol.GetConformer(), 0, 1, 2, 3, torsion_deg)
    conf = mol.GetConformer()
    return [(a.GetSymbol(), tuple(float(v) for v in conf.GetAtomPosition(a.GetIdx()))) for a in mol.GetAtoms()]


def _twisted_prism(n: int, twist: float, mirror: bool = False):
    """Two n-gons of one element, the lower twisted by ``twist`` radians: D_n, chiral, every atom equivalent.

    The hard case for a relabelling search: all 2n atoms have the same distance
    signature, so every atom is a candidate for every other, and the mirror
    image (``mirror=True``, a twist the other way) agrees with every distance
    yet no rotation superposes it.
    """
    atoms = []
    for k in range(n):
        a = 2.0 * math.pi * k / n
        atoms.append(("C", (2.0 * math.cos(a), 2.0 * math.sin(a), 0.75)))
    for k in range(n):
        a = 2.0 * math.pi * k / n + twist
        atoms.append(("C", (2.0 * math.cos(a), 2.0 * math.sin(a), -0.75)))
    if mirror:
        atoms = [(el, (x, -y, z)) for el, (x, y, z) in atoms]
    return atoms


# ---- a relabelled copy is found ---------------------------------------------------------------------------------


def test_water_listed_in_another_order_and_moved_is_matched():
    reordered = _moved_and_permuted(_WATER, seed=1)
    _assert_is_the_relabelling(_round(_WATER), reordered, _search(_round(_WATER), reordered))


@pytest.mark.parametrize("seed", range(12))
def test_ethanol_in_any_order_is_matched(seed):
    ethanol = _round(_embedded("CCO"))
    reordered = _moved_and_permuted(ethanol, seed=seed)
    _assert_is_the_relabelling(ethanol, reordered, _search(ethanol, reordered))


@pytest.mark.parametrize("seed", range(12))
def test_a_symmetric_methane_in_any_order_is_matched(seed):
    """Four equivalent hydrogens: several relabellings are valid and any one of them superposes."""
    methane = _round(_methane())
    reordered = _moved_and_permuted(methane, seed=seed)
    _assert_is_the_relabelling(methane, reordered, _search(methane, reordered))


@pytest.mark.parametrize("seed", range(12))
def test_methyl_hydrogens_exchanged_among_themselves_are_matched(seed):
    """Ethane: the same element sequence, only the three H of one methyl cycled. The same-order check would fail here."""
    ethane = _round(_staggered_ethane())
    reordered = _moved_and_permuted(ethane, seed=seed)
    _assert_is_the_relabelling(ethane, reordered, _search(ethane, reordered))
    cycled = list(ethane)
    cycled[2], cycled[3], cycled[4] = ethane[3], ethane[4], ethane[2]
    _assert_is_the_relabelling(ethane, cycled, _search(ethane, cycled))


def test_two_precisions_in_one_pair_are_matched():
    first = _round(_embedded("CCO"), 6)
    second = _moved_and_permuted(_embedded("CCO"), seed=4, decimals=8)
    tolerance = math.sqrt(3.0) / 2.0 * (1e-6 + 1e-8)
    _assert_is_the_relabelling(first, second, _search(first, second, tolerance=tolerance))


def test_a_linear_molecule_listed_in_another_order_is_matched():
    """No third anchor off the line exists; two anchors fix a linear molecule."""
    hcn = [("H", (0.0, 0.0, 0.0)), ("C", (0.0, 0.0, 1.064)), ("N", (0.0, 0.0, 2.220))]
    reordered = _moved_and_permuted(hcn, seed=2)
    _assert_is_the_relabelling(hcn, reordered, _search(hcn, reordered))


def test_a_diatomic_listed_in_another_order_is_matched():
    co = [("C", (0.0, 0.0, 0.0)), ("O", (0.0, 0.0, 1.128))]
    reordered = [co[1], co[0]]
    _assert_is_the_relabelling(co, _round(reordered), _search(co, _round(reordered)))


# ---- what must stay different -----------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_the_mirror_image_in_another_order_is_different(seed):
    """Enantiomers stay distinct: proper rotations only, whatever the atom order."""
    first = _round(_chbrclf())
    mirrored = _moved_and_permuted(_chbrclf(mirror=True), seed=seed)
    assert _search(first, mirrored).outcome == "different"
    # And the control: the same enantiomer, permuted and moved, matches.
    again = _moved_and_permuted(_chbrclf(), seed=seed)
    assert _search(first, again).outcome == "matched"


def test_a_chiral_symmetric_cluster_and_its_mirror_image_are_not_matched():
    """Every distance of the mirror image agrees, every atom is equivalent: only the proper-rotation test says no."""
    first = _round(_twisted_prism(5, 0.2))
    assert _search(first, _moved_and_permuted(_twisted_prism(5, 0.2), seed=1)).outcome == "matched"
    assert _search(first, _moved_and_permuted(_twisted_prism(5, 0.2, mirror=True), seed=1)).outcome == "different"


def test_a_real_conformer_pair_is_different_whatever_the_order():
    anti, gauche = _round(_butane(180.0)), _butane(60.0)
    found = _search(anti, _moved_and_permuted(gauche, seed=3))
    assert found.outcome == "different"
    assert found.fits == 0, "distinct conformers differ in their distance signatures: no fit should be attempted"


def test_a_rotamer_a_hundredth_of_a_degree_apart_is_different():
    near = _moved_and_permuted(_butane(180.01), seed=5)
    assert _search(_round(_butane(180.0)), near).outcome == "different"


def test_a_geometry_does_not_match_itself_with_one_atom_displaced_past_the_rounding():
    base = _embedded("CCO")
    nudged = [(el, (x, y + (1e-4 if i == 3 else 0.0), z)) for i, (el, (x, y, z)) in enumerate(base)]
    assert _search(_round(base), _moved_and_permuted(nudged, seed=2)).outcome == "different"


def test_different_atom_counts_by_element_are_different():
    methanol_like = _round(_embedded("CO"))
    assert _search(methanol_like, _moved_and_permuted(_embedded("CC"), seed=1)[: len(methanol_like)]).outcome == "different"


def test_the_same_shape_with_the_elements_in_other_places_is_different():
    """Same coordinates, same elements, but the centre atom is a different element: no relabelling puts O in F's place."""
    triangle = [("O", (0.0, 0.0, 0.0)), ("H", (0.96, 0.0, 0.0)), ("F", (-0.2976, 0.9127, 0.0))]
    # F, O, H with F at the apex of the same isosceles shape: only the element labels move.
    swapped = [("F", (0.0, 0.0, 0.0)), ("O", (0.96, 0.0, 0.0)), ("H", (-0.2976, 0.9127, 0.0))]
    assert _search(_round(triangle), _round(swapped)).outcome == "different"


def test_elements_are_never_exchanged():
    """A relabelled copy is accepted only with every atom keeping its element."""
    ethanol = _round(_embedded("CCO"))
    found = _search(ethanol, _moved_and_permuted(ethanol, seed=9))
    assert found.outcome == "matched"
    second = _moved_and_permuted(ethanol, seed=9)
    assert [second[j][0] for j in found.permutation] == [el for el, _ in ethanol]


# ---- isotopes ---------------------------------------------------------------------------------------------------


def _search_nuclides(first, first_nuclides, second, second_nuclides, tolerance: float = TOL):
    return find_matching_permutation(
        [c for _, c in first], first_nuclides, [c for _, c in second], second_nuclides, tolerance=tolerance
    )


def test_a_deuterium_and_a_hydrogen_are_never_mapped_onto_each_other():
    """Same coordinates; the one deuterium sits on the oxygen in one copy and on the methyl in the other."""
    ethanol = _round(_embedded("CCO"))
    elements = [el for el, _ in ethanol]
    hydrogens = [i for i, el in enumerate(elements) if el == "H"]
    # In _embedded("CCO") the hydroxyl H is the last atom; a methyl H is the first hydrogen.
    on_oxygen, on_carbon = hydrogens[-1], hydrogens[0]

    def labels(deuterated: int):
        return [(el, 2 if i == deuterated else (1 if el == "H" else None)) for i, el in enumerate(elements)]

    moved = _moved_and_permuted(ethanol, seed=0, permute=False)
    assert _search_nuclides(ethanol, labels(on_oxygen), moved, labels(on_carbon)).outcome == "different"
    assert _search_nuclides(ethanol, labels(on_oxygen), moved, labels(on_oxygen)).outcome == "matched"


def test_the_deuterium_on_either_of_two_equivalent_hydrogens_is_the_same_isotopologue():
    """CH3D: the D may sit on any of the three equivalent H, and the relabelling finds the one that superposes."""
    methane = _round(_methane())

    def labels(deuterated: int):
        return [("C", None)] + [("H", 2 if i == deuterated else 1) for i in range(4)]

    for second_site in range(4):
        found = _search_nuclides(methane, labels(0), methane, labels(second_site))
        assert found.outcome == "matched", second_site
        assert labels(second_site)[found.permutation[1]] == labels(0)[1]


def test_atoms_of_different_isotope_composition_are_different():
    methane = _round(_methane())
    plain = [("C", None)] + [("H", 1)] * 4
    one_d = [("C", None)] + [("H", 2)] + [("H", 1)] * 3
    assert _search_nuclides(methane, plain, methane, one_d).outcome == "different"


# ---- bounded cost -----------------------------------------------------------------------------------------------


def test_the_search_is_capped_not_exhaustive_on_a_large_chiral_symmetric_cluster():
    """198 equivalent atoms (396 fits to exhaust, 256 allowed) and a mirror image that every distance agrees with: the cost is bounded, and says so."""
    first = _round(_twisted_prism(99, 0.2))
    mirrored = _moved_and_permuted(_twisted_prism(99, 0.2, mirror=True), seed=1)
    started = time.perf_counter()
    found = _search(first, mirrored)
    elapsed = time.perf_counter() - started
    assert found.outcome == "capped", found
    assert found.fits <= prm.MAX_FITS_PER_PAIR
    assert elapsed < 5.0, f"the capped search took {elapsed:.1f} s"


def test_a_capped_search_is_not_a_match(monkeypatch):
    """With no fits allowed, a perfectly matching pair is reported capped, never matched."""
    monkeypatch.setattr(prm, "MAX_FITS_PER_PAIR", 0)
    reordered = _moved_and_permuted(_WATER, seed=1)
    assert _search(_round(_WATER), reordered).outcome == "capped"


def test_the_examined_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(prm, "MAX_EXAMINED_PER_PAIR", 1)
    ethane = _round(_staggered_ethane())
    assert _search(ethane, _moved_and_permuted(ethane, seed=1)).outcome == "capped"


def test_the_record_budget_is_shared_and_spent():
    budget = SearchBudget(fits_left=1)
    ethane = _round(_staggered_ethane())
    first = _search(ethane, _moved_and_permuted(ethane, seed=1), budget=budget)
    assert first.outcome == "matched"
    assert budget.fits_left == 0
    assert _search(ethane, _moved_and_permuted(ethane, seed=2), budget=budget).outcome == "capped"


def test_a_geometry_over_the_atom_cap_is_not_searched():
    chain = [("C", (1.5 * i, 0.3 * (i % 2), 0.0)) for i in range(prm.MAX_ATOMS + 1)]
    assert _search(chain, list(reversed(chain))).outcome == "capped"


def test_timing_of_a_typical_pair_is_milliseconds():
    """Cost of the common case: a 125-atom asymmetric molecule, permuted, is one fit."""
    alkane = _round(_embedded("C" * 40))
    reordered = _moved_and_permuted(alkane, seed=5)
    started = time.perf_counter()
    found = _search(alkane, reordered)
    elapsed = time.perf_counter() - started
    assert found.outcome == "matched"
    assert found.fits == 1
    assert elapsed < 1.0, f"{elapsed:.3f} s"


def test_unequal_inputs_are_different_not_an_error():
    assert find_matching_permutation([(0, 0, 0)], ["H"], [], [], tolerance=TOL).outcome == "different"
    assert find_matching_permutation([(0, 0, 0)], ["H"], [(0, 0, 0)], ["H"], tolerance=TOL).outcome == "different"


# ---- the final proper-rotation test is load-bearing --------------------------------------------------------------


def _puckered_chlorofluorobenzene(pucker: float, mirror: bool = False):
    """para-C6H4FCl flattened onto its plane, one ring carbon lifted ``pucker`` Angstrom out of it.

    The flat molecule is achiral; the lift makes it chiral by 0.002 A, and its
    mirror image (``mirror=True``) is the lift the other way.
    """
    atoms = _embedded("Fc1ccc(Cl)cc1")
    points = np.array([c for _, c in atoms])
    centred = points - points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(centred)
    flat = centred @ vt.T
    flat[:, 2] = 0.0
    ring_carbon = next(i for i, (el, _) in enumerate(atoms) if el == "C")
    flat[ring_carbon, 2] = pucker
    if mirror:
        flat[:, 2] *= -1.0
    return [(el, tuple(float(v) for v in row)) for (el, _), row in zip(atoms, flat, strict=True)]


def test_a_nearly_planar_chiral_geometry_is_not_matched_to_its_relabelled_mirror_image():
    """Every distance agrees and the anchors fit it; only the proper-rotation RMSD says the 0.002 A pucker is the wrong way."""
    first = _round(_puckered_chlorofluorobenzene(0.002))
    mirrored = _moved_and_permuted(_puckered_chlorofluorobenzene(0.002, mirror=True), seed=4)
    assert _search(first, mirrored).outcome == "different"
    again = _moved_and_permuted(_puckered_chlorofluorobenzene(0.002), seed=4)
    assert _search(first, again).outcome == "matched", "the control: the same pucker, shuffled, is the same structure"


# ---- a permutation is a bijection ------------------------------------------------------------------------------


def test_a_matched_permutation_is_always_a_bijection_even_under_a_huge_tolerance():
    """Two atoms 0.02 A apart would both be nearest to one image; the bijection test, not the RMSD, refuses that."""
    first = [("C", (0.0, 0.0, 0.0)), ("C", (0.02, 0.0, 0.0)), ("O", (3.0, 0.0, 0.0)), ("N", (0.0, 3.0, 0.0))]
    second = [("C", (0.0, 0.0, 0.0)), ("C", (-0.5, 0.0, 0.0)), ("O", (3.0, 0.0, 0.0)), ("N", (0.0, 3.0, 0.0))]
    found = _search(first, second, tolerance=1.0)
    if found.outcome == "matched":
        assert sorted(found.permutation) == [0, 1, 2, 3]


# ---- the filters agree with the verdict ------------------------------------------------------------------------


def test_a_move_inside_the_rms_tolerance_is_a_duplicate_in_either_atom_order():
    """One atom moved 2.5 x tolerance is an RMSD of about 0.7 x tolerance: one structure, in the given order or shuffled.

    A per-atom prefilter of 2 x tolerance would refuse the shuffled one while
    the same-order test accepts the other.
    """
    from app.services.calculation_levels import _AtomsOf, _same_polyatomic_structure

    base = _round(_butane(180.0))
    nudged = [(el, (x, y + (2.5 * TOL if i == 5 else 0.0), z)) for i, (el, (x, y, z)) in enumerate(base)]

    def atoms(geometry):
        return _AtomsOf(tuple((el, None) for el, _ in geometry), [c for _, c in geometry], 6)

    first = atoms(base)
    assert kabsch_rmsd(first.coordinates, atoms(nudged).coordinates) < TOL
    assert _same_polyatomic_structure(first, atoms(nudged)) is True
    order = list(range(len(nudged)))
    random.Random(2).shuffle(order)
    assert _same_polyatomic_structure(first, atoms([nudged[i] for i in order])) is True


# ---- cost of a record --------------------------------------------------------------------------------------------


def _fake_geometry(geometry_id: int, atoms):
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(atom_index=k + 1, element=el, x=x, y=y, z=z, isotope_mass_number=None)
        for k, (el, (x, y, z)) in enumerate(atoms)
    ]
    return SimpleNamespace(id=geometry_id, natoms=len(rows), atoms=rows)


def _random_atoms(rng, n: int):
    return [("C" if k % 3 else "H", tuple(float(v) for v in rng.normal(scale=6.0, size=3))) for k in range(n)]


def test_a_record_of_many_large_distinct_geometries_costs_a_bounded_amount(monkeypatch):
    """40 distinct 200-atom geometries, 780 pairs: the gate answers each in O(n^2), no candidate table is built."""
    from app.services import calculation_levels as levels

    spent: list = []
    real_budget = levels.SearchBudget

    def spying_budget():
        budget = real_budget()
        spent.append(budget)
        return budget

    monkeypatch.setattr(levels, "SearchBudget", spying_budget)
    rng = np.random.default_rng(0)
    geometries = [_fake_geometry(g, _random_atoms(rng, 200)) for g in range(40)]
    started = time.perf_counter()
    roots = levels._merge_rigidly_equal_geometries(geometries)
    elapsed = time.perf_counter() - started
    assert roots == {g.id: g.id for g in geometries}
    gate_units = 780 * (200 * 199 // 2)
    budget = spent[0]
    assert prm.MAX_WORK_PER_RECORD - budget.work_left == gate_units, "only the O(n^2) gate was charged: no pair got past it"
    assert budget.fits_left == prm.MAX_FITS_PER_RECORD
    assert elapsed < 2.0, f"{elapsed:.2f} s for 40 x 200 atoms (main: well under 1 s; before the gate: 8 s)"


def test_pairs_already_in_one_structure_are_not_compared_again(monkeypatch):
    """Ten relabelled copies of one geometry need nine joins, not forty-five comparisons."""
    from app.services import calculation_levels as levels

    calls = []
    real = levels._same_polyatomic_structure

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(levels, "_same_polyatomic_structure", counting)
    base = _random_atoms(np.random.default_rng(3), 30)
    geometries = []
    for g in range(10):
        order = list(range(30))
        random.Random(g).shuffle(order)
        geometries.append(_fake_geometry(g, [base[i] for i in order]))
    roots = levels._merge_rigidly_equal_geometries(geometries)
    assert len(set(roots.values())) == 1
    assert len(calls) == 9


def test_the_work_budget_caps_pairs_that_pass_the_gate(monkeypatch):
    """Copies that all pass the gate (relabelled duplicates of one 200-atom geometry) still stop at the budget."""
    from app.services import calculation_levels as levels

    base = _random_atoms(np.random.default_rng(1), 200)
    geometries = []
    for g in range(30):
        order = list(range(200))
        random.Random(g).shuffle(order)
        geometries.append(_fake_geometry(g, [base[i] for i in order]))
    monkeypatch.setattr(prm, "MAX_WORK_PER_RECORD", 3 * 200**3)
    started = time.perf_counter()
    roots = levels._merge_rigidly_equal_geometries(geometries)
    elapsed = time.perf_counter() - started
    assert len(set(roots.values())) > 1, "the budget ran out before every copy was joined: capped pairs are different"
    assert elapsed < 5.0
