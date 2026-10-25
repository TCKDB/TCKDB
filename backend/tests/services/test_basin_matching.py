"""Symmetry-aware conformer-basin matching (``app.chemistry.basin_matching``).

Two geometries share a basin iff SOME graph symmetry of the molecule puts EVERY rotor within its own
tolerance. These tests pin the cases that motivated the rule, one by one:

* a planar aryl group twisted +0.5 and -0.5 degrees is one conformer (it was split in two);
* the two hydrogens of an -NH2 or a radical -CH2 are interchangeable (listing them in the other order
  was a split);
* one-hydrogen rotors (O-H, O-O-H) are basin identity (ethanol trans/gauche, syn/anti acetic acid and
  hydrogen-bonded rotamers were merged);
* a drift inside the tolerance does not split a basin and one outside it does;
* the symmetry search is bounded, deterministically.

The geometries are built with RDKit by setting one dihedral on an embedded structure. No quantum
chemistry is involved and none of these numbers is a claim about energies: the subject is only which
torsion differences the matcher calls "the same".

``CASES`` is the table of §6 cases, shared with the before/after comparison the pull request reports.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem import rdMolTransforms as T

from app.chemistry.basin_matching import (
    BasinMatchParams,
    build_symmetry_model,
    compare_basins,
    symmetry_model_for,
)
from app.services.conformer_resolution import TORSION_BASIN_PARAMS, compute_fingerprint_for_observation

PARAMS = dict(TORSION_BASIN_PARAMS)
MATCH = BasinMatchParams.from_scheme_params(PARAMS)

Atoms = tuple[tuple[str, float, float, float], ...]


def embed(smiles: str, seed: int = 7, *, optimise: bool = True) -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=seed)
    if optimise:
        AllChem.MMFFOptimizeMolecule(mol)
    return mol


def atoms_of(mol: Chem.Mol) -> Atoms:
    conf = mol.GetConformer()
    return tuple((a.GetSymbol(), *conf.GetAtomPosition(i)) for i, a in enumerate(mol.GetAtoms()))


def with_dihedral(smiles: str, smarts: str, degrees: float, *, seed: int = 7, optimise: bool = True) -> Atoms:
    mol = embed(smiles, seed, optimise=optimise)
    quartet = mol.GetSubstructMatch(Chem.MolFromSmarts(smarts))
    assert len(quartet) == 4, f"{smarts} did not match {smiles}"
    T.SetDihedralDeg(mol.GetConformer(), *quartet, degrees)
    return atoms_of(mol)


def shuffled(atoms: Atoms, seed: int) -> Atoms:
    order = list(range(len(atoms)))
    random.Random(seed).shuffle(order)
    return tuple(atoms[i] for i in order)


def swap_hydrogens(atoms: Atoms, mol: Chem.Mol, heavy_symbol: str) -> Atoms:
    """The same geometry with the two hydrogens of the first ``heavy_symbol`` atom listed in the other order."""
    for atom in mol.GetAtoms():
        if atom.GetSymbol() != heavy_symbol:
            continue
        hs = [n.GetIdx() for n in atom.GetNeighbors() if n.GetSymbol() == "H"]
        if len(hs) == 2:
            out = list(atoms)
            out[hs[0]], out[hs[1]] = atoms[hs[1]], atoms[hs[0]]
            return tuple(out)
    raise AssertionError(f"no {heavy_symbol} with two hydrogens")


def compare(smiles: str, a: Atoms, b: Atoms, params: dict | None = None):
    scheme = dict(params or PARAMS)
    fa, ca = compute_fingerprint_for_observation(smiles, a, scheme)
    fb, cb = compute_fingerprint_for_observation(smiles, b, scheme)
    assert fa is not None and fb is not None
    return compare_basins(smiles, ca, cb, BasinMatchParams.from_scheme_params(scheme))


@dataclass(frozen=True)
class Case:
    name: str
    smiles: str
    a: Callable[[], Atoms]
    b: Callable[[], Atoms]
    same: bool


_ARYL = "c:c-[!#1;!c]~*"


def _ethanol(deg: float) -> Atoms:
    return with_dihedral("CCO", "[#1][OX2][CH2][CH3]", deg, seed=5)


CASES: list[Case] = [
    Case("nitrobenzene +0.5 vs -0.5", "c1ccccc1[N+](=O)[O-]",
         lambda: with_dihedral("c1ccccc1[N+](=O)[O-]", _ARYL, 0.5), lambda: with_dihedral("c1ccccc1[N+](=O)[O-]", _ARYL, -0.5), True),
    Case("benzaldehyde +0.5 vs -0.5", "c1ccccc1C=O",
         lambda: with_dihedral("c1ccccc1C=O", _ARYL, 0.5), lambda: with_dihedral("c1ccccc1C=O", _ARYL, -0.5), True),
    Case("anisole +0.5 vs -0.5", "c1ccccc1OC",
         lambda: with_dihedral("c1ccccc1OC", _ARYL, 0.5), lambda: with_dihedral("c1ccccc1OC", _ARYL, -0.5), True),
    Case("benzotrifluoride +0.5 vs -0.5 (CF3 top)", "c1ccccc1C(F)(F)F",
         lambda: with_dihedral("c1ccccc1C(F)(F)F", _ARYL, 0.5), lambda: with_dihedral("c1ccccc1C(F)(F)F", _ARYL, -0.5), True),
    Case("ethanol trans vs gauche", "CCO", lambda: _ethanol(180.0), lambda: _ethanol(60.0), False),
    Case("acetic acid syn vs anti", "CC(=O)O",
         lambda: with_dihedral("CC(=O)O", "[#1][OX2]C=O", 0.0), lambda: with_dihedral("CC(=O)O", "[#1][OX2]C=O", 180.0), False),
    Case("hydroxyacetone H-bonded vs free OH", "CC(=O)CO",
         lambda: with_dihedral("CC(=O)CO", "[#1][OX2][CH2][C;$(C=O)]", 0.0), lambda: with_dihedral("CC(=O)CO", "[#1][OX2][CH2][C;$(C=O)]", 180.0), False),
    Case("2-methoxyethanol H-bonded vs free OH", "COCCO",
         lambda: with_dihedral("COCCO", "[#1][OX2][CH2][CH2]", 60.0), lambda: with_dihedral("COCCO", "[#1][OX2][CH2][CH2]", 180.0), False),
    Case("ethyl hydroperoxide O-O-H rotamers", "CCOO",
         lambda: with_dihedral("CCOO", "[#1][OX2][OX2][CH2]", 80.0), lambda: with_dihedral("CCOO", "[#1][OX2][OX2][CH2]", 180.0), False),
    Case("ethylamine N-H atoms in swapped order", "CCN",
         lambda: atoms_of(embed("CCN", 3)), lambda: swap_hydrogens(atoms_of(embed("CCN", 3)), embed("CCN", 3), "N"), True),
    Case("12 degree drift on a heavy rotor (n-butane)", "CCCC",
         lambda: with_dihedral("CCCC", "[CH3][CH2][CH2][CH3]", 180.0), lambda: with_dihedral("CCCC", "[CH3][CH2][CH2][CH3]", 168.0), True),
    Case("25 degree drift on an O-H rotor", "CCO", lambda: _ethanol(180.0), lambda: _ethanol(155.0), True),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_section6_cases(case: Case) -> None:
    result = compare(case.smiles, case.a(), case.b())
    assert result.same_basin is case.same, (case.name, result)


# ---------------------------------------------------------------------------
# Symmetric tops: no discontinuity, no dependence on atom order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smiles", ["c1ccccc1[N+](=O)[O-]", "c1ccccc1C=O", "c1ccccc1OC", "c1ccccc1C(F)(F)F", "c1ccccc1C(=O)O"]
)
@pytest.mark.parametrize("twist", [0.5, 5.0, 89.5, 179.5])
def test_a_planar_or_near_planar_group_is_one_basin_across_the_wrap(smiles: str, twist: float) -> None:
    """+t and -t are the same conformer up to a relabelling of the ring, wherever on the circle t is."""
    a = with_dihedral(smiles, _ARYL, twist)
    b = with_dihedral(smiles, _ARYL, -twist)
    assert compare(smiles, a, b).same_basin


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_the_answer_does_not_depend_on_the_order_of_atoms_in_the_file(seed: int) -> None:
    smiles = "c1ccccc1[N+](=O)[O-]"
    a = with_dihedral(smiles, _ARYL, 0.5)
    b = with_dihedral(smiles, _ARYL, -0.5)
    assert compare(smiles, shuffled(a, seed), shuffled(b, seed + 100)).same_basin
    # and a real difference stays a difference under any ordering
    c = with_dihedral(smiles, _ARYL, 90.0)
    assert not compare(smiles, shuffled(a, seed), shuffled(c, seed + 100)).same_basin


def test_methyl_hydrogens_are_not_enumerated_and_a_120_degree_methyl_turn_is_one_basin() -> None:
    smiles = "Cc1ccccc1"
    mol = embed(smiles)
    a = atoms_of(mol)
    quartet = mol.GetSubstructMatch(Chem.MolFromSmarts("[#1][CH3]c:c"))
    T.SetDihedralDeg(mol.GetConformer(), *quartet, T.GetDihedralDeg(mol.GetConformer(), *quartet) + 120.0)
    assert compare(smiles, a, atoms_of(mol)).same_basin
    # The model enumerates heavy-atom symmetries only: toluene's ring flip, not 3! methyl-hydrogen orders.
    assert symmetry_model_for(smiles, MATCH).n_mappings == 2


@pytest.mark.parametrize(
    ("smiles", "heavy"),
    [("CCN", "N"), ("CC(N)=O", "N"), ("Nc1ccccc1", "N"), ("[CH2]CC", "C")],
    ids=["ethylamine (pyramidal)", "acetamide (planar)", "aniline", "propyl radical (CH2 top)"],
)
def test_the_two_hydrogens_of_an_nh2_or_ch2_radical_top_are_interchangeable(smiles: str, heavy: str) -> None:
    mol = embed(smiles, 3, optimise=smiles != "[CH2]CC")
    a = atoms_of(mol)
    if heavy == "C":
        # the radical carbon is the one with two hydrogens
        top = next(x for x in mol.GetAtoms() if x.GetSymbol() == "C" and sum(n.GetSymbol() == "H" for n in x.GetNeighbors()) == 2 and x.GetDegree() == 3)
        hs = [n.GetIdx() for n in top.GetNeighbors() if n.GetSymbol() == "H"]
        out = list(a)
        out[hs[0]], out[hs[1]] = a[hs[1]], a[hs[0]]
        swapped = tuple(out)
    else:
        swapped = swap_hydrogens(a, mol, heavy)
    result = compare(smiles, a, swapped)
    assert result.same_basin, result
    assert max(result.torsion_deltas) < 1e-6


def test_a_radical_ch2_top_has_a_real_rotor_and_two_orientations_are_two_basins() -> None:
    smiles = "[CH2]CC"
    assert any(s.top_kind == "two_h" for s in symmetry_model_for(smiles, MATCH).slots)
    a = with_dihedral(smiles, "[#1][CH2][CH2][CH3]", 0.0, optimise=False)
    b = with_dihedral(smiles, "[#1][CH2][CH2][CH3]", 90.0, optimise=False)
    assert not compare(smiles, a, b).same_basin


# ---------------------------------------------------------------------------
# Terminal-hydrogen rotors are identity; tolerances
# ---------------------------------------------------------------------------


def test_one_hydrogen_rotors_are_slots_and_methyl_is_not_double_counted() -> None:
    kinds = sorted(s.top_kind for s in symmetry_model_for("CCO", MATCH).slots)
    assert kinds == ["methyl", "single_h"]
    assert {s.top_kind for s in symmetry_model_for("CCOO", MATCH).slots} >= {"single_h"}


def test_the_both_terminal_rule_still_excludes_ethane_like_bonds() -> None:
    for smiles in ("CC", "CO", "OO", "CN"):
        assert symmetry_model_for(smiles, MATCH).rotor_count == 0, smiles


@pytest.mark.parametrize(
    ("degrees", "same"),
    [(19.5, True), (20.5, False)],
    ids=["inside the heavy-atom tolerance", "outside it"],
)
def test_the_heavy_atom_tolerance_is_20_degrees(degrees: float, same: bool) -> None:
    smiles = "CCCC"
    a = with_dihedral(smiles, "[CH3][CH2][CH2][CH3]", 180.0)
    b = with_dihedral(smiles, "[CH3][CH2][CH2][CH3]", 180.0 - degrees)
    assert compare(smiles, a, b).same_basin is same


@pytest.mark.parametrize(
    ("degrees", "same"),
    [(29.5, True), (30.5, False)],
    ids=["inside the terminal-hydrogen tolerance", "outside it"],
)
def test_the_terminal_hydrogen_tolerance_is_30_degrees(degrees: float, same: bool) -> None:
    assert compare("CCO", _ethanol(180.0), _ethanol(180.0 - degrees)).same_basin is same


def test_ordinary_distinct_heavy_atom_rotamers_stay_distinct() -> None:
    a = with_dihedral("CCCC", "[CH3][CH2][CH2][CH3]", 180.0)
    b = with_dihedral("CCCC", "[CH3][CH2][CH2][CH3]", 60.0)
    assert not compare("CCCC", a, b).same_basin


def test_a_rigid_molecule_falls_back_to_rmsd() -> None:
    mol = embed("c1ccccc1")
    a = atoms_of(mol)
    assert compare("c1ccccc1", a, a).is_rigid
    assert compare("c1ccccc1", a, a).same_basin
    stretched = tuple((s, 1.6 * x, 1.6 * y, 1.6 * z) for s, x, y, z in a)
    assert not compare("c1ccccc1", a, stretched).same_basin


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------

#: 2,2,3,3-tetramethylbutane: six methyls on two quaternary carbons.
_TETRAMETHYLBUTANE = "CC(C)(C)C(C)(C)C"


def test_a_tert_butyl_rich_molecule_stays_within_the_default_cap() -> None:
    model = symmetry_model_for(_TETRAMETHYLBUTANE, MATCH)
    assert model.n_mappings == 72  # 3! * 3! * 2
    assert not model.cap_hit
    mol = embed(_TETRAMETHYLBUTANE)
    a = atoms_of(mol)
    assert compare(_TETRAMETHYLBUTANE, a, shuffled(a, 11)).same_basin


def test_neopentane_symmetries_are_heavy_atom_only() -> None:
    """31,104 labelled mappings if hydrogens were permuted; 24 when only the four methyl carbons are."""
    assert symmetry_model_for("CC(C)(C)C", MATCH).n_mappings == 24


def test_the_cap_truncates_deterministically_and_says_so() -> None:
    capped = dict(PARAMS, max_symmetry_mappings=8)
    params = BasinMatchParams.from_scheme_params(capped)
    build_symmetry_model.cache_clear()
    first = symmetry_model_for(_TETRAMETHYLBUTANE, params)
    build_symmetry_model.cache_clear()
    second = symmetry_model_for(_TETRAMETHYLBUTANE, params)
    assert first.cap_hit and first.n_mappings == 8
    assert (first.images == second.images).all()
    # identity is always searched first, so an identical pair still matches
    mol = embed(_TETRAMETHYLBUTANE)
    a = atoms_of(mol)
    result = compare(_TETRAMETHYLBUTANE, a, a, capped)
    assert result.same_basin and result.symmetry_cap_hit and result.symmetry_mappings == 8
    fingerprint, _ = compute_fingerprint_for_observation(_TETRAMETHYLBUTANE, a, capped)
    assert fingerprint is not None
    assert fingerprint.to_dict()["symmetry_cap_hit"] is True
    build_symmetry_model.cache_clear()


def test_a_capped_search_can_only_split_never_merge() -> None:
    """A pair that needs a mapping beyond the cap is reported different; the cap never makes a pair the same."""
    smiles = "c1ccccc1C=O"
    # Coordinates straight from the embedding are already in species atom order, so no atom mapping can
    # quietly pick the flipped labelling: the pair genuinely needs the ring-flip symmetry.
    a = [(x, y, z) for _, x, y, z in with_dihedral(smiles, _ARYL, 90.0)]
    b = [(x, y, z) for _, x, y, z in with_dihedral(smiles, _ARYL, -90.0)]
    assert compare_basins(smiles, a, b, MATCH).same_basin
    identity_only = BasinMatchParams.from_scheme_params(dict(PARAMS, max_symmetry_mappings=1))
    result = compare_basins(smiles, a, b, identity_only)
    assert not result.same_basin
    assert result.symmetry_cap_hit
    build_symmetry_model.cache_clear()


# ---------------------------------------------------------------------------
# Existence, not min-max; rigid molecules do not depend on atom order
# ---------------------------------------------------------------------------


def _propanediol(central_1: float, central_2: float, oh_1: float, oh_2: float) -> Atoms:
    mol = embed("OCCCO", 11, optimise=False)
    conf = mol.GetConformer()
    T.SetDihedralDeg(conf, 0, 1, 2, 3, central_1)
    T.SetDihedralDeg(conf, 1, 2, 3, 4, central_2)
    for heavy, degrees, partner, far in ((0, oh_1, 1, 2), (4, oh_2, 3, 2)):
        hydrogen = next(n.GetIdx() for n in mol.GetAtomWithIdx(heavy).GetNeighbors() if n.GetSymbol() == "H")
        T.SetDihedralDeg(conf, hydrogen, heavy, partner, far, degrees)
    return atoms_of(mol)


def test_the_rule_is_existence_with_per_slot_tolerances_not_a_minimised_maximum() -> None:
    """1,3-propanediol: a 25 degree move of one O-H rotor, everything else equal.

    25 is over the 20 degree heavy-atom tolerance and inside the 30 degree O-H tolerance. A rule that minimised
    "the largest difference" against one number could not say which tolerance applies and would call these
    different; the existence rule checks every slot against its own tolerance.
    """
    smiles = "OCCCO"
    a = _propanediol(60.0, 81.0, 180.0, 200.0)
    b = _propanediol(60.0, 81.0, 205.0, 200.0)
    result = compare(smiles, a, b)
    assert result.same_basin
    assert sorted(round(d) for d in result.torsion_deltas) == [0, 0, 0, 25]
    # and the same 25 degrees on a heavy-atom rotor is a different basin
    c = _propanediol(85.0, 81.0, 180.0, 200.0)
    assert not compare(smiles, a, c).same_basin


@pytest.mark.parametrize(
    "smiles",
    ["CC", "[CH2]C", "NN", "CN", "c1ccccc1"],
    ids=["ethane", "ethyl radical", "hydrazine", "methylamine", "benzene"],
)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_rigid_molecule_is_the_same_basin_as_its_own_shuffled_copy(smiles: str, seed: int) -> None:
    """With no rotors the comparison is by RMSD, which must not depend on the order of atoms in the file."""
    atoms = atoms_of(embed(smiles, 5, optimise=smiles != "[CH2]C"))
    result = compare(smiles, atoms, shuffled(atoms, seed))
    assert result.is_rigid
    assert result.same_basin, result


def test_a_rigid_molecule_still_separates_a_genuinely_different_geometry() -> None:
    atoms = atoms_of(embed("NN", 5))
    stretched = tuple((s, 1.6 * x, 1.6 * y, 1.6 * z) for s, x, y, z in atoms)
    assert not compare("NN", atoms, shuffled(stretched, 2)).same_basin
