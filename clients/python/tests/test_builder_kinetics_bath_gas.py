"""``Kinetics.modified_arrhenius(bath_gas=...)`` states the collider a rate was obtained for.

The builder judges what it can know with the same shared check the server applies, never infers a
bath gas, and names each component by the upload's species key.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)
from tckdb_client.builders.validation import TCKDBBuilderValidationError

_ORCA = SoftwareRelease(software="ORCA", version="6.0.0")
_LOT = LevelOfTheory(method="dlpno-ccsd(t)-f12", basis="cc-pvtz-f12")


def _opt(xyz: str) -> Calculation:
    return Calculation.opt(_ORCA, _LOT, output_geometry=Geometry.from_xyz(xyz), converged=True, final_energy_hartree=-1.0)


def _kinetics(**kwargs) -> Kinetics:
    return Kinetics.modified_arrhenius(A=1.0e10, A_units="cm3/mol/s", n=2.0, Ea=10.0, **kwargs)


def _species() -> tuple[Species, Species]:
    return (
        Species(smiles="[Ar]", charge=0, multiplicity=1, label="Ar"),
        Species(smiles="N#N", charge=0, multiplicity=1, label="N2"),
    )


def _upload(kinetics: Kinetics) -> ComputedReactionUpload:
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    return ComputedReactionUpload(
        reaction=ChemReaction(reactants=[ch3, h], products=[ch4], family="H_Abstraction", kinetics=[kinetics]),
        calculations=[],
        species_calculations={
            ch4: [_opt("5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0")],
            ch3: [_opt("4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0")],
            h: [Calculation.sp(_ORCA, _LOT, input_geometry=Geometry.from_xyz("1\nH\nH 0 0 0"), electronic_energy_hartree=-0.5)],
        },
    )


def test_bath_gas_is_sent_by_species_key_and_the_payload_validates():
    ar, n2 = _species()
    payload = _upload(
        _kinetics(pressure_context="pressure_dependent", bath_gas=[(ar, 0.25), (n2, 0.75)])
    ).to_payload()

    (fit,) = payload["kinetics"]
    assert fit["pressure_context"] == "pressure_dependent"
    keys = {s["species_entry"]["smiles"]: s["key"] for s in payload["species"]}
    assert fit["bath_gas"] == [
        {"species_key": keys["[Ar]"], "mole_fraction": 0.25},
        {"species_key": keys["N#N"], "mole_fraction": 0.75},
    ]
    validated = ComputedReactionUploadRequest.model_validate(payload)
    assert [bg.mole_fraction for bg in validated.kinetics[0].bath_gas] == [0.25, 0.75]


def test_a_record_that_states_no_bath_gas_sends_none():
    """Nothing is inferred: no ``bath_gas`` key, and no collider joins the species list."""
    payload = _upload(_kinetics()).to_payload()
    assert "bath_gas" not in payload["kinetics"][0]
    assert {s["species_entry"]["smiles"] for s in payload["species"]} == {"[H]", "[CH3]", "C"}


def test_a_bath_gas_at_the_high_pressure_limit_is_refused_with_the_server_code():
    ar, _ = _species()
    with pytest.raises(TCKDBBuilderValidationError, match="kinetics_bath_gas_at_high_pressure_limit"):
        _kinetics(pressure_context="high_p_limit", bath_gas=[(ar, 1.0)])


def test_mole_fractions_must_sum_to_one():
    ar, n2 = _species()
    with pytest.raises(TCKDBBuilderValidationError, match="kinetics_bath_gas_mole_fraction_sum"):
        _kinetics(bath_gas=[(ar, 0.5), (n2, 0.2)])


def test_the_same_species_builder_twice_is_refused():
    ar, _ = _species()
    with pytest.raises(TCKDBBuilderValidationError, match="kinetics_bath_gas_duplicate_species"):
        _kinetics(bath_gas=[(ar, 0.5), (ar, 0.5)])


@pytest.mark.parametrize("fraction", [0, -0.1, 1.5, True, "0.5"])
def test_a_component_outside_zero_to_one_is_refused(fraction):
    ar, _ = _species()
    with pytest.raises(TCKDBBuilderValidationError):
        _kinetics(bath_gas=[(ar, fraction)])


def test_an_apparent_rate_needs_its_pressure():
    with pytest.raises(TCKDBBuilderValidationError, match="requires pressure_bar"):
        _kinetics(pressure_context="apparent_at_pressure")
    assert _kinetics(pressure_context="apparent_at_pressure", pressure_bar=1.0).pressure_bar == 1.0
