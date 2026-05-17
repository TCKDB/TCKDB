"""Tests for the ``SourceCalculations`` helper (Phase 1).

Covers the contract spelled out in
``clients/python/docs/source_calculation_ergonomics.md`` §6–§8 plus
§12's test plan:

* construction shapes (scalar / list, kwargs / .add chain),
* emission order (.as_list() insertion order, .only() requested order),
* duplicate-role preservation,
* lightest-possible validation (non-Calculation / empty list / bad role),
* drop-in compatibility with the existing thermo / statmech / kinetics /
  transport ``source_calculations=`` normalisers.
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    Geometry,
    Kinetics,
    LevelOfTheory,
    SoftwareRelease,
    SourceCalculations,
    Statmech,
    TCKDBBuilderValidationError,
    Thermo,
    Transport,
)


# ---------------------------------------------------------------------
# Fixtures — three calcs that form the canonical opt/freq/sp triad
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# Construction + canonical representation (§6)
# ---------------------------------------------------------------------


def test_kwargs_preserve_insertion_order(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    assert sources.as_list() == [("opt", opt), ("freq", freq), ("sp", sp)]


def test_kwargs_accept_scalar_and_list(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(
        reactant_energy=[opt, freq],
        ts_energy=sp,
    )
    assert sources.as_list() == [
        ("reactant_energy", opt),
        ("reactant_energy", freq),
        ("ts_energy", sp),
    ]


def test_add_appends_and_returns_self(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt)
    returned = sources.add("freq", freq).add("sp", sp)
    assert returned is sources
    assert sources.as_list() == [("opt", opt), ("freq", freq), ("sp", sp)]


def test_add_accepts_non_identifier_role(calc_trio):
    opt, freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt).add("k-inf", freq)
    assert sources.as_list() == [("opt", opt), ("k-inf", freq)]


def test_empty_construction_is_empty(calc_trio):
    sources = SourceCalculations()
    assert sources.as_list() == []
    assert len(sources) == 0
    assert list(sources) == []


# ---------------------------------------------------------------------
# Emission contracts
# ---------------------------------------------------------------------


def test_only_returns_requested_order(calc_trio):
    """`.only("sp", "opt")` emits sp first, opt second — caller-requested
    role order, not source insertion order."""
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    assert sources.only("sp", "opt") == [("sp", sp), ("opt", opt)]


def test_only_preserves_duplicate_role_intra_order(calc_trio):
    """Within a single requested role, entries keep their source order."""
    opt, freq, sp = calc_trio
    # opt appears twice in the source list (degenerate but legal).
    sources = SourceCalculations(opt=[opt, freq]).add("opt", sp)
    assert sources.only("opt") == [("opt", opt), ("opt", freq), ("opt", sp)]


def test_only_returns_empty_when_no_roles_requested(calc_trio):
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt)
    assert sources.only() == []


def test_only_raises_on_unknown_role(calc_trio):
    opt, freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq)
    with pytest.raises(TCKDBBuilderValidationError) as exc:
        sources.only("opt", "nope")
    assert "nope" in str(exc.value)


def test_as_list_preserves_duplicate_roles(calc_trio):
    """The bimolecular kinetics use case — duplicate roles flow through."""
    opt, freq, sp = calc_trio
    sources = SourceCalculations(
        reactant_energy=[opt, freq],
        ts_energy=sp,
    )
    pairs = sources.as_list()
    assert [r for r, _c in pairs] == ["reactant_energy", "reactant_energy", "ts_energy"]


def test_as_list_returns_fresh_list(calc_trio):
    """Mutating the returned list must not affect the helper."""
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt)
    pairs = sources.as_list()
    pairs.append(("bogus", opt))
    assert sources.as_list() == [("opt", opt)]


# ---------------------------------------------------------------------
# Lightest-possible validation (§7)
# ---------------------------------------------------------------------


def test_rejects_non_calculation_kwarg(calc_trio):
    with pytest.raises(TCKDBBuilderValidationError):
        SourceCalculations(opt="not a calculation")


def test_rejects_non_calculation_inside_list(calc_trio):
    opt, _freq, _sp = calc_trio
    with pytest.raises(TCKDBBuilderValidationError):
        SourceCalculations(reactant_energy=[opt, "oops"])


def test_rejects_empty_list_value(calc_trio):
    with pytest.raises(TCKDBBuilderValidationError):
        SourceCalculations(opt=[])


def test_add_rejects_non_calculation(calc_trio):
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt)
    with pytest.raises(TCKDBBuilderValidationError):
        sources.add("freq", "not a calc")


def test_add_rejects_empty_role(calc_trio):
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt)
    with pytest.raises(TCKDBBuilderValidationError):
        sources.add("   ", opt)


def test_only_rejects_empty_role_token(calc_trio):
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt)
    with pytest.raises(TCKDBBuilderValidationError):
        sources.only("opt", "")


# ---------------------------------------------------------------------
# Drop-in compatibility with existing builder normalisers (§8)
# ---------------------------------------------------------------------


def test_thermo_accepts_source_calculations_directly(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        source_calculations=sources,
    )
    assert [r for r, _c in thermo.source_calculations] == ["opt", "freq", "sp"]


def test_thermo_accepts_only_subset(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    thermo = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        source_calculations=sources.only("opt", "freq"),
    )
    assert [r for r, _c in thermo.source_calculations] == ["opt", "freq"]


def test_statmech_accepts_source_calculations_directly(calc_trio):
    opt, freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq)
    statmech = Statmech(
        external_symmetry=1,
        point_group="C1",
        is_linear=False,
        rigid_rotor_kind="asymmetric_top",
        statmech_treatment="rrho",
        source_calculations=sources,
    )
    assert [r for r, _c in statmech.source_calculations] == ["opt", "freq"]


def test_transport_accepts_source_calculations_directly(calc_trio):
    opt, _freq, _sp = calc_trio
    sources = SourceCalculations(supporting_geometry=opt)
    transport = Transport(
        epsilon_over_k_k=572.4,
        sigma_angstrom=2.605,
        dipole_debye=1.85,
        polarizability_angstrom3=1.45,
        rotational_relaxation=4.0,
        source_calculations=sources,
    )
    assert [r for r, _c in transport.source_calculations] == ["supporting_geometry"]


def test_kinetics_accepts_source_calculations_directly(calc_trio):
    opt, freq, sp = calc_trio
    # Kinetics uses the reaction-side role vocabulary; build a sources
    # bag with duplicate roles to exercise the §9 bimolecular case.
    sources = SourceCalculations(
        reactant_energy=[opt, freq],
        ts_energy=sp,
    )
    kinetics = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3/mol/s",
        n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations=sources,
    )
    roles = [r for r, _c in kinetics.source_calculations]
    assert roles == ["reactant_energy", "reactant_energy", "ts_energy"]


def test_existing_dict_shape_still_works(calc_trio):
    """§8 promises producers are not forced to migrate — the three
    legacy shapes keep working alongside the helper."""
    opt, freq, sp = calc_trio
    thermo_dict = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        source_calculations={"opt": opt, "freq": freq, "sp": sp},
    )
    thermo_dict_list = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        source_calculations={"opt": [opt], "freq": [freq, sp]},
    )
    thermo_pairs = Thermo.scalar(
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
    )
    assert [r for r, _c in thermo_dict.source_calculations] == ["opt", "freq", "sp"]
    assert [r for r, _c in thermo_dict_list.source_calculations] == ["opt", "freq", "freq"]
    assert [r for r, _c in thermo_pairs.source_calculations] == ["opt", "freq", "sp"]


# ---------------------------------------------------------------------
# Repr / iter / len
# ---------------------------------------------------------------------


def test_iter_and_len_match_as_list(calc_trio):
    opt, freq, _sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq)
    assert len(sources) == 2
    assert list(sources) == sources.as_list()


def test_repr_lists_roles_in_order(calc_trio):
    opt, freq, sp = calc_trio
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    text = repr(sources)
    # roles surface in order so a producer reading a traceback sees the
    # source-supplied order plainly.
    assert text == "SourceCalculations(roles=['opt', 'freq', 'sp'])"
