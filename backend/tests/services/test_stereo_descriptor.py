"""The entry stereo descriptor (DR-0038): perception, sources, symmetry, vocabulary.

No database. The resolution services and the migration are tested where a database
is: ``test_api_species_stereo_identity.py`` and ``tests/db/test_species_key_stereo_migration.py``.
"""

from __future__ import annotations

import random

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import (
    canonical_species_identity,
    derive_stereo_label_from_3d,
    identity_mol_from_smiles,
)
from app.db.models.common import StereoResolution
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from tests.services.test_sigma_vinyl_stereo import _PROPENYL_E, _PROPENYL_Z


def embed_xyz(smiles: str, *, seed: int = 7) -> str:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0, smiles
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol)
    return Chem.MolToXYZBlock(mol)


def shuffled(xyz: str, seed: int) -> str:
    lines = xyz.strip().splitlines()
    head, coords = lines[:2], lines[2:]
    random.Random(seed).shuffle(coords)
    return "\n".join(head + coords)


def frame(smiles: str) -> stereo.StereoFrame:
    return stereo.frame_from_mol(identity_mol_from_smiles(smiles))


def resolve(smiles: str, *, xyz: str | None = None, xyzs=(), label: str | None = None) -> stereo.ResolvedStereo:
    geometries = [g for g in (xyz, *xyzs) if g]
    return stereo.resolve_stereo(frame(smiles), geometries=geometries, supplied_label=label)


def species_key(smiles: str) -> tuple[str, str]:
    return canonical_species_identity(SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1))


# ---------------------------------------------------------------------------
# The species key is the constitution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spellings",
    [
        ["C/C=C/C", "CC=CC", "C/C=C\\C", "C\\C=C\\C"],
        ["C[C@@H](O)CC", "C[C@H](O)CC", "CC(O)CC"],
        ["[H]/N=N\\[H]", "[H]/N=N/[H]", "N=N"],
        ["C/C=C/C=CC", "CC=CC=CC", "C/C=C\\C=C/C"],
        ["C[C@H]([2H])O", "CCO"],
    ],
)
def test_species_key_ignores_stereo_marks_and_isotopes(spellings: list[str]) -> None:
    assert len({species_key(smiles) for smiles in spellings}) == 1


def test_radical_marks_do_not_fork_the_species() -> None:
    assert species_key("[CH2]/C=C/C") == species_key("[CH2]C=CC")


def test_species_key_still_separates_constitutions() -> None:
    assert species_key("CC=CC") != species_key("C=CCC")


# ---------------------------------------------------------------------------
# E/Z and R/S come from CIP codes, never from cis/trans or the bond-stereo enum
# ---------------------------------------------------------------------------

_CASES = [
    ("CC=CC", embed_xyz("C/C=C/C"), "E"),
    ("CC=CC", embed_xyz("C/C=C\\C"), "Z"),
    ("[CH2]C=CC", embed_xyz("[CH2]/C=C/C"), "E"),
    ("CC(O)CC", embed_xyz("C[C@H](O)CC"), "S"),
    ("CC(O)CC", embed_xyz("C[C@@H](O)CC"), "R"),
    ("[CH]=CC", _PROPENYL_E, "E"),
    ("[CH]=CC", _PROPENYL_Z, "Z"),
]


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy_perception", "new_perception"])
@pytest.mark.parametrize(("smiles", "xyz", "expected"), _CASES)
def test_descriptor_is_the_same_under_legacy_and_new_stereo_perception(
    smiles: str, xyz: str, expected: str, legacy: bool
) -> None:
    previous = Chem.GetUseLegacyStereoPerception()
    Chem.SetUseLegacyStereoPerception(legacy)
    try:
        # Twelve atom orders: cis/trans from the bond-stereo enum is relative to whichever reference atoms RDKit
        # picks, so it flips with the order; a CIP code does not.
        got = {derive_stereo_label_from_3d(smiles, shuffled(xyz, seed)) for seed in range(12)}
        assert got == {expected}
    finally:
        Chem.SetUseLegacyStereoPerception(previous)


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy_perception", "new_perception"])
def test_marks_give_the_same_descriptor_under_legacy_and_new_perception(legacy: bool) -> None:
    previous = Chem.GetUseLegacyStereoPerception()
    Chem.SetUseLegacyStereoPerception(legacy)
    try:
        assert resolve("C/C=C/C").descriptor == "E"
        assert resolve("C/C=C\\C").descriptor == "Z"
        assert resolve("C[C@@H](O)CC").descriptor == "R"
        assert resolve("[CH2]/C=C/C").descriptor == "E"
    finally:
        Chem.SetUseLegacyStereoPerception(previous)


