"""Contract test for the Phase-4 ``ComputedSpeciesUpload.statmech`` field.

Imports both the client builder layer and the backend schema; verifies
the wire shape the builder emits for the statmech block validates
against ``ComputedSpeciesUploadRequest`` and that ``source_calculations``
resolve to bundle-local keys defined elsewhere in the same upload.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    Statmech,
)

from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def calc_trio():
    geom = Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )
    opt = Calculation.opt(
        _sr(), _lot(), output_geometry=geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    freq = Calculation.freq(
        _sr(), _lot(), input_geometry=geom,
        n_imag=0, zpe_hartree=0.0214, depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        _sr(), _lot(), input_geometry=geom,
        electronic_energy_hartree=-76.45, depends_on=opt, label="sp",
    )
    return opt, freq, sp


@pytest.fixture
def water() -> Species:
    return Species(smiles="O", charge=0, multiplicity=1, label="water")


def _upload_with(statmech: Statmech, water, calc_trio) -> ComputedSpeciesUpload:
    opt, freq, sp = calc_trio
    return ComputedSpeciesUpload(
        species=water,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        statmech=statmech,
    )


def test_statmech_payload_validates_against_backend_schema(water, calc_trio):
    opt, freq, sp = calc_trio
    sm = Statmech(
        external_symmetry=2, point_group="C2v", is_linear=False,
        rigid_rotor_kind="asymmetric_top", statmech_treatment="rrho",
        uses_projected_frequencies=False,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(sm, water, calc_trio)
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    assert validated.statmech is not None
    assert validated.statmech.external_symmetry == 2
    assert validated.statmech.point_group == "C2v"
    assert validated.statmech.is_linear is False
    assert validated.statmech.rigid_rotor_kind.value == "asymmetric_top"
    assert validated.statmech.statmech_treatment.value == "rrho"
    assert {(sc.calculation_key, sc.role.value)
            for sc in validated.statmech.source_calculations} == {
        ("opt", "opt"), ("freq", "freq"), ("sp", "sp"),
    }


def test_source_calculations_survive_round_trip(water, calc_trio):
    opt, freq, sp = calc_trio
    sm = Statmech(
        external_symmetry=1, point_group="C1",
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(sm, water, calc_trio)
    payload = upload.to_payload()
    validated = ComputedSpeciesUploadRequest.model_validate(payload)
    dumped = validated.model_dump(mode="json")
    pairs = {
        (sc["calculation_key"], sc["role"])
        for sc in dumped["statmech"]["source_calculations"]
    }
    assert pairs == {("opt", "opt"), ("freq", "freq"), ("sp", "sp")}


def test_to_payload_twice_is_byte_stable(water, calc_trio):
    opt, freq, sp = calc_trio
    sm = Statmech(
        external_symmetry=2, point_group="C2v",
        source_calculations=[("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(sm, water, calc_trio)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    v1 = ComputedSpeciesUploadRequest.model_validate(p1)
    v2 = ComputedSpeciesUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")


def test_statmech_without_source_calcs_still_validates(water, calc_trio):
    """An identity-only statmech block (no source calcs) is legal — the
    backend schema makes ``source_calculations`` optional."""
    sm = Statmech(external_symmetry=2, point_group="C2v")
    upload = _upload_with(sm, water, calc_trio)
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    assert validated.statmech is not None
    assert validated.statmech.source_calculations == []
