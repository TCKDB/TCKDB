"""Tests for ``emission_diagnostics()`` on builder upload objects.

These pin three things at once:

- The stable diagnostic codes (so producers can match them safely).
- Which currently-known forward-compat gaps produce a warning.
- Which builder fields *do* emit on the wire (and therefore produce
  no diagnostic).
"""

from __future__ import annotations

import warnings

import httpx
import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    DIAG_CODES,
    Diagnostic,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    Statmech,
    TCKDBBuilderValidationError,
    Thermo,
    Transport,
    TransitionState,
)

from conftest import make_client


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


# ----- helper fixtures ----------------------------------------------


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


# ----- diagnostic code stability ------------------------------------


def test_diagnostic_codes_are_stable_strings():
    """Locks the exact wire tokens producers can match on. Renaming any
    of these is a breaking change."""
    assert (
        DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE
        == "transport_not_emitted_in_computed_species_bundle"
    )
    assert (
        DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        == "transport_not_emitted_in_computed_reaction_bundle"
    )
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        == "thermo_source_calculations_not_emitted_in_computed_reaction_bundle"
    )


def test_diagnostic_record_shape():
    d = Diagnostic(
        level="warning", code="x", message="msg", path="foo",
    )
    # Frozen + comparable.
    assert d.level == "warning"
    with pytest.raises(Exception):  # FrozenInstanceError
        d.level = "info"  # type: ignore[misc]


# ----- ComputedSpeciesUpload ----------------------------------------


def test_computed_species_transport_reports_not_emitted(
    water_species, calc_trio,
):
    opt, _freq, _sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt],
        primary_calculation=opt,
        transport=Transport(
            sigma_angstrom=2.7, epsilon_over_k_k=572.4,
            source_calculations={"supporting_geometry": opt},
        ),
    )
    diags = upload.emission_diagnostics()
    assert len(diags) == 1
    diag = diags[0]
    assert diag.level == "warning"
    assert diag.code == DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE
    assert diag.path == "transport"


def test_computed_species_thermo_source_calcs_produce_no_warning(
    water_species, calc_trio,
):
    """The computed-species ``ThermoInBundle`` DOES carry
    ``source_calculations`` — emission happens, no warning."""
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    assert upload.emission_diagnostics() == []


def test_computed_species_no_optional_blocks_is_clean(water_species, calc_trio):
    opt, _freq, _sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt], primary_calculation=opt,
    )
    assert upload.emission_diagnostics() == []


# ----- ComputedReactionUpload ---------------------------------------


def _basic_reaction(ts_geom):
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    return (
        ch3, ch4,
        ChemReaction(
            reactants=[ch3], products=[ch4],
            transition_state=TransitionState(
                charge=0, multiplicity=2, geometry=ts_geom,
            ),
        ),
    )


def test_computed_reaction_species_transport_reports_not_emitted():
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    _ch3, ch4, rxn = _basic_reaction(ts_geom)
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: Transport(dipole_debye=0.1)},
    )
    diags = upload.emission_diagnostics()
    assert len(diags) == 1
    assert diags[0].level == "warning"
    assert diags[0].code == DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
    assert diags[0].path == "species_transport[CH4]"


def test_computed_reaction_species_thermo_source_calcs_report_not_emitted():
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    _ch3, ch4, rxn = _basic_reaction(ts_geom)
    thermo = Thermo.scalar(
        h298_kj_mol=-74.6,
        source_calculations={"opt": ch4_opt},
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_thermo={ch4: thermo},
    )
    diags = upload.emission_diagnostics()
    assert len(diags) == 1
    assert diags[0].level == "warning"
    assert (
        diags[0].code
        == DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
    )
    assert diags[0].path == "species_thermo[CH4].source_calculations"


def test_computed_reaction_thermo_without_source_calcs_is_clean():
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    _ch3, ch4, rxn = _basic_reaction(ts_geom)
    # Thermo without source_calculations is fully emitted — no warning.
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_thermo={ch4: Thermo.scalar(h298_kj_mol=-74.6)},
    )
    assert upload.emission_diagnostics() == []


