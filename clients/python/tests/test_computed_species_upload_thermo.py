"""``ComputedSpeciesUpload`` × ``thermo`` integration tests (Phase 3C).

Phase 3B left the computed-species path thermo-less. This module
exercises the newly-added ``thermo`` kwarg and the on-wire emission
of ``source_calculations`` with resolved bundle-local keys.
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
    """opt → freq, sp (both depend on opt) — the canonical thermo trio."""
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


# ----- API acceptance -------------------------------------------------


def test_accepts_scalar_thermo(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8,
            s298_j_mol_k=188.8,
            source_calculations={"opt": opt, "freq": freq, "sp": sp},
        ),
    )
    payload = upload.to_payload()
    assert payload["thermo"]["h298_kj_mol"] == -241.8
    assert payload["thermo"]["s298_j_mol_k"] == 188.8
    roles = [s["role"] for s in payload["thermo"]["source_calculations"]]
    assert roles == ["opt", "freq", "sp"]


def test_accepts_nasa_thermo(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.nasa(
            coeffs_low=[0.5] + [0.0] * 6,
            coeffs_high=[0.5] + [0.0] * 6,
            t_low=200, t_mid=1000, t_high=5000,
            h298_kj_mol=-241.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    payload = upload.to_payload()
    assert payload["thermo"]["nasa"]["t_low"] == 200.0
    assert payload["thermo"]["nasa"]["a1"] == 0.5
    assert payload["thermo"]["nasa"]["b7"] == 0.0


def test_accepts_points_thermo(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.points(
            [
                {"temperature_k": 298.15, "cp_j_mol_k": 33.6, "h_kj_mol": 0.0},
                {"temperature_k": 500.0, "cp_j_mol_k": 35.2},
            ],
            tmin_k=200, tmax_k=1000,
            source_calculations={"sp": sp},
        ),
    )
    payload = upload.to_payload()
    assert len(payload["thermo"]["points"]) == 2
    assert payload["thermo"]["points"][0]["temperature_k"] == 298.15


# ----- source_calculations key resolution ----------------------------


def test_source_calculations_resolve_to_local_keys(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    payload = upload.to_payload()
    # ``opt`` / ``freq`` / ``sp`` labels are slugified to those same keys.
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    additional_keys = [
        c["key"] for c in payload["conformers"][0]["additional_calculations"]
    ]
    expected_keys = [primary_key] + additional_keys
    seen_keys = [
        s["calculation_key"]
        for s in payload["thermo"]["source_calculations"]
    ]
    assert set(seen_keys) <= set(expected_keys)
    # And ordering follows the user's insertion order.
    assert seen_keys == [primary_key, additional_keys[0], additional_keys[1]]


def test_source_calc_outside_upload_rejected(water_species, calc_trio):
    opt, freq, _sp = calc_trio
    sr = _sr()
    lot = _lot()
    floating = Calculation.sp(
        sr, lot, input_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        electronic_energy_hartree=-1.0,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt, freq],
            primary_calculation=opt,
            thermo=Thermo.scalar(
                h298_kj_mol=-241.8,
                source_calculations={"sp": floating},
            ),
        )


def test_thermo_must_be_thermo_builder(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt],
            primary_calculation=opt,
            thermo="not a thermo",  # type: ignore[arg-type]
        )


def test_thermo_optional_keeps_existing_payload_shape(
    water_species, calc_trio
):
    """Without thermo, the emitted payload remains exactly Phase-1 shape."""
    opt, freq, _sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq],
        primary_calculation=opt,
    )
    payload = upload.to_payload()
    assert "thermo" not in payload


# ----- determinism + snapshot ----------------------------------------


def test_to_payload_deterministic(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.nasa(
            coeffs_low=[0.5] + [0.0] * 6,
            coeffs_high=[0.5] + [0.0] * 6,
            t_low=200, t_mid=1000, t_high=5000,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_snapshot_structural(water_species, calc_trio):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.nasa(
            coeffs_low=[0.5, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0],
            coeffs_high=[0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0],
            t_low=200, t_mid=1000, t_high=5000,
            h298_kj_mol=-241.8, s298_j_mol_k=188.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    payload = upload.to_payload()

    # Pre-thermo bundle shape unchanged.
    assert payload["species_entry"]["smiles"] == "O"
    assert payload["conformers"][0]["primary_calculation"]["type"] == "opt"
    assert [
        c["type"] for c in payload["conformers"][0]["additional_calculations"]
    ] == ["freq", "sp"]

    # Thermo block.
    thermo = payload["thermo"]
    assert thermo["h298_kj_mol"] == -241.8
    assert thermo["s298_j_mol_k"] == 188.8
    assert thermo["nasa"]["a2"] == 0.1
    assert thermo["nasa"]["b2"] == 0.2
    # source_calculations resolve to the same keys as the calc entries.
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    add_keys = [
        c["key"] for c in payload["conformers"][0]["additional_calculations"]
    ]
    assert [s["calculation_key"] for s in thermo["source_calculations"]] == [
        primary_key, add_keys[0], add_keys[1],
    ]
    assert [s["role"] for s in thermo["source_calculations"]] == [
        "opt", "freq", "sp",
    ]


# ----- duplicate-pair backend rule -----------------------------------


def test_duplicate_role_with_distinct_calcs_emits_both(water_species, calc_trio):
    """Backend rejects duplicates of ``(calculation_key, role)``, but
    multiple calcs sharing the same role are fine when they point at
    distinct keys."""
    opt, freq, sp = calc_trio
    # Two ``sp`` roles, two distinct calcs.
    sr = _sr()
    lot = _lot()
    sp2 = Calculation.sp(
        sr, lot, input_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        electronic_energy_hartree=-76.46, depends_on=opt, label="sp2",
    )
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp, sp2],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8,
            source_calculations={"sp": [sp, sp2]},
        ),
    )
    payload = upload.to_payload()
    sources = payload["thermo"]["source_calculations"]
    assert len(sources) == 2
    assert all(s["role"] == "sp" for s in sources)
    # The two calculation_keys differ — that's the backend uniqueness
    # rule on (key, role) pairs.
    assert sources[0]["calculation_key"] != sources[1]["calculation_key"]
