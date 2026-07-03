"""Unit tests for workflow-boundary provenance warnings.

These tests exercise the pure helpers in
``app.services.provenance_warnings`` without touching the database:

* **provided provenance** produces no spurious missing-provenance warnings
* **omitted provenance** produces structured warnings where the policy
  marks the fragment as scientifically meaningful for the record type
* warnings use the shared :class:`UploadWarning` shape
"""

from __future__ import annotations

from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.provenance_warnings import (
    W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE,
    W_MISSING_LEVEL_OF_THEORY_PROVENANCE,
    W_MISSING_LITERATURE_PROVENANCE,
    W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
    W_MISSING_WORKFLOW_TOOL_PROVENANCE,
    collect_kinetics_provenance_warnings,
    collect_statmech_provenance_warnings,
    collect_thermo_provenance_warnings,
    collect_transport_provenance_warnings,
)

# ---------------------------------------------------------------------------
# Fragment fixtures
# ---------------------------------------------------------------------------

_SPECIES_ENTRY = {"smiles": "O", "charge": 0, "multiplicity": 1}

_SOFTWARE_RELEASE = {"name": "Gaussian", "version": "16"}
_WORKFLOW_TOOL_RELEASE = {"name": "ARC", "version": "1.2.0"}
_LITERATURE = {"doi": "10.1000/example.doi", "title": "Placeholder title"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_FREQ_SCALE_FACTOR = {
    "level_of_theory": _LOT,
    "scale_kind": "fundamental",
    "value": 0.97,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thermo(**overrides) -> ThermoUploadRequest:
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
    }
    base.update(overrides)
    return ThermoUploadRequest(**base)


def _transport(**overrides) -> TransportUploadRequest:
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
    }
    base.update(overrides)
    return TransportUploadRequest(**base)


def _statmech(**overrides) -> StatmechUploadRequest:
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    base.update(overrides)
    return StatmechUploadRequest(**base)


def _kinetics(**overrides) -> KineticsUploadRequest:
    base: dict = {
        "reaction": {
            "reversible": False,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {
                    "species_entry": {
                        "smiles": "[H][H]",
                        "charge": 0,
                        "multiplicity": 1,
                    }
                }
            ],
        },
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.23e12,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
    }
    base.update(overrides)
    return KineticsUploadRequest(**base)


def _codes(warnings: list[UploadWarning]) -> set[str]:
    return {w.code for w in warnings}


# ---------------------------------------------------------------------------
# Test 1. Provided provenance → no missing-provenance warnings
# ---------------------------------------------------------------------------


def test_thermo_fully_specified_computed_has_no_warnings() -> None:
    warnings = collect_thermo_provenance_warnings(
        _thermo(
            software_release=dict(_SOFTWARE_RELEASE),
            workflow_tool_release=dict(_WORKFLOW_TOOL_RELEASE),
        )
    )
    assert warnings == []


def test_transport_fully_specified_computed_has_no_warnings() -> None:
    warnings = collect_transport_provenance_warnings(
        _transport(
            software_release=dict(_SOFTWARE_RELEASE),
            workflow_tool_release=dict(_WORKFLOW_TOOL_RELEASE),
        )
    )
    assert warnings == []


def test_statmech_fully_specified_computed_has_no_warnings() -> None:
    warnings = collect_statmech_provenance_warnings(
        _statmech(
            software_release=dict(_SOFTWARE_RELEASE),
            workflow_tool_release=dict(_WORKFLOW_TOOL_RELEASE),
            freq_scale_factor=dict(_FREQ_SCALE_FACTOR),
        )
    )
    assert warnings == []


def test_kinetics_fully_specified_computed_has_no_warnings() -> None:
    warnings = collect_kinetics_provenance_warnings(
        _kinetics(
            software_release=dict(_SOFTWARE_RELEASE),
            workflow_tool_release=dict(_WORKFLOW_TOOL_RELEASE),
            energy_level_of_theory=dict(_LOT),
        )
    )
    assert warnings == []


