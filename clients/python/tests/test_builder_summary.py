"""Tests for the ``UploadSummary`` / ``upload.summary()`` surface.

Pins the design contract from
``clients/python/docs/builder_summary_design.md``:

* every required field appears in ``summary().to_dict()``,
* the dict is JSON-serialisable,
* no XYZ / base64 / artifact-path / diagnostic-message content leaks
  in,
* counts and codes line up with the existing public iteration /
  diagnostic helpers,
* ``to_text()`` includes every stable section marker,
* ``UploadSummary`` is exported from ``tckdb_client.builders``.
"""

from __future__ import annotations

import json

import pytest

import tckdb_client.builders as builders_pkg
from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    SourceCalculations,
    Statmech,
    Thermo,
    TransitionState,
    Transport,
    UploadSummary,
)
from tckdb_client.builders.summary import SECTION_MARKERS


# ---------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------


SR = SoftwareRelease(software="Gaussian", version="16")
LOT = LevelOfTheory(method="wb97xd", basis="def2tzvp")

WATER_XYZ = (
    "3\nh2o\n"
    "O 0.0 0.0 0.117\n"
    "H 0.0 0.757 -0.469\n"
    "H 0.0 -0.757 -0.469"
)


def _calc_trio(prefix: str, xyz: str, sp_energy: float):
    geom = Geometry.from_xyz(xyz)
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, converged=True,
        final_energy_hartree=sp_energy - 0.05,
        label=f"{prefix} opt",
    )
    freq = Calculation.freq(
        SR, LOT, n_imag=0, zpe_hartree=0.03,
        depends_on=opt, label=f"{prefix} freq",
    )
    sp = Calculation.sp(
        SR, LOT, electronic_energy_hartree=sp_energy,
        depends_on=opt, label=f"{prefix} sp",
    )
    return opt, freq, sp


@pytest.fixture
def species_upload() -> ComputedSpeciesUpload:
    opt, freq, sp = _calc_trio("water", WATER_XYZ, -76.45)
    opt.add_artifact("water_opt.log", kind="output_log")
    sp.add_artifact("water_sp.log", kind="output_log")
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=Thermo.scalar(
            h298_kj_mol=-241.8, s298_j_mol_k=188.8,
            source_calculations=[("opt", opt), ("freq", freq), ("sp", sp)],
        ),
        statmech=Statmech(
            external_symmetry=2, point_group="C2v",
            is_linear=False, rigid_rotor_kind="asymmetric_top",
            statmech_treatment="rrho",
            source_calculations=[("opt", opt), ("freq", freq)],
        ),
    )


@pytest.fixture
def reaction_upload() -> ComputedReactionUpload:
    ch3 = _calc_trio("ch3", "4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0", -39.71)
    h = _calc_trio("h", "1\nh\nH 0 0 0", -0.5)
    ch4 = _calc_trio("ch4", "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0", -40.51)
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(
        SR, LOT, output_geometry=ts_geom, converged=True,
        final_energy_hartree=-40.45, label="ts opt",
    )
    ts_freq = Calculation.freq(
        SR, LOT, n_imag=1, imag_freq_cm1=-1200.0, zpe_hartree=0.04,
        depends_on=ts_opt, label="ts freq",
    )
    ts_sp = Calculation.sp(
        SR, LOT, electronic_energy_hartree=-40.42,
        depends_on=ts_opt, label="ts sp",
    )
    ts_opt.add_artifact("ts_opt.log", kind="output_log")
    ch4[2].add_artifact("ch4_sp.log", kind="output_log")
    sources = SourceCalculations(
        reactant_energy=[ch3[2], h[2]],
        product_energy=ch4[2],
        ts_energy=ts_sp,
        freq=ts_freq,
    )
    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3/mol/s",
        n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations=sources.as_list(),
    )
    ch3_sp = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    h_sp = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch4_sp = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    rxn = ChemReaction(
        reactants=[ch3_sp, h_sp], products=[ch4_sp],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq, ts_sp],
        species_calculations={ch3_sp: list(ch3), h_sp: list(h), ch4_sp: list(ch4)},
        species_thermo={
            ch4_sp: Thermo.nasa(
                coeffs_low=[0.5] + [0.0] * 6,
                coeffs_high=[0.5] + [0.0] * 6,
                t_low=200, t_mid=1000, t_high=5000,
                h298_kj_mol=-74.6, s298_j_mol_k=186.3,
            ),
        },
        species_statmech={
            ch4_sp: Statmech(
                external_symmetry=12, point_group="Td",
                is_linear=False, rigid_rotor_kind="spherical_top",
                statmech_treatment="rrho",
            ),
        },
        species_transport={
            ch4_sp: Transport(
                sigma_angstrom=3.8, epsilon_over_k_k=141.4,
                dipole_debye=0.0, polarizability_angstrom3=2.6,
                rotational_relaxation=13.0,
            ),
        },
    )