def test_sigma_vinyl_z_has_the_hydrogen_cis_to_the_methyl() -> None:
    """The CIP convention: the unpaired electron ranks lowest.

    So in Z-1-propenyl the H on C1 (which outranks the radical) is on the same side as CH3 on C2, and the radical
    orbital is trans to CH3. A name taken from the precursor halide gives the opposite letter.
    """
    assert resolve("[CH]=CC", xyz=_PROPENYL_Z).descriptor == "Z"
    assert resolve("[CH]=CC", xyz=_PROPENYL_E).descriptor == "E"


# ---------------------------------------------------------------------------
# B1: a chiral radical keeps its stereocentre
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("smiles", ["C[C@H](O)[CH2]", "C[C@H]([CH2])CC"])
def test_a_chiral_radical_keeps_its_stereocentre(smiles: str) -> None:
    constitution = smiles.replace("[C@H]", "C").replace("[C@@H]", "C")
    assert len(frame(constitution).elements) == 1, "CH3 and CH2. must not look the same to stereo perception"
    other = smiles.replace("[C@H]", "[C@@H]")
    mine, theirs = resolve(smiles), resolve(other)
    assert {mine.descriptor, theirs.descriptor} == {"R", "S"}
    # Geometry reads the same centre, and a supplied label is accepted for it.
    for marked in (smiles, other):
        xyz = embed_xyz(marked)
        assert resolve(constitution, xyz=xyz).descriptor == resolve(marked).descriptor
    assert resolve(constitution, label="R").descriptor == "R"


# ---------------------------------------------------------------------------
# B2: the descriptor is injective -- one string per stereoisomer
# ---------------------------------------------------------------------------

_INJECTIVE = [
    "OCC(O)C(O)C(O)C(O)CO",  # hexitols (galactitol, allitol, ...)
    "CC(O)C(O)C(O)C(C)O",  # hexane-2,3,4,5-tetrol
    "CC=CC=CC=CC=CC",  # deca-2,4,6,8-tetraene
    "CC(O)C(O)C(O)C(O)C(C)O",  # heptane-2,3,4,5,6-pentol: pseudo-asymmetric centres
    "CC(O)C(O)C(C)O",  # pentane-2,3,4-triol
    "C/C=C/C(O)C(O)/C=C/C",  # centres and bonds together
]


def _stereoisomers(constitution: str):
    mol = Chem.MolFromSmiles(constitution)
    options = StereoEnumerationOptions(onlyUnassigned=True, unique=True, tryEmbedding=False)
    return list(EnumerateStereoisomers(mol, options=options))


@pytest.mark.parametrize("constitution", _INJECTIVE)
def test_distinct_stereoisomers_get_distinct_descriptors_and_equal_ones_the_same(constitution: str) -> None:
    by_smiles: dict[str, set[str]] = {}
    by_descriptor: dict[str, set[str]] = {}
    for isomer in _stereoisomers(constitution):
        smiles = Chem.MolToSmiles(isomer)
        descriptor = stereo.descriptor_of_mol(Chem.MolFromSmiles(smiles))
        by_smiles.setdefault(smiles, set()).add(descriptor)
        by_descriptor.setdefault(descriptor, set()).add(smiles)
    assert len(by_smiles) > 1
    assert all(len(d) == 1 for d in by_smiles.values()), "one molecule, two descriptors"
    assert all(len(s) == 1 for s in by_descriptor.values()), f"two molecules share a descriptor: {by_descriptor}"
    assert len(by_smiles) == len(by_descriptor)