def test_experimental_thermo_with_literature_has_no_warnings() -> None:
    """Non-computed origins only expect a literature anchor."""
    warnings = collect_thermo_provenance_warnings(
        _thermo(
            scientific_origin="experimental",
            literature=dict(_LITERATURE),
        )
    )
    assert warnings == []


# ---------------------------------------------------------------------------
# Test 2. Omitted provenance → structured warnings
# ---------------------------------------------------------------------------


def test_thermo_computed_missing_software_and_workflow_tool_emits_warnings() -> None:
    warnings = collect_thermo_provenance_warnings(_thermo())
    assert _codes(warnings) == {
        W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        W_MISSING_WORKFLOW_TOOL_PROVENANCE,
    }


def test_transport_computed_missing_software_and_workflow_tool_emits_warnings() -> None:
    warnings = collect_transport_provenance_warnings(_transport())
    assert _codes(warnings) == {
        W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        W_MISSING_WORKFLOW_TOOL_PROVENANCE,
    }


def test_statmech_computed_also_warns_on_missing_freq_scale_factor() -> None:
    warnings = collect_statmech_provenance_warnings(_statmech())
    assert _codes(warnings) == {
        W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        W_MISSING_WORKFLOW_TOOL_PROVENANCE,
        W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE,
    }


def test_kinetics_computed_also_warns_on_missing_energy_lot() -> None:
    warnings = collect_kinetics_provenance_warnings(_kinetics())
    assert _codes(warnings) == {
        W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        W_MISSING_WORKFLOW_TOOL_PROVENANCE,
        W_MISSING_LEVEL_OF_THEORY_PROVENANCE,
    }


def test_experimental_kinetics_without_literature_warns_on_literature() -> None:
    """For non-computed origins, literature is the expected anchor."""
    warnings = collect_kinetics_provenance_warnings(
        _kinetics(scientific_origin="experimental")
    )
    assert _codes(warnings) == {W_MISSING_LITERATURE_PROVENANCE}


def test_estimated_thermo_without_literature_warns_on_literature() -> None:
    warnings = collect_thermo_provenance_warnings(
        _thermo(scientific_origin="estimated")
    )
    assert _codes(warnings) == {W_MISSING_LITERATURE_PROVENANCE}


# ---------------------------------------------------------------------------
# Test 3. Partial provenance — only the omitted piece warns
# ---------------------------------------------------------------------------


def test_thermo_with_only_software_release_still_warns_on_workflow_tool() -> None:
    warnings = collect_thermo_provenance_warnings(
        _thermo(software_release=dict(_SOFTWARE_RELEASE))
    )
    assert _codes(warnings) == {W_MISSING_WORKFLOW_TOOL_PROVENANCE}


def test_statmech_with_only_freq_scale_still_warns_on_software_and_tool() -> None:
    warnings = collect_statmech_provenance_warnings(
        _statmech(freq_scale_factor=dict(_FREQ_SCALE_FACTOR))
    )
    assert _codes(warnings) == {
        W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        W_MISSING_WORKFLOW_TOOL_PROVENANCE,
    }


# ---------------------------------------------------------------------------
# Test 4. Warning shape matches the shared UploadWarning primitive
# ---------------------------------------------------------------------------


def test_warning_shape_matches_shared_upload_warning_pattern() -> None:
    warnings = collect_thermo_provenance_warnings(_thermo())
    assert warnings, "expected at least one warning for missing provenance"
    for w in warnings:
        assert isinstance(w, UploadWarning)
        # Exact field set from the shared primitive
        assert set(w.model_dump().keys()) == {"field", "code", "message"}
        # Codes are machine-readable tokens
        assert w.code.startswith("missing_")
        # Field names match the request schema attribute
        assert w.field in {
            "literature",
            "software_release",
            "workflow_tool_release",
            "energy_level_of_theory",
            "freq_scale_factor",
        }
        # Messages are non-empty, pre-formatted text
        assert w.message and isinstance(w.message, str)