# ---------------------------------------------------------------------
# Required-key contract
# ---------------------------------------------------------------------


SPECIES_REQUIRED_KEYS = {
    "kind",
    "species_smiles",
    "species_label",
    "charge",
    "multiplicity",
    "conformer_record_count",
    "calculation_count",
    "calculation_counts_by_type",
    "primary_calculation_label",
    "primary_calculation_key",
    "primary_calculation_type",
    "has_thermo",
    "thermo_kind",
    "has_statmech",
    "has_transport",
    "artifact_count",
    "artifact_calculation_count",
    "diagnostic_count",
    "diagnostic_codes",
}

REACTION_REQUIRED_KEYS = {
    "kind",
    "reactant_smiles",
    "reactant_labels",
    "product_smiles",
    "product_labels",
    "reaction_family",
    "species_count",
    "ts_calculation_counts_by_type",
    "species_calculation_counts",
    "species_calculation_counts_by_type",
    "kinetics_count",
    "species_with_thermo",
    "species_with_statmech",
    "species_with_transport",
    "artifact_count",
    "artifact_calculation_count",
    "diagnostic_count",
    "diagnostic_codes",
}


def test_species_summary_has_all_required_keys(species_upload):
    data = species_upload.summary().to_dict()
    missing = SPECIES_REQUIRED_KEYS - data.keys()
    assert not missing, f"missing species summary keys: {sorted(missing)}"


def test_reaction_summary_has_all_required_keys(reaction_upload):
    data = reaction_upload.summary().to_dict()
    missing = REACTION_REQUIRED_KEYS - data.keys()
    assert not missing, f"missing reaction summary keys: {sorted(missing)}"


def test_species_summary_kind(species_upload):
    summary = species_upload.summary()
    assert isinstance(summary, UploadSummary)
    assert summary.kind == "computed_species"
    assert summary.to_dict()["kind"] == "computed_species"


def test_reaction_summary_kind(reaction_upload):
    summary = reaction_upload.summary()
    assert isinstance(summary, UploadSummary)
    assert summary.kind == "computed_reaction"
    assert summary.to_dict()["kind"] == "computed_reaction"


# ---------------------------------------------------------------------
# JSON-serialisability — the §10 wire-shape rule for to_dict() shape.
# ---------------------------------------------------------------------


def test_species_summary_dict_is_json_serialisable(species_upload):
    blob = json.dumps(species_upload.summary().to_dict())
    # Round-trip through json so we know the shape is reachable from a
    # logging/observability pipeline.
    round_tripped = json.loads(blob)
    assert round_tripped["kind"] == "computed_species"


def test_reaction_summary_dict_is_json_serialisable(reaction_upload):
    blob = json.dumps(reaction_upload.summary().to_dict())
    round_tripped = json.loads(blob)
    assert round_tripped["kind"] == "computed_reaction"


# ---------------------------------------------------------------------
# Exclusions — §4 of the design doc.
# ---------------------------------------------------------------------