def test_the_known_counts() -> None:
    assert len({Chem.MolToSmiles(i) for i in _stereoisomers("OCC(O)C(O)C(O)C(O)CO")}) == 10
    assert len({Chem.MolToSmiles(i) for i in _stereoisomers("CC(O)C(O)C(O)C(C)O")}) == 10


def test_galactitol_and_allitol_differ() -> None:
    galactitol = "OC[C@H](O)[C@@H](O)[C@@H](O)[C@H](O)CO"
    allitol = "OC[C@H](O)[C@H](O)[C@H](O)[C@H](O)CO"
    assert Chem.MolToSmiles(Chem.MolFromSmiles(galactitol)) != Chem.MolToSmiles(Chem.MolFromSmiles(allitol))
    assert resolve(galactitol).descriptor != resolve(allitol).descriptor


def test_a_partial_descriptor_stays_distinct_from_every_full_one() -> None:
    partial = resolve("C/C=C/C=CC=CC=CC")
    assert partial.resolution == StereoResolution.partial
    full = {resolve(s).descriptor for s in ("C/C=C/C=C/C=C/C=C/C", "C/C=C/C=C/C=C/C=C\\C")}
    assert partial.descriptor not in full
    assert partial.descriptor == "E,?,?,?"


def test_symmetric_elements_have_one_descriptor_whichever_end_is_named() -> None:
    """(2E,4Z)- and (2Z,4E)-hexa-2,4-diene are one molecule."""
    assert resolve("C/C=C/C=C\\C").descriptor == resolve("C/C=C\\C=C\\C").descriptor == "E,Z"
    assert resolve("CC=CC=CC", xyz=embed_xyz("C/C=C\\C=C/C")).descriptor == "Z,Z"


def test_the_descriptor_does_not_depend_on_how_the_smiles_orders_its_atoms() -> None:
    """Both spell (E)-4-hydroxy... the same molecule from opposite ends."""
    assert resolve("C[C@@H](O)/C=C/C").descriptor == resolve("C/C=C/[C@H](O)C").descriptor
    assert resolve("C[C@@H](O)/C=C/C").descriptor != resolve("C[C@H](O)/C=C/C").descriptor


# ---------------------------------------------------------------------------
# Marks, geometry, label: order of authority and contradictions
# ---------------------------------------------------------------------------


def test_marks_alone_resolve_and_unmarked_is_unknown_not_null() -> None:
    assert resolve("C/C=C/C").descriptor == "E"
    unknown = resolve("CC=CC")
    assert (unknown.descriptor, unknown.resolution, unknown.label) == ("?", StereoResolution.unresolved, None)


def test_an_achiral_species_has_no_descriptor() -> None:
    assert resolve("CCO") == stereo.ResolvedStereo(None, None, None)


def test_geometry_fills_what_unmarked_smiles_leave_unknown() -> None:
    assert resolve("CC=CC", xyz=embed_xyz("C/C=C\\C")).descriptor == "Z"


def test_a_label_fills_what_geometry_and_marks_leave_unknown() -> None:
    assert resolve("CC=CC", label="E").descriptor == "E"


@pytest.mark.parametrize(
    ("smiles", "xyz", "label"),
    [
        ("C/C=C/C", embed_xyz("C/C=C\\C"), None),
        ("C[C@@H](O)CC", embed_xyz("C[C@H](O)CC"), None),
        ("CC=CC", embed_xyz("C/C=C\\C"), "E"),
        ("C/C=C/C", None, "Z"),
    ],
)
def test_contradictions_are_refused_in_every_direction(smiles: str, xyz: str | None, label: str | None) -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve(smiles, xyz=xyz, label=label)
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION


# ---------------------------------------------------------------------------
# B5: every conformer is a source
# ---------------------------------------------------------------------------


def test_a_second_conformer_deposited_as_the_other_isomer_is_a_contradiction() -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=CC", xyzs=(embed_xyz("C/C=C/C"), embed_xyz("C/C=C\\C")))
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION
    assert excinfo.value.context["sources"] == ["conformer geometry 1", "conformer geometry 2"]


