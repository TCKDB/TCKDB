"""Smoke test for ``examples/builder_arc_style_dry_run.py``.

Mirrors the existing reaction-demo test in structure: module-scoped
subprocess fixture so the test suite pays the subprocess + interpreter
startup cost once across every assertion.

The dry-run script must:

- exit zero without ``TCKDB_BASE_URL`` / ``TCKDB_API_KEY``,
- never attempt network without env vars,
- print summary / diagnostics / workflow-mapping / artifact / plan
  sections,
- use only public APIs (no private upload attrs, no inline
  ``calculation_keys`` mocking),
- respect the conformer-boundary policy (no ``selected_conformer``,
  no candidate-list language).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tckdb_client.builders import DIAG_CODES


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "builder_arc_style_dry_run.py"
)


# ---------------------------------------------------------------------
# Module-scoped fixture: one subprocess shared by every assertion
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def dry_run():
    env = dict(os.environ)
    env.pop("TCKDB_BASE_URL", None)
    env.pop("TCKDB_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc


# ---------------------------------------------------------------------
# Existence + exit status
# ---------------------------------------------------------------------


def test_script_exists():
    assert _SCRIPT_PATH.is_file(), (
        f"ARC-style dry-run script missing at {_SCRIPT_PATH}; "
        "README links to it."
    )


def test_script_exits_zero(dry_run):
    assert dry_run.returncode == 0, (
        f"dry-run exited {dry_run.returncode}\n"
        f"--- stdout ---\n{dry_run.stdout}\n"
        f"--- stderr ---\n{dry_run.stderr}"
    )


# ---------------------------------------------------------------------
# Output sections — every required printout shows up.
# ---------------------------------------------------------------------


def test_output_has_summary_section(dry_run):
    out = dry_run.stdout
    assert "== Payload summary ==" in out
    assert "ComputedReactionUpload" in out
    # Section markers from ``SECTION_MARKERS`` — pinned in the
    # builder summary tests; we just sample two here to keep the
    # dry-run test from duplicating that contract.
    assert "Identity:" in out
    assert "Calculations:" in out
    # Reaction-shaped signals.
    assert "H_Abstraction" in out
    for label in ("CH4", "OH", "CH3", "H2O"):
        assert label in out, f"summary missing species label {label!r}"


def test_output_has_diagnostics_section(dry_run):
    out = dry_run.stdout
    assert "== Emission diagnostics ==" in out
    # The script attaches two artifacts (one TS, one CH3) — Phase-7
    # diagnostic must surface for both attached calcs.
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in out
    # CH3 thermo carries source_calculations; the reaction-side
    # BundleThermoIn does not emit them — the script surfaces the
    # forward-compat warning.
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in out
    )


def test_output_has_artifact_plan_preview(dry_run):
    out = dry_run.stdout
    assert "== Artifact plan preview (mock calculation IDs) ==" in out
    plan_lines = [
        line for line in out.splitlines()
        if line.startswith("  - calc_key=")
    ]
    # Two attached artifacts → two plan entries; deterministic
    # synthetic IDs starting at 1000.
    assert len(plan_lines) >= 2
    assert any("calc_key=ts_opt" in line for line in plan_lines)
    assert any("calc_key=ch3_sp" in line for line in plan_lines)


def test_output_has_workflow_mapping_section(dry_run):
    out = dry_run.stdout
    assert "== Workflow → builder mapping ==" in out
    # Buckets present.
    for bucket in ("TS", "CH4", "OH", "CH3", "H2O"):
        assert f"[{bucket}]" in out


def test_output_surfaces_source_calculation_counts(dry_run):
    """``SourceCalculations`` is used indirectly through the kinetics
    record. The script surfaces the resulting source-calc counts via
    the workflow-mapping section so a reader can see ``SourceCalculations``
    is wired in correctly without the test parsing internal builder
    state.

    The kinetics record consumes:

    - ``reactant_energy = [ch4_sp, oh_sp]``  (2)
    - ``product_energy  = [ch3_sp, h2o_sp]`` (2)
    - ``ts_energy       = ts_sp``            (1)
    - ``freq            = ts_freq``          (1)

    → 6 entries total.
    """
    out = dry_run.stdout
    assert "kinetics[0] source_calculations: 6 entries" in out
    # Per-species statmech source_calculations also surface (CH3 has
    # opt + freq listed via ``.only("opt", "freq")``).
    assert "statmech source_calculations=2" in out


def test_output_has_truncated_payload_preview(dry_run):
    out = dry_run.stdout
    assert "== Wire payload (truncated) ==" in out
    # Confirm the payload preview is real JSON (key markers present).
    assert "\"species\":" in out


def test_output_lists_attached_artifacts(dry_run):
    out = dry_run.stdout
    assert "== Artifacts ==" in out
    assert "2 artifact(s) across 2 calculation(s)" in out
    assert "ts_opt.log" in out
    assert "ch3_sp.log" in out


# ---------------------------------------------------------------------
# Network short-circuit guarantee
# ---------------------------------------------------------------------


def test_dry_run_does_not_attempt_network_without_env_vars(dry_run):
    out = dry_run.stdout
    err = dry_run.stderr
    assert "skipping live upload" in out
    for needle in (
        "ConnectionError",
        "ConnectError",
        "Failed to establish a new connection",
        "httpx",
    ):
        assert needle not in err, (
            f"dry-run attempted network without env vars; stderr:\n{err}"
        )


# ---------------------------------------------------------------------
# Public-API + policy hygiene: static source-text guards.
# ---------------------------------------------------------------------


def test_script_does_not_access_private_upload_attributes():
    """The dry-run must use only public iteration helpers (Phase-8
    introduced ``iter_calculation_entries`` precisely so demos
    never reach into private upload state)."""
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "_species_calc_pairs",
        "_species_thermo_pairs",
        "_species_statmech_pairs",
        "_species_transport_pairs",
    ):
        assert forbidden not in text, (
            f"dry-run reaches into private upload state ({forbidden!r}); "
            "use ``upload.iter_calculation_entries()`` instead."
        )


def test_script_does_not_synthesise_calculation_keys_inline():
    """The mock plan-preview must come from
    ``upload.artifact_plan_preview``, not an inline ``calculation_keys``
    dict literal — otherwise the public method has gaps."""
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    for marker in ('"calculation_keys":', "'calculation_keys':"):
        assert marker not in text, (
            f"dry-run mocks ``calculation_keys`` inline (found {marker!r}); "
            "call ``upload.artifact_plan_preview()`` instead."
        )


def test_script_uses_public_iteration_helpers():
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert (
        "iter_calculation_entries" in text
        or "iter_artifacts" in text
        or "iter_calculations" in text
    ), "dry-run must walk the upload via the public ``iter_*`` helpers."
    assert "artifact_plan_preview" in text, (
        "dry-run must use the public ``artifact_plan_preview`` helper."
    )
    assert "summary()" in text, (
        "dry-run must call the public ``upload.summary()`` helper."
    )
    assert "emission_diagnostics" in text, (
        "dry-run must call the public ``emission_diagnostics()`` helper."
    )


def test_script_uses_source_calculations_helper():
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "SourceCalculations(" in text, (
        "dry-run must exercise the public ``SourceCalculations`` helper."
    )


def test_script_respects_conformer_boundary_policy():
    """Per ``docs/conformer_semantic_boundary.md`` the upload models
    one scientifically meaningful conformer / geometry per species —
    never the workflow's candidate list. The dry-run must not
    reintroduce the rejected API surface."""
    text = _SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "selected_conformer",
        "species_conformers",
        # Rejected kwargs from the policy doc.
        "conformers=[",
        "conformer_candidates",
        "candidate_conformers",
    ):
        assert forbidden not in text, (
            f"dry-run reintroduces rejected conformer-boundary token "
            f"{forbidden!r}; see ``docs/conformer_semantic_boundary.md``."
        )


def test_script_does_not_use_candidate_list_language():
    """The narrative around the script must reflect 'what the workflow
    stands behind' — not the search history."""
    text_lower = _SCRIPT_PATH.read_text(encoding="utf-8").lower()
    # The script's prose may legitimately mention 'conformer search'
    # in the *artifact* context (search runs become artifacts, not
    # records). What it must not promise is multiple records.
    for forbidden in (
        "multiple conformer records",
        "every conformer",
        "all conformers considered",
        "n conformers per species",
        "best of n",
    ):
        assert forbidden not in text_lower, (
            f"dry-run text suggests candidate-list behaviour ({forbidden!r}); "
            "the upload represents *what the workflow stands behind*."
        )


# ---------------------------------------------------------------------
# Diagnostic-code stability
# ---------------------------------------------------------------------


def test_script_diagnostic_codes_match_public_constants(dry_run):
    out = dry_run.stdout
    # The two stable codes the script's data shape triggers.
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in out
    )
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in out