def _collect_string_values(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _collect_string_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _collect_string_values(v)


def test_species_summary_does_not_leak_xyz(species_upload):
    """No XYZ-shaped lines slip into the dict (no whitespace-separated
    element + 3 floats anywhere in the values)."""
    data = species_upload.summary().to_dict()
    for s in _collect_string_values(data):
        # A heuristic that's tight enough to catch raw XYZ rows
        # ("O  0.0  0.0  0.117") but loose enough to pass labels,
        # codes, and smiles.
        toks = s.split()
        if len(toks) == 4 and toks[0].isalpha() and len(toks[0]) <= 2:
            try:
                [float(x) for x in toks[1:]]
            except ValueError:
                continue
            pytest.fail(f"summary leaks XYZ-shaped string: {s!r}")


def test_reaction_summary_does_not_leak_xyz(reaction_upload):
    data = reaction_upload.summary().to_dict()
    for s in _collect_string_values(data):
        toks = s.split()
        if len(toks) == 4 and toks[0].isalpha() and len(toks[0]) <= 2:
            try:
                [float(x) for x in toks[1:]]
            except ValueError:
                continue
            pytest.fail(f"summary leaks XYZ-shaped string: {s!r}")


def test_species_summary_does_not_leak_artifact_paths(species_upload):
    data = species_upload.summary().to_dict()
    for s in _collect_string_values(data):
        assert "water_opt.log" not in s, (
            "summary leaks the artifact path; only counts should be exposed."
        )
        assert "water_sp.log" not in s
        # No "path"-like keys either.
    assert "artifact_paths" not in data
    assert "artifact_hashes" not in data


def test_reaction_summary_does_not_leak_artifact_paths(reaction_upload):
    data = reaction_upload.summary().to_dict()
    for s in _collect_string_values(data):
        assert "ts_opt.log" not in s
        assert "ch4_sp.log" not in s
    assert "artifact_paths" not in data
    assert "artifact_hashes" not in data


def test_summary_does_not_leak_diagnostic_messages(species_upload):
    """§5 — only codes survive in the summary, not human messages."""
    diags = species_upload.emission_diagnostics()
    # Sanity: the fixture should produce at least one diagnostic so
    # this test is meaningful.
    assert diags, "species_upload fixture must produce ≥1 diagnostic"
    data = species_upload.summary().to_dict()
    for s in _collect_string_values(data):
        for d in diags:
            assert d.message not in s, (
                "summary leaks the diagnostic message; only codes should appear."
            )


def test_summary_does_not_leak_base64_or_db_ids(reaction_upload):
    data = reaction_upload.summary().to_dict()
    # No base64-style padding patterns and no DB-shaped id keys.
    blob = json.dumps(data)
    assert "=\"" not in blob  # base64 padding char inside a string
    assert "database_id" not in data
    assert "id" not in data  # no flat "id" field on the summary
    # No NASA-coefficient table.
    assert "nasa" not in data
    # No frequency lists.
    assert "frequencies" not in data


# ---------------------------------------------------------------------
# Cross-consistency with public iteration helpers.
# ---------------------------------------------------------------------


def test_species_summary_calculation_count_matches_iter(species_upload):
    data = species_upload.summary().to_dict()
    assert data["calculation_count"] == sum(
        1 for _ in species_upload.iter_calculations()
    )


def test_species_summary_calc_counts_by_type_match(species_upload):
    data = species_upload.summary().to_dict()
    expected: dict[str, int] = {}
    for calc in species_upload.iter_calculations():
        expected[calc.type] = expected.get(calc.type, 0) + 1
    assert data["calculation_counts_by_type"] == expected


def test_species_summary_artifact_count_matches_iter(species_upload):
    data = species_upload.summary().to_dict()
    assert data["artifact_count"] == sum(
        1 for _ in species_upload.iter_artifacts()
    )
    distinct = {
        id(calc) for calc, _ in species_upload.iter_artifacts()
    }
    assert data["artifact_calculation_count"] == len(distinct)


def test_reaction_summary_artifact_count_matches_iter(reaction_upload):
    data = reaction_upload.summary().to_dict()
    assert data["artifact_count"] == sum(
        1 for _ in reaction_upload.iter_artifacts()
    )
    distinct = {
        id(calc) for calc, _ in reaction_upload.iter_artifacts()
    }
    assert data["artifact_calculation_count"] == len(distinct)


def test_species_summary_diagnostic_codes_match_emission(species_upload):
    data = species_upload.summary().to_dict()
    expected_codes = sorted({
        d.code for d in species_upload.emission_diagnostics()
    })
    expected_count = len(species_upload.emission_diagnostics())
    assert data["diagnostic_codes"] == expected_codes
    assert data["diagnostic_count"] == expected_count


def test_reaction_summary_diagnostic_codes_match_emission(reaction_upload):
    data = reaction_upload.summary().to_dict()
    expected_codes = sorted({
        d.code for d in reaction_upload.emission_diagnostics()
    })
    expected_count = len(reaction_upload.emission_diagnostics())
    assert data["diagnostic_codes"] == expected_codes
    assert data["diagnostic_count"] == expected_count


def test_reaction_summary_species_calc_counts_match_entries(reaction_upload):
    data = reaction_upload.summary().to_dict()
    expected_counts: dict[str, int] = {}
    expected_by_type: dict[str, dict[str, int]] = {}
    for entry in reaction_upload.iter_calculation_entries():
        if entry.bucket == "TS":
            continue
        expected_counts[entry.bucket] = expected_counts.get(entry.bucket, 0) + 1
        per_type = expected_by_type.setdefault(entry.bucket, {})
        per_type[entry.calculation.type] = (
            per_type.get(entry.calculation.type, 0) + 1
        )
    assert data["species_calculation_counts"] == expected_counts
    assert data["species_calculation_counts_by_type"] == expected_by_type


# ---------------------------------------------------------------------
# Identity / scientific-block visibility.
# ---------------------------------------------------------------------


def test_species_summary_identity_fields(species_upload):
    data = species_upload.summary().to_dict()
    assert data["species_smiles"] == "O"
    assert data["species_label"] == "water"
    assert data["charge"] == 0
    assert data["multiplicity"] == 1
    assert data["primary_calculation_type"] == "opt"
    assert data["primary_calculation_label"] == "water opt"
    assert isinstance(data["primary_calculation_key"], str)
    assert data["conformer_record_count"] == 1


def test_species_summary_scientific_block_flags(species_upload):
    data = species_upload.summary().to_dict()
    assert data["has_thermo"] is True
    assert data["thermo_kind"] == "scalar"
    assert data["has_statmech"] is True
    assert data["has_transport"] is False


def test_reaction_summary_identity_fields(reaction_upload):
    data = reaction_upload.summary().to_dict()
    assert data["reactant_smiles"] == ["[CH3]", "[H]"]
    assert data["reactant_labels"] == ["CH3", "H"]
    assert data["product_smiles"] == ["C"]
    assert data["product_labels"] == ["CH4"]
    assert data["reaction_family"] == "H_Abstraction"
    assert data["species_count"] == 3
    assert data["kinetics_count"] == 1


def test_reaction_summary_scientific_block_visibility(reaction_upload):
    data = reaction_upload.summary().to_dict()
    assert data["species_with_thermo"] == ["CH4"]
    assert data["species_with_statmech"] == ["CH4"]
    assert data["species_with_transport"] == ["CH4"]


def test_thermo_kind_none_when_no_thermo():
    opt = Calculation.opt(
        SR, LOT,
        output_geometry=Geometry.from_xyz(WATER_XYZ),
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    upload = ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1),
        calculations=[opt],
        primary_calculation=opt,
    )
    data = upload.summary().to_dict()
    assert data["has_thermo"] is False
    assert data["thermo_kind"] is None


