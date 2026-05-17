"""Smoke test for ``examples/builder_computed_species_demo.py``.

Mirrors the reaction-demo test in structure: module-scoped
subprocess fixture so the test suite pays the subprocess +
interpreter startup cost once across every assertion.

The test:

- runs the demo without ``TCKDB_BASE_URL`` / ``TCKDB_API_KEY``,
- asserts payload / diagnostics / artifacts / plan-preview sections
  are all present,
- pins the artifact diagnostic code against the public ``DIAG_CODES``,
- guards the demo against re-introducing private-attr access or
  inline ``calculation_keys`` synthesis.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tckdb_client.builders import DIAG_CODES


_DEMO_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "builder_computed_species_demo.py"
)


# ---------------------------------------------------------------------
# Module-scoped fixture: one subprocess shared by every assertion
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_run():
    env = dict(os.environ)
    env.pop("TCKDB_BASE_URL", None)
    env.pop("TCKDB_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(_DEMO_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc


# ---------------------------------------------------------------------
# Existence + exit status
# ---------------------------------------------------------------------


def test_demo_script_exists():
    assert _DEMO_PATH.is_file(), (
        f"Demo script missing at {_DEMO_PATH}; README links to it."
    )


def test_demo_exits_zero(demo_run):
    assert demo_run.returncode == 0, (
        f"demo script exited {demo_run.returncode}\n"
        f"--- stdout ---\n{demo_run.stdout}\n"
        f"--- stderr ---\n{demo_run.stderr}"
    )


# ---------------------------------------------------------------------
# Output sections
# ---------------------------------------------------------------------


def test_demo_prints_payload_summary(demo_run):
    """Pins the migration to ``upload.summary().to_text()`` — the
    demo no longer formats payload dicts inline."""
    out = demo_run.stdout
    assert "== Payload summary ==" in out
    assert "ComputedSpeciesUpload" in out
    # Stable section markers from ``SECTION_MARKERS``.
    for marker in ("Identity:", "Calculations:", "Scientific blocks:",
                   "Artifacts:", "Diagnostics:"):
        assert marker in out
    assert "CCO" in out  # ethanol SMILES — distinguishes from reaction demo


def test_demo_uses_upload_summary_to_text():
    text = _DEMO_PATH.read_text(encoding="utf-8")
    assert "upload.summary().to_text()" in text, (
        "demo must use ``upload.summary().to_text()`` for its payload "
        "summary section (replaces the old ``_payload_summary(...)`` helper)."
    )


def test_demo_no_longer_defines_payload_summary_helper():
    text = _DEMO_PATH.read_text(encoding="utf-8")
    assert "_payload_summary" not in text, (
        "demo should rely on ``upload.summary()`` instead of a "
        "demo-local ``_payload_summary`` helper."
    )


def test_demo_prints_emission_diagnostics(demo_run):
    out = demo_run.stdout
    assert "== Emission diagnostics ==" in out
    assert DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE in out


def test_demo_prints_artifact_diagnostic_code(demo_run):
    """One ``artifact_upload_requires_second_phase`` warning per calc
    with attached artifacts (opt + sp in this demo)."""
    out = demo_run.stdout
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in out
    assert "calculations[ethanol opt].artifacts" in out
    assert "calculations[ethanol sp].artifacts" in out


def test_demo_prints_artifact_summary(demo_run):
    out = demo_run.stdout
    assert "== Artifacts ==" in out
    assert "2 artifact(s) across 2 calculation(s)" in out
    assert "input" in out
    assert "output_log" in out
    assert "second phase" in out
    assert "upload.artifact_plan" in out
    assert "client.upload_artifacts" in out


def test_demo_prints_artifact_plan_preview_with_mock_ids(demo_run):
    """No-server mode renders the plan against synthetic IDs via the
    public ``artifact_plan_preview`` helper."""
    out = demo_run.stdout
    assert "== Artifact plan preview (mock calculation IDs) ==" in out
    plan_lines = [
        line for line in out.splitlines()
        if line.startswith("  - calc_key=")
    ]
    # Two attached artifacts → two plan entries; deterministic synthetic
    # IDs starting at 1000.
    assert len(plan_lines) >= 2
    assert any("calc_key=ethanol_opt" in line for line in plan_lines)
    assert any("calc_key=ethanol_sp" in line for line in plan_lines)


def test_demo_prints_truncated_payload_preview(demo_run):
    out = demo_run.stdout
    assert "== Wire payload (truncated) ==" in out


def test_demo_writes_tempdir_for_artifact_files(demo_run):
    """The temp-dir prefix appears in the artifact summary; producers
    can find the demo files there if they want to inspect them."""
    out = demo_run.stdout
    assert "tckdb-builder-species-demo-" in out


# ---------------------------------------------------------------------
# Network short-circuit guarantee
# ---------------------------------------------------------------------


def test_demo_does_not_attempt_network_without_env_vars(demo_run):
    out = demo_run.stdout
    err = demo_run.stderr
    assert "skipping live upload" in out
    for needle in (
        "ConnectionError",
        "ConnectError",
        "Failed to establish a new connection",
    ):
        assert needle not in err, (
            f"demo attempted network without env vars; stderr:\n{err}"
        )


# ---------------------------------------------------------------------
# Public-API hygiene: no private state, no manual response mocking
# ---------------------------------------------------------------------


def test_demo_does_not_access_private_upload_attributes():
    text = _DEMO_PATH.read_text(encoding="utf-8")
    # The Phase-8 cleanup removed every ``_species_calc_pairs`` reach
    # from the reaction demo; the species demo never had one but we
    # pin the rule explicitly to catch any future regression.
    assert "_species_calc_pairs" not in text
    assert "_species_thermo_pairs" not in text
    assert "_species_statmech_pairs" not in text
    assert "_species_transport_pairs" not in text


def test_demo_does_not_synthesise_calculation_keys_inline():
    """The mock plan-preview moved into ``artifact_plan_preview`` —
    inline ``{"calculation_keys": …}`` dict literals in the demo
    would mean the public method has gaps."""
    text = _DEMO_PATH.read_text(encoding="utf-8")
    for marker in ('"calculation_keys":', "'calculation_keys':"):
        assert marker not in text, (
            f"demo mocks ``calculation_keys`` inline (found {marker!r}); "
            "call ``upload.artifact_plan_preview()`` instead."
        )


def test_demo_uses_public_iteration_helpers():
    """At least one of the public iter_* helpers appears in the
    demo, plus the public ``artifact_plan_preview`` helper."""
    text = _DEMO_PATH.read_text(encoding="utf-8")
    assert (
        "iter_calculation_entries" in text
        or "iter_artifacts" in text
        or "iter_calculations" in text
    )
    assert "artifact_plan_preview" in text


# ---------------------------------------------------------------------
# Diagnostic-code stability
# ---------------------------------------------------------------------


def test_demo_diagnostic_codes_match_public_constants(demo_run):
    out = demo_run.stdout
    assert DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE in out
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in out