def test_two_rotamers_of_e_2_pentene_both_give_e() -> None:
    xyzs = [embed_xyz("CC/C=C/C", seed=seed) for seed in (3, 11, 29, 77)]
    assert resolve("CCC=CC", xyzs=xyzs).descriptor == "E"
    for xyz in xyzs:
        assert resolve("CCC=CC", xyz=xyz).descriptor == "E"


# ---------------------------------------------------------------------------
# Partial elements, symmetry classes
# ---------------------------------------------------------------------------


def test_partial_marks_are_their_own_descriptor() -> None:
    partial = resolve("C/C=C/C=CC")
    full = resolve("C/C=C/C=C/C")
    assert (partial.descriptor, partial.resolution) == ("E,?", StereoResolution.partial)
    assert (full.descriptor, full.resolution) == ("E,E", StereoResolution.resolved)
    assert partial.stereo_smiles is None  # only a fully specified configuration is written


def test_a_label_is_checked_against_the_geometry_up_to_symmetry() -> None:
    assert resolve("CC=CC=CC", xyz=embed_xyz("C/C=C/C=C\\C"), label="Z,E").descriptor == "E,Z"
    with pytest.raises(CodedValueError):
        resolve("CC=CC=CC", xyz=embed_xyz("C/C=C/C=C\\C"), label="E,E")


def test_a_position_that_is_ambiguous_inside_a_symmetry_class_is_refused_and_says_so() -> None:
    # An E on an outer bond and an E on an inner bond of deca-2,4,6,8-tetraene can sit on either end.
    with pytest.raises(CodedValueError, match="ambiguous inside a symmetry class") as excinfo:
        resolve("C/C=C/C=CC=CC=CC", label="?,?,E,?")
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION


def test_the_diazene_spellings_resolve_to_one_entry() -> None:
    cis = embed_xyz("[H]/N=N\\[H]")
    assert resolve("N=N", xyz=cis).descriptor == resolve("[H]/N=N\\[H]").descriptor == "Z"
    assert resolve("N=N", xyz=cis).stereo_smiles == resolve("[H]/N=N\\[H]").stereo_smiles


def test_a_sigma_vinyl_radical_is_resolved_by_geometry_not_by_its_smiles() -> None:
    """Plain ``[CH]=CC`` cannot carry E/Z; the geometry is the only source. A SMILES is derived only because an
    explicit-hydrogen spelling happens to round-trip to the same descriptor, which is checked before it is stored."""
    e, z = resolve("[CH]=CC", xyz=_PROPENYL_E), resolve("[CH]=CC", xyz=_PROPENYL_Z)
    assert (e.descriptor, z.descriptor) == ("E", "Z")
    assert resolve("[CH]=CC").descriptor == "?"
    for resolved in (e, z):
        assert resolved.stereo_smiles is not None
        assert stereo.descriptor_of_mol(identity_mol_from_smiles(resolved.stereo_smiles)) == resolved.descriptor


def test_stereo_smiles_is_a_function_of_the_descriptor() -> None:
    assert resolve("CC=CC", xyz=embed_xyz("C/C=C/C")).stereo_smiles == resolve("C/C=C/C").stereo_smiles == "C/C=C/C"


# ---------------------------------------------------------------------------
# N1: pseudo-asymmetric centres, non-stereogenic elements, allenes, isotopes
# ---------------------------------------------------------------------------


def test_pentane_triol_has_one_descriptor_per_isomer_and_dashes_the_non_stereogenic_centre() -> None:
    descriptors = {
        Chem.MolToSmiles(i): resolve(Chem.MolToSmiles(i)) for i in _stereoisomers("CC(O)C(O)C(C)O")
    }
    assert len(descriptors) == 4
    assert len({r.descriptor for r in descriptors.values()}) == 4
    assert all(r.resolution == StereoResolution.resolved for r in descriptors.values()), "no permanent '?'"
    assert sum("-" in r.descriptor for r in descriptors.values()) == 2
    assert sum(("r" in r.descriptor or "s" in r.descriptor) for r in descriptors.values()) == 2