# ---------------------------------------------------------------------
# to_text() section markers
# ---------------------------------------------------------------------


@pytest.mark.parametrize("marker", SECTION_MARKERS)
def test_species_summary_text_includes_section_marker(species_upload, marker):
    text = species_upload.summary().to_text()
    assert marker in text, (
        f"species summary text missing stable section marker {marker!r}"
    )


@pytest.mark.parametrize("marker", SECTION_MARKERS)
def test_reaction_summary_text_includes_section_marker(reaction_upload, marker):
    text = reaction_upload.summary().to_text()
    assert marker in text, (
        f"reaction summary text missing stable section marker {marker!r}"
    )


def test_species_summary_text_lists_diagnostic_codes(species_upload):
    text = species_upload.summary().to_text()
    for code in species_upload.summary().to_dict()["diagnostic_codes"]:
        assert code in text


def test_text_does_not_contain_xyz(species_upload):
    text = species_upload.summary().to_text()
    # The canonical fixture's XYZ has the element symbol followed by
    # three floats — that sub-pattern must not appear in the summary
    # text.
    assert "0.117" not in text
    assert "0.757" not in text


# ---------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------


def test_upload_summary_is_public_export():
    assert hasattr(builders_pkg, "UploadSummary")
    assert "UploadSummary" in builders_pkg.__all__
    assert builders_pkg.UploadSummary is UploadSummary


def test_upload_summary_is_frozen():
    summary = UploadSummary(kind="computed_species", data={"a": 1})
    with pytest.raises(Exception):  # FrozenInstanceError is a subclass of AttributeError
        summary.kind = "other"  # type: ignore[misc]


def test_to_dict_returns_fresh_copy():
    """Mutating the returned dict must not affect future ``.to_dict()``
    calls — the underlying ``data`` mapping should be read-through."""
    summary = UploadSummary(kind="computed_species", data={"a": 1})
    out = summary.to_dict()
    out["a"] = 999
    out["b"] = 2
    assert summary.to_dict() == {"a": 1}
