"""Contract test: ``tckdb-client`` Phase-1 builder payloads must
validate against the backend's ``ComputedSpeciesUploadRequest`` schema.

This test lives in the backend test tree on purpose: it imports both
the client builder layer AND the backend schemas to assert that the
two sides stay aligned. The client package itself remains
backend-independent — moving this file under ``clients/python/tests/``
would force the client wheel to depend on the backend.

If this test fails after a backend schema change, the builder layer
must be updated to match the new wire shape. If it fails after a
builder change, regenerate the payload via ``to_payload()`` and
re-validate.
"""

from __future__ import annotations

import pytest

# The client wheel is installed in tckdb_env; if it isn't, skip the
# contract test rather than failing the entire backend suite.
pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)

from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)


@pytest.fixture
def water_upload() -> ComputedSpeciesUpload:
    g_in = Geometry.from_xyz(
        "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
    )
    g_out = Geometry.from_xyz(
        "3\nwater\nO 0.0 0.0 0.119\nH 0.0 0.756 -0.468\nH 0.0 -0.756 -0.468"
    )
    sr = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
    lot = LevelOfTheory(method="B3LYP", basis="6-31G(d)")
    opt = Calculation.opt(
        sr,
        lot,
        input_geometry=g_in,
        output_geometry=g_out,
        final_energy_hartree=-76.4,
        converged=True,
        label="opt water",
    )
    freq = Calculation.freq(
        sr,
        lot,
        input_geometry=g_out,
        n_imag=0,
        zpe_hartree=0.0214,
        depends_on=opt,
    )
    sp = Calculation.sp(
        sr,
        LevelOfTheory(method="CCSD(T)", basis="cc-pVTZ"),
        input_geometry=g_out,
        electronic_energy_hartree=-76.45,
        depends_on=opt,
    )
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1),
        calculations=[opt, freq, sp],
        primary_calculation=opt,
    )


def test_builder_payload_validates_against_backend_schema(water_upload):
    """Pydantic-validate the builder payload via the real schema."""
    payload = water_upload.to_payload()
    validated = ComputedSpeciesUploadRequest.model_validate(payload)

    assert validated.species_entry.smiles == "O"
    assert len(validated.conformers) == 1

    conformer = validated.conformers[0]
    assert conformer.primary_calculation.type.value == "opt"
    assert len(conformer.additional_calculations) == 2

    # Calc-key uniqueness across the bundle (mirrors the server's own
    # validator).
    all_keys = [conformer.primary_calculation.key] + [
        c.key for c in conformer.additional_calculations
    ]
    assert len(set(all_keys)) == len(all_keys)

    # Dependency role assignment maps to the backend enum value.
    freq_calc = next(
        c for c in conformer.additional_calculations if c.type.value == "freq"
    )
    sp_calc = next(
        c for c in conformer.additional_calculations if c.type.value == "sp"
    )
    assert [d.role.value for d in freq_calc.depends_on] == ["freq_on"]
    assert [d.role.value for d in sp_calc.depends_on] == ["single_point_on"]


def test_builder_payload_is_deterministic_under_schema_validation(water_upload):
    """Two ``to_payload()`` calls must validate identically."""
    p1 = water_upload.to_payload()
    p2 = water_upload.to_payload()
    v1 = ComputedSpeciesUploadRequest.model_validate(p1)
    v2 = ComputedSpeciesUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")
