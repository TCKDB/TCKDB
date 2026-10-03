"""The builders anchor a single atom on its ``sp`` and nothing else (#610)."""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)
from tckdb_client.builders.validation import TCKDBBuilderValidationError

_ORCA = SoftwareRelease(software="ORCA", version="6.0.0")
_LOT = LevelOfTheory(method="dlpno-ccsd(t)-f12", basis="cc-pvtz-f12")
_H = "1\nH atom\nH 0.0 0.0 0.0"
_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _sp(xyz: str) -> Calculation:
    return Calculation.sp(
        _ORCA, _LOT, input_geometry=Geometry.from_xyz(xyz), electronic_energy_hartree=-0.5
    )


def test_an_atoms_sp_is_the_species_primary_and_is_sent_as_one():
    sp = _sp(_H)
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2), calculations=[sp]
    )
    assert upload.primary_calculation is sp
    (conformer,) = upload.to_payload()["conformers"]
    assert conformer["primary_calculation"]["type"] == "sp"
    assert conformer["additional_calculations"] == []


def test_a_molecules_sp_is_refused_with_the_atom_count():
    with pytest.raises(TCKDBBuilderValidationError, match="this geometry has 2 atoms"):
        ComputedSpeciesUpload(
            species=Species(smiles="[H][H]", charge=0, multiplicity=1),
            calculations=[_sp(_H2)],
        )


def test_an_opt_still_wins_over_an_sp_when_both_are_present():
    opt = Calculation.opt(
        _ORCA, _LOT, output_geometry=Geometry.from_xyz(_H2), converged=True,
        final_energy_hartree=-1.1,
    )
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H][H]", charge=0, multiplicity=1),
        calculations=[_sp(_H2), opt],
    )
    assert upload.primary_calculation is opt


def _reaction(atom_xyz: str) -> ComputedReactionUpload:
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")

    def opt(xyz: str) -> Calculation:
        return Calculation.opt(
            _ORCA, _LOT, output_geometry=Geometry.from_xyz(xyz), converged=True,
            final_energy_hartree=-1.0,
        )

    return ComputedReactionUpload(
        reaction=ChemReaction(reactants=[ch3, h], products=[ch4], family="H_Abstraction"),
        calculations=[],
        species_calculations={
            ch4: [opt("5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0")],
            ch3: [opt("4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0")],
            h: [_sp(atom_xyz)],
        },
    )


def test_reaction_builder_sends_an_atoms_sp_as_its_conformer_calculation():
    payload = _reaction(_H).to_payload()
    by_smiles = {s["species_entry"]["smiles"]: s for s in payload["species"]}
    assert by_smiles["[H]"]["conformers"][0]["calculation"]["type"] == "sp"
    assert by_smiles["[H]"]["calculations"] == []
    assert by_smiles["C"]["conformers"][0]["calculation"]["type"] == "opt"


def test_reaction_builder_refuses_a_molecules_sp_primary():
    with pytest.raises(TCKDBBuilderValidationError, match="this geometry has 2 atoms"):
        _reaction(_H2).to_payload()


# A species whose only calculation is an sp that declares no geometry has
# nothing to count, so the useful message is the one about the type: it needs
# an opt. The geometry message used to replace it (#623).


def _bare_sp() -> Calculation:
    return Calculation.sp(_ORCA, _LOT, electronic_energy_hartree=-1.1)


def test_a_species_whose_only_calculation_is_an_sp_without_geometry_is_told_it_needs_an_opt():
    with pytest.raises(TCKDBBuilderValidationError) as raised:
        ComputedSpeciesUpload(
            species=Species(smiles="[H][H]", charge=0, multiplicity=1),
            calculations=[_bare_sp()],
        )
    message = str(raised.value)
    assert "primary_calculation.type must be 'opt', got 'sp'" in message
    assert "anchors each conformer on an opt" in message
    assert "so the conformer carries a reference" not in message


def test_an_opt_primary_without_geometry_still_gets_the_geometry_message():
    opt = Calculation.opt(_ORCA, _LOT, converged=True, final_energy_hartree=-1.1)
    with pytest.raises(TCKDBBuilderValidationError, match="must declare output_geometry or input_geometry"):
        ComputedSpeciesUpload(
            species=Species(smiles="[H][H]", charge=0, multiplicity=1),
            calculations=[opt],
        ).to_payload()


def test_a_reaction_species_whose_only_calculation_is_an_sp_without_geometry_is_told_it_needs_an_opt():
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    upload = ComputedReactionUpload(
        reaction=ChemReaction(reactants=[h], products=[h], family="H_Abstraction"),
        calculations=[],
        species_calculations={h: [_bare_sp()]},
    )
    with pytest.raises(TCKDBBuilderValidationError, match="must contain at least one opt calculation"):
        upload.to_payload()
