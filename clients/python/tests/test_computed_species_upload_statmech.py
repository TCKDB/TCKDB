"""``ComputedSpeciesUpload`` × ``statmech`` integration tests (Phase 4).

Verifies the new ``statmech`` kwarg, source-calculation key resolution,
deterministic emission, and that the absence of the kwarg preserves
Phase-1 / Phase-3C behaviour byte-for-byte.
"""

from __future__ import annotations

import json

import pytest

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    Statmech,
    TCKDBBuilderValidationError,
    Thermo,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def water_geom() -> Geometry:
    return Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )


@pytest.fixture
def water_species() -> Species:
    return Species(smiles="O", charge=0, multiplicity=1, label="water")


@pytest.fixture
def calc_trio(water_geom):
    sr = _sr()
    lot = _lot()
    opt = Calculation.opt(
        sr, lot, output_geometry=water_geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    freq = Calculation.freq(
        sr, lot, input_geometry=water_geom,
        n_imag=0, zpe_hartree=0.0214, depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        sr, lot, input_geometry=water_geom,
        electronic_energy_hartree=-76.45, depends_on=opt, label="sp",
    )
    return opt, freq, sp


def _build_upload(
    water_species, calc_trio, *, statmech: Statmech, thermo: Thermo | None = None,
) -> ComputedSpeciesUpload:
    opt, freq, sp = calc_trio
    return ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=thermo,
        statmech=statmech,
    )


# ----- API acceptance ------------------------------------------------


def test_statmech_block_emitted(water_species, calc_trio):
    opt, freq, sp = calc_trio
    sm = Statmech(
        external_symmetry=2, point_group="C2v", is_linear=False,
        rigid_rotor_kind="asymmetric_top", statmech_treatment="rrho",
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    payload = _build_upload(water_species, calc_trio, statmech=sm).to_payload()
    assert "statmech" in payload
    sm_block = payload["statmech"]
    assert sm_block["external_symmetry"] == 2
    assert sm_block["point_group"] == "C2v"
    assert sm_block["is_linear"] is False
    assert sm_block["rigid_rotor_kind"] == "asymmetric_top"
    assert sm_block["statmech_treatment"] == "rrho"


def test_statmech_must_be_statmech_builder(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt],
            primary_calculation=opt,
            statmech="not a statmech",  # type: ignore[arg-type]
        )


# ----- source_calculations key resolution ----------------------------


def test_source_calcs_resolve_to_local_keys(water_species, calc_trio):
    opt, freq, sp = calc_trio
    sm = Statmech(
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    payload = _build_upload(water_species, calc_trio, statmech=sm).to_payload()
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    additional_keys = [
        c["key"] for c in payload["conformers"][0]["additional_calculations"]
    ]
    seen = [
        s["calculation_key"]
        for s in payload["statmech"]["source_calculations"]
    ]
    assert seen == [primary_key, additional_keys[0], additional_keys[1]]
    roles = [s["role"] for s in payload["statmech"]["source_calculations"]]
    assert roles == ["opt", "freq", "sp"]


def test_source_calc_outside_upload_rejected(water_species, calc_trio):
    opt, freq, _sp = calc_trio
    floating = Calculation.sp(
        _sr(), _lot(),
        input_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        electronic_energy_hartree=-1.0,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt, freq],
            primary_calculation=opt,
            statmech=Statmech(source_calculations={"sp": floating}),
        )


def test_statmech_optional_keeps_payload_shape(water_species, calc_trio):
    opt, freq, _sp = calc_trio
    payload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq], primary_calculation=opt,
    ).to_payload()
    assert "statmech" not in payload


def test_thermo_and_statmech_coexist(water_species, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    sm = Statmech(
        external_symmetry=2, point_group="C2v",
        source_calculations=[("freq", freq)],
    )
    payload = _build_upload(
        water_species, calc_trio, statmech=sm, thermo=thermo,
    ).to_payload()
    assert "thermo" in payload
    assert "statmech" in payload
    # Both reference the same bundle calc-key namespace.
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    add_keys = [
        c["key"] for c in payload["conformers"][0]["additional_calculations"]
    ]
    expected_keys = {primary_key, *add_keys}
    for ref in payload["thermo"]["source_calculations"]:
        assert ref["calculation_key"] in expected_keys
    for ref in payload["statmech"]["source_calculations"]:
        assert ref["calculation_key"] in expected_keys


# ----- determinism ----------------------------------------------------


def test_to_payload_deterministic(water_species, calc_trio):
    opt, freq, _sp = calc_trio
    sm = Statmech(
        external_symmetry=2, point_group="C2v",
        source_calculations=[("freq", freq)],
    )
    upload = _build_upload(water_species, calc_trio, statmech=sm)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
