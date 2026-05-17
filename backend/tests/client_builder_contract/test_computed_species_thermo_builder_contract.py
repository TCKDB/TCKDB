"""Contract test for the Phase-3C ``ComputedSpeciesUpload.thermo`` field.

Imports both the client builder layer and the backend schema; verifies
the wire shape the builder emits for thermo (scalar / NASA / points)
validates against ``ComputedSpeciesUploadRequest`` and that
``source_calculations`` resolve to bundle-local keys defined elsewhere
in the same upload.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (  # noqa: E402
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    Thermo,
)

from app.schemas.workflows.computed_species_upload import (  # noqa: E402
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


def _upload_with(thermo: Thermo, water: Species, calc_trio) -> ComputedSpeciesUpload:
    opt, freq, sp = calc_trio
    return ComputedSpeciesUpload(
        species=water,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=thermo,
    )


# ---------------------------------------------------------------------
# Per-representation validation
# ---------------------------------------------------------------------


def test_scalar_thermo_payload_validates(water, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        tmin_k=200, tmax_k=2000,
        source_calculations={"opt": opt, "freq": freq, "sp": sp},
    )
    upload = _upload_with(thermo, water, calc_trio)
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    assert validated.thermo is not None
    assert validated.thermo.h298_kj_mol == -241.8
    assert validated.thermo.s298_j_mol_k == 188.8
    assert validated.thermo.nasa is None
    assert validated.thermo.points == []
    assert {sc.role.value for sc in validated.thermo.source_calculations} == {
        "opt", "freq", "sp",
    }


def test_nasa_thermo_payload_validates(water, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.nasa(
        coeffs_low=[0.5, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0],
        coeffs_high=[0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-241.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(thermo, water, calc_trio)
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    assert validated.thermo is not None
    assert validated.thermo.nasa is not None
    assert validated.thermo.nasa.t_low == 200
    assert validated.thermo.nasa.t_mid == 1000
    assert validated.thermo.nasa.t_high == 5000
    assert validated.thermo.nasa.a1 == 0.5
    assert validated.thermo.nasa.a2 == 0.1
    assert validated.thermo.nasa.b1 == 0.5
    assert validated.thermo.nasa.b2 == 0.2


def test_points_thermo_payload_validates(water, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.points(
        [
            {"temperature_k": 298.15, "cp_j_mol_k": 33.6, "h_kj_mol": 0.0,
             "s_j_mol_k": 188.8},
            {"temperature_k": 500.0, "cp_j_mol_k": 35.2, "h_kj_mol": 10.0},
        ],
        tmin_k=200, tmax_k=1000,
        source_calculations={"sp": sp},
    )
    upload = _upload_with(thermo, water, calc_trio)
    validated = ComputedSpeciesUploadRequest.model_validate(upload.to_payload())
    assert validated.thermo is not None
    assert len(validated.thermo.points) == 2
    assert validated.thermo.points[0].temperature_k == 298.15
    assert validated.thermo.points[0].cp_j_mol_k == 33.6


# ---------------------------------------------------------------------
# source_calculations survival
# ---------------------------------------------------------------------


def test_source_calculations_survive_round_trip(water, calc_trio):
    """Resolved ``calculation_key`` values must round-trip through
    Pydantic validation and ``model_dump``."""
    opt, freq, sp = calc_trio
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(thermo, water, calc_trio)
    payload = upload.to_payload()

    validated = ComputedSpeciesUploadRequest.model_validate(payload)
    dumped = validated.model_dump(mode="json")

    # The keys are whatever the upload-level minter assigned —
    # ``opt`` / ``freq`` / ``sp`` here because each calc was given a
    # label that slugifies to its role.
    expected_pairs = {
        ("opt", "opt"),
        ("freq", "freq"),
        ("sp", "sp"),
    }
    dumped_pairs = {
        (sc["calculation_key"], sc["role"])
        for sc in dumped["thermo"]["source_calculations"]
    }
    assert dumped_pairs == expected_pairs


def test_to_payload_twice_is_byte_stable_post_validation(water, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.nasa(
        coeffs_low=[0.5] + [0.0] * 6,
        coeffs_high=[0.5] + [0.0] * 6,
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-241.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    upload = _upload_with(thermo, water, calc_trio)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    v1 = ComputedSpeciesUploadRequest.model_validate(p1)
    v2 = ComputedSpeciesUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")