def test_computed_reaction_statmech_source_calcs_produce_no_warning():
    """Both bundle schemas DO carry statmech ``source_calculations``
    — emission happens, no warning."""
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    _ch3, ch4, rxn = _basic_reaction(ts_geom)
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_statmech={
            ch4: Statmech(
                external_symmetry=12, point_group="Td",
                source_calculations=[("opt", ch4_opt)],
            ),
        },
    )
    assert upload.emission_diagnostics() == []


def test_diagnostics_one_per_affected_species():
    """A reaction with two species attaching transport produces two
    diagnostics, one per species, each with its own path."""
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ch3, ch4, rxn = _basic_reaction(ts_geom)
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch3: [ch3_opt], ch4: [ch4_opt]},
        species_transport={
            ch3: Transport(dipole_debye=0.0),
            ch4: Transport(dipole_debye=0.1),
        },
    )
    diags = upload.emission_diagnostics()
    assert len(diags) == 2
    paths = sorted(d.path for d in diags)
    assert paths == ["species_transport[CH3]", "species_transport[CH4]"]
    assert all(
        d.code == DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        for d in diags
    )


# ----- to_payload not perturbed by the new method --------------------


def test_to_payload_unchanged_after_diagnostics_method_added(
    water_species, calc_trio,
):
    """Spot-check that adding emission_diagnostics did not perturb the
    bundle payload for the canonical opt+freq+sp shape."""
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq, sp],
        primary_calculation=opt,
    )
    payload = upload.to_payload()
    # Phase-1 shape — pre-diagnostics. The keys we lock here are the
    # ones the §14 snapshot test in test_computed_species_upload_builder
    # also asserts on.
    assert payload["species_entry"]["smiles"] == "O"
    assert payload["conformers"][0]["primary_calculation"]["type"] == "opt"
    assert [
        c["type"] for c in payload["conformers"][0]["additional_calculations"]
    ] == ["freq", "sp"]
    assert "thermo" not in payload
    assert "statmech" not in payload
    assert "transport" not in payload


# ----- client.upload(warn_on_dropped_fields=True) --------------------


def _ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"ok": True})

    return handler


def test_client_warn_on_dropped_fields_emits_python_warning(
    water_species, calc_trio,
):
    opt, _freq, _sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt], primary_calculation=opt,
        transport=Transport(
            sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        ),
    )
    client, _recorder = make_client(_ok())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.upload(upload, warn_on_dropped_fields=True)
    matching = [
        w for w in caught
        if "transport_not_emitted_in_computed_species_bundle" in str(w.message)
    ]
    assert len(matching) == 1
    assert issubclass(matching[0].category, UserWarning)


def test_client_default_does_not_emit_python_warning(water_species, calc_trio):
    """Default (``warn_on_dropped_fields=False``) stays silent — the
    diagnostic API exists for producers that explicitly opt in."""
    opt, _freq, _sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt], primary_calculation=opt,
        transport=Transport(
            sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        ),
    )
    client, _recorder = make_client(_ok())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.upload(upload)
    assert not [
        w for w in caught
        if "transport_not_emitted" in str(w.message)
    ]


def test_client_warn_on_dropped_fields_silent_when_no_diagnostics(
    water_species, calc_trio,
):
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
    )
    client, _recorder = make_client(_ok())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.upload(upload, warn_on_dropped_fields=True)
    assert not [
        w for w in caught
        if "not_emitted" in str(w.message)
    ]


def test_client_warn_on_dropped_fields_skips_objects_without_emission_diagnostics():
    """A bare object exposing ``upload_kind`` + ``to_payload`` but no
    ``emission_diagnostics`` must not crash the dispatcher."""

    class Bare:
        upload_kind = "computed_species"

        def to_payload(self):
            return {
                "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
                "conformers": [],
            }

    client, _recorder = make_client(_ok())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.upload(Bare(), warn_on_dropped_fields=True)
    # No diagnostic-related warnings produced.
    assert not [
        w for w in caught
        if "not_emitted" in str(w.message)
    ]
