"""``ComputedSpeciesUpload`` × ``transport`` integration tests.

Phase-5 forward-compat contract: the builder accepts ``transport`` and
validates locally, but the computed-species bundle schema does NOT
yet carry a transport field, so nothing is emitted on the wire. These
tests pin that exact contract — the kwarg behaves consistently and
doesn't silently change the payload shape.
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
    Transport,
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
    opt = Calculation.opt(
        _sr(), _lot(), output_geometry=water_geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    freq = Calculation.freq(
        _sr(), _lot(), input_geometry=water_geom,
        n_imag=0, zpe_hartree=0.0214, depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        _sr(), _lot(), input_geometry=water_geom,
        electronic_energy_hartree=-76.45, depends_on=opt, label="sp",
    )
    return opt, freq, sp


def _build_upload(
    water_species, calc_trio, *, transport: Transport,
) -> ComputedSpeciesUpload:
    opt, freq, sp = calc_trio
    return ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        transport=transport,
    )


# --- API acceptance --------------------------------------------------


def test_accepts_transport(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    tr = Transport(
        sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        dipole_debye=1.85, polarizability_angstrom3=1.45,
        rotational_relaxation=4.0,
        source_calculations={"supporting_geometry": opt},
    )
    upload = _build_upload(water_species, calc_trio, transport=tr)
    # The builder stores the block locally.
    assert upload.transport is tr
    # Local validation captured the source-calc reference.
    assert (
        list(tr.source_calculations_iter())[0][0] == "supporting_geometry"
    )


def test_transport_must_be_transport_builder(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt],
            primary_calculation=opt,
            transport="not a transport",  # type: ignore[arg-type]
        )


def test_source_calc_outside_upload_rejected(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    floating = Calculation.sp(
        _sr(), _lot(),
        input_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        electronic_energy_hartree=-1.0,
    )
    tr = Transport(
        dipole_debye=0.1,
        source_calculations={"supporting_geometry": floating},
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt],
            primary_calculation=opt,
            transport=tr,
        )


# --- forward-compat payload contract --------------------------------


def test_transport_is_not_emitted_on_the_wire(water_species, calc_trio):
    """Until the bundle schema gains a ``transport`` field, the
    builder must NOT add anything to the payload. Any future schema
    change can flip on emission in one place."""
    opt, _freq, _sp = calc_trio
    tr = Transport(
        sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        source_calculations={"supporting_geometry": opt},
    )
    payload = _build_upload(water_species, calc_trio, transport=tr).to_payload()
    assert "transport" not in payload


def test_transport_optional_keeps_payload_shape(water_species, calc_trio):
    """No transport kwarg → payload identical to Phase-1 / 3C output."""
    opt, freq, _sp = calc_trio
    p_no_kwarg = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq],
        primary_calculation=opt,
    ).to_payload()
    p_none_kwarg = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq],
        primary_calculation=opt, transport=None,
    ).to_payload()
    assert p_no_kwarg == p_none_kwarg


def test_transport_alongside_thermo_and_statmech(water_species, calc_trio):
    opt, freq, sp = calc_trio
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    statmech = Statmech(
        external_symmetry=2, point_group="C2v",
        source_calculations=[("freq", freq)],
    )
    transport = Transport(
        sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        source_calculations={"supporting_geometry": opt},
    )
    payload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=thermo,
        statmech=statmech,
        transport=transport,
    ).to_payload()
    # thermo + statmech land on the wire, transport does not.
    assert "thermo" in payload
    assert "statmech" in payload
    assert "transport" not in payload


def test_to_payload_deterministic_with_transport(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    tr = Transport(
        sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        source_calculations={"supporting_geometry": opt},
    )
    upload = _build_upload(water_species, calc_trio, transport=tr)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
