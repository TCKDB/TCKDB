"""``LevelOfTheory.spin_treatment`` maps to the wire field and is never inferred."""

from __future__ import annotations

import pytest
from tckdb_schemas.enums import SpinTreatment
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    TCKDBBuilderValidationError,
)


@pytest.mark.parametrize("token", [m.value for m in SpinTreatment])
def test_every_schema_token_is_sent_verbatim_and_validates(token):
    payload = LevelOfTheory(method="b3lyp", basis="def2-tzvp", spin_treatment=token).to_payload()
    assert payload["spin_treatment"] == token
    assert LevelOfTheoryRef(**payload).spin_treatment == SpinTreatment(token)


def test_unstated_spin_treatment_is_absent_even_for_an_unrestricted_looking_method():
    payload = LevelOfTheory(method="UB3LYP", basis="def2-tzvp").to_payload()
    assert "spin_treatment" not in payload
    assert set(payload) == {"method", "basis"}


def test_the_level_does_not_infer_it_from_multiplicity_in_a_full_upload():
    geom = Geometry.from_xyz("1\nH\nH 0 0 0")
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2-tzvp"),
        output_geometry=geom,
        converged=True,
    )
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2), calculations=[opt], primary_calculation=opt
    )
    lot = upload.to_payload()["conformers"][0]["primary_calculation"]["level_of_theory"]
    assert "spin_treatment" not in lot


def test_the_stated_value_reaches_the_calculation_payload():
    geom = Geometry.from_xyz("1\nH\nH 0 0 0")
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2-tzvp", spin_treatment="unrestricted"),
        output_geometry=geom,
        converged=True,
    )
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2), calculations=[opt], primary_calculation=opt
    )
    lot = upload.to_payload()["conformers"][0]["primary_calculation"]["level_of_theory"]
    assert lot["spin_treatment"] == "unrestricted"


@pytest.mark.parametrize("bad", ["Unrestricted", "UNRESTRICTED", "ro", "restricted-open", "open_shell"])
def test_a_token_the_schema_does_not_define_is_refused_locally(bad):
    with pytest.raises(TCKDBBuilderValidationError):
        LevelOfTheory(method="b3lyp", spin_treatment=bad)


def test_an_empty_value_is_refused():
    with pytest.raises(TCKDBBuilderValidationError):
        LevelOfTheory(method="b3lyp", spin_treatment="  ")
