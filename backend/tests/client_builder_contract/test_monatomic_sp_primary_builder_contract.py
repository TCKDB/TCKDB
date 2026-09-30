"""Contract test: the client builders anchor a single atom on its ``sp`` (#610).

``tckdb-client`` used to refuse any non-``opt`` primary calculation, so a
producer could not send an honest atom through the builders even after the
server accepted one. The builders now apply the schema's own rule to the
conformer geometry they will send: an ``sp`` primary is accepted for a
one-atom geometry and refused, with the atom count, for anything larger. The
payloads are validated against the backend schemas, so the two sides cannot
drift.
"""

from __future__ import annotations

import pytest

from tests._ci_dependency import require_module

# Skips locally without the client; fails on CI, which installs it (#575).
require_module("tckdb_client.builders", install="pip install -e clients/python")

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

from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)

_ORCA = SoftwareRelease(software="ORCA", version="6.0.0")
_LOT = LevelOfTheory(method="dlpno-ccsd(t)-f12", basis="cc-pvtz-f12")
_H = "1\nH atom\nH 0.0 0.0 0.0"
_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _sp(xyz: str, **kwargs) -> Calculation:
    return Calculation.sp(
        _ORCA, _LOT, input_geometry=Geometry.from_xyz(xyz),
        electronic_energy_hartree=-0.49994557, **kwargs,
    )


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-primary", "picked-primary"])
def test_species_builder_accepts_an_atoms_sp_primary(explicit):
    sp = _sp(_H)
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2),
        calculations=[sp],
        **({"primary_calculation": sp} if explicit else {}),
    )
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    (conformer,) = validated.conformers
    assert conformer.primary_calculation.type.value == "sp"
    assert conformer.additional_calculations == []


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-primary", "picked-primary"])
def test_species_builder_refuses_a_molecules_sp_primary(explicit):
    sp = _sp(_H2)
    with pytest.raises(TCKDBBuilderValidationError) as exc:
        ComputedSpeciesUpload(
            species=Species(smiles="[H][H]", charge=0, multiplicity=1),
            calculations=[sp],
            **({"primary_calculation": sp} if explicit else {}),
        )
    assert "this geometry has 2 atoms" in str(exc.value)


def test_species_builder_still_needs_some_primary():
    freq = Calculation.freq(_ORCA, _LOT, input_geometry=Geometry.from_xyz(_H), n_imag=0)
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=Species(smiles="[H]", charge=0, multiplicity=2), calculations=[freq]
        )


def _atom_plus_methyl_upload(atom_calcs, atom_xyz=_H) -> ComputedReactionUpload:
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ch4_opt = Calculation.opt(
        _ORCA, _LOT,
        output_geometry=Geometry.from_xyz("5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"),
        converged=True, final_energy_hartree=-40.5,
    )
    ch3_opt = Calculation.opt(
        _ORCA, _LOT,
        output_geometry=Geometry.from_xyz("4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0"),
        converged=True, final_energy_hartree=-39.7,
    )
    rxn = ChemReaction(reactants=[ch3, h], products=[ch4], family="H_Abstraction")
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[],
        species_calculations={ch4: [ch4_opt], ch3: [ch3_opt], h: atom_calcs(atom_xyz)},
    )


def test_reaction_builder_accepts_an_atoms_sp_primary():
    upload = _atom_plus_methyl_upload(lambda xyz: [_sp(xyz)])
    validated = ComputedReactionUploadRequest.model_validate(upload.to_payload())
    by_smiles = {sp.species_entry.smiles: sp for sp in validated.species}
    (conformer,) = by_smiles["[H]"].conformers
    assert conformer.calculation.type.value == "sp"
    assert by_smiles["[H]"].calculations == []
    assert by_smiles["C"].conformers[0].calculation.type.value == "opt"


def test_reaction_builder_refuses_a_molecules_sp_primary():
    with pytest.raises(TCKDBBuilderValidationError) as exc:
        _atom_plus_methyl_upload(lambda xyz: [_sp(xyz)], atom_xyz=_H2).to_payload()
    assert "this geometry has 2 atoms" in str(exc.value)