def test_a_label_for_a_configuration_that_cannot_exist_is_refused() -> None:
    # C3 of pentane-2,3,4-triol cannot be R when C2 and C4 are both R.
    for label in ("R,R,R", "S,S,R", "R,R,S"):
        with pytest.raises(CodedValueError) as excinfo:
            resolve("CC(O)C(O)C(C)O", label=label)
        assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


def test_pseudo_asymmetric_labels_are_in_the_vocabulary() -> None:
    marked = resolve("C[C@H](O)[C@@H](O)[C@@H](C)O")
    assert "r" in marked.descriptor or "s" in marked.descriptor
    assert resolve("CC(O)C(O)C(C)O", label=marked.descriptor).descriptor == marked.descriptor


def test_unknown_stays_unknown_when_the_configuration_does_not_decide_the_centre() -> None:
    partial = resolve("C[C@H](O)C(O)C(C)O")
    assert partial.resolution == StereoResolution.partial


def test_a_stereogenic_allene_is_refused_clearly() -> None:
    with pytest.raises(CodedValueError) as excinfo:
        frame("CC=C=CC")
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_UNSUPPORTED
    assert "two E/Z double bonds" in str(excinfo.value)


@pytest.mark.parametrize("smiles", ["C=C=C", "C=C=CC", "C=C=O", "CS(=O)(=O)C", "O=C=O"])
def test_a_cumulated_chain_that_is_not_stereogenic_contributes_no_element(smiles: str) -> None:
    assert frame(smiles).elements == ()


@pytest.mark.parametrize("smiles", ["FC1=C=N[N]N=N1", "FC1=C=[NH+][N-]N=N1"])
def test_a_cumulated_chain_closed_in_a_small_ring_is_not_an_element_and_is_not_refused(smiles: str) -> None:
    """A ring of fewer than eight members cannot take the linear geometry axial stereo needs, so such a deposit (it
    exists in the bulk reaction fixtures) is neither stereogenic nor refused."""
    assert frame(smiles).elements == ()


def test_isotopic_stereo_gets_a_descriptor_but_the_species_does_not() -> None:
    r, s = resolve("C[C@H]([2H])O"), resolve("C[C@@H]([2H])O")
    assert {r.descriptor, s.descriptor} == {"R", "S"}
    assert species_key("C[C@H]([2H])O") == species_key("CCO")
    assert r.stereo_smiles is None  # stripped of its isotope the SMILES would lose the stereo
    assert resolve("CCO").descriptor is None
    # Geometry tells which hydrogen is the deuterium only when the file labels its isotopes (GeometryPayload.isotopes).
    for marked, expected in (("C[C@H]([2H])O", r.descriptor), ("C[C@@H]([2H])O", s.descriptor)):
        mol = Chem.AddHs(Chem.MolFromSmiles(marked))
        assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
        xyz = Chem.MolToXYZBlock(mol)
        isotopes = {i + 1: a.GetIsotope() for i, a in enumerate(mol.GetAtoms()) if a.GetIsotope()}
        assert resolve("CC([2H])O", xyzs=[(xyz, isotopes)]).descriptor == expected
        assert resolve("CC([2H])O", xyz=xyz).descriptor == "?", "unlabelled geometry says nothing about an isotopic centre"


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["trans", "cis", "e", "R/S", "E;Z", "E,", "1"])
def test_free_text_is_refused_by_the_wire_schema(label: str) -> None:
    with pytest.raises(ValueError, match="stereo_label"):
        SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1, stereo_label=label)


@pytest.mark.parametrize("label", ["E", "R,S", "E,?", "?", "  Z  ", "R,r,S", "S,-,S"])
def test_descriptor_tokens_are_accepted_by_the_wire_schema(label: str) -> None:
    assert SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1, stereo_label=label)


@pytest.mark.parametrize(("smiles", "label"), [("CC=CC", "R"), ("CC=CC", "E,Z"), ("CCO", "E"), ("CC(O)CC", "E")])
def test_a_label_that_does_not_fit_the_molecule_is_refused(smiles: str, label: str) -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve(smiles, label=label)
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID
