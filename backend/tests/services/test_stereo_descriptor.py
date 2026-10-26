"""The entry stereo descriptor (DR-0038): perception, sources, vocabulary.

No database. The resolution services and the migration are tested where a database
is: ``test_species_stereo_identity.py`` and ``tests/db/test_species_key_stereo_migration.py``.
"""

from __future__ import annotations

import random

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import (
    canonical_species_identity,
    derive_stereo_label_from_3d,
    identity_mol_from_smiles,
    strip_isotopes,
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
    return stereo.frame_from_mol(strip_isotopes(identity_mol_from_smiles(smiles)))


def resolve(smiles: str, *, xyz: str | None = None, label: str | None = None) -> stereo.ResolvedStereo:
    return stereo.resolve_stereo(frame(smiles), geometry_xyz=xyz, supplied_label=label)


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
    ],
)
def test_species_key_ignores_stereo_marks(spellings: list[str]) -> None:
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
        # The marks path reads CIP too, so a marked SMILES gives the same descriptor under both perceptions.
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


def test_partial_marks_are_their_own_descriptor() -> None:
    partial = resolve("C/C=C/C=CC")
    full = resolve("C/C=C/C=C/C")
    assert (partial.descriptor, partial.resolution) == ("E,?", StereoResolution.partial)
    assert (full.descriptor, full.resolution) == ("E,E", StereoResolution.resolved)
    assert partial.stereo_smiles is None  # only a fully specified configuration is written


def test_symmetric_elements_have_one_descriptor_whichever_end_is_named() -> None:
    """(2E,4Z)- and (2Z,4E)-hexa-2,4-diene are one molecule."""
    assert resolve("C/C=C/C=C\\C").descriptor == resolve("C/C=C\\C=C\\C").descriptor == "E,Z"
    assert resolve("CC=CC=CC", xyz=embed_xyz("C/C=C\\C=C/C")).descriptor == "Z,Z"


def test_a_label_is_checked_against_the_geometry_per_symmetry_class() -> None:
    assert resolve("CC=CC=CC", xyz=embed_xyz("C/C=C/C=C\\C"), label="Z,E").descriptor == "E,Z"
    with pytest.raises(CodedValueError):
        resolve("CC=CC=CC", xyz=embed_xyz("C/C=C/C=C\\C"), label="E,E")


def test_two_rotamers_of_e_2_pentene_both_give_e() -> None:
    assert {resolve("CCC=CC", xyz=embed_xyz("CC/C=C/C", seed=seed)).descriptor for seed in (3, 11, 29, 77)} == {"E"}


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
        assert stereo.descriptor_of_mol(strip_isotopes(identity_mol_from_smiles(resolved.stereo_smiles))) == resolved.descriptor


def test_stereo_smiles_is_a_function_of_the_descriptor() -> None:
    assert resolve("CC=CC", xyz=embed_xyz("C/C=C/C")).stereo_smiles == resolve("C/C=C/C").stereo_smiles == "C/C=C/C"


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["trans", "cis", "e", "R/S", "E;Z", "E,", "1"])
def test_free_text_is_refused_by_the_wire_schema(label: str) -> None:
    with pytest.raises(ValueError, match="stereo_label"):
        SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1, stereo_label=label)


@pytest.mark.parametrize("label", ["E", "R,S", "E,?", "?", "  Z  "])
def test_descriptor_tokens_are_accepted_by_the_wire_schema(label: str) -> None:
    assert SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1, stereo_label=label)


@pytest.mark.parametrize(("smiles", "label"), [("CC=CC", "R"), ("CC=CC", "E,Z"), ("CCO", "E"), ("CC(O)CC", "E")])
def test_a_label_that_does_not_fit_the_molecule_is_refused(smiles: str, label: str) -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve(smiles, label=label)
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID
