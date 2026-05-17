"""Smoke test for the documented end-to-end builder demo script.

Runs ``examples/builder_computed_reaction_demo.py`` as a subprocess
with neither ``TCKDB_BASE_URL`` nor ``TCKDB_API_KEY`` set. The script
must:

- exit with status 0,
- print the payload summary,
- print the emission diagnostics (transport / thermo source_calcs / artifact),
- print the artifact summary block,
- print a mock artifact plan preview, and
- short-circuit before any HTTP dispatch.

The test never touches the network — the demo script short-circuits
before HTTP dispatch when the env vars are absent. Anyone reading
the README and copy-pasting the demo path should land on a working
example. The demo run is cached at module scope so the test suite
pays the subprocess + import cost once across all assertions.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tckdb_client.builders import DIAG_CODES


_DEMO_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "builder_computed_reaction_demo.py"
)


@pytest.fixture(scope="module")
def demo_run():
    """Run the demo once, with the server env vars scrubbed.

    Cached across the whole module — every assertion below works off
    the same stdout/stderr capture so the suite pays one subprocess
    + interpreter startup cost regardless of how many checks we
    make.
    """
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


def test_demo_prints_payload_summary(demo_run):
    """Pins the migration to ``upload.summary().to_text()`` — the
    demo no longer formats payload dicts inline."""
    out = demo_run.stdout
    assert "Payload summary" in out
    assert "ComputedReactionUpload" in out
    for marker in ("Identity:", "Calculations:", "Scientific blocks:",
                   "Artifacts:", "Diagnostics:"):
        assert marker in out
    # Reaction-specific signals.
    assert "reactants:" in out
    assert "kinetics:" in out
    assert "H_Abstraction" in out


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
    assert "Emission diagnostics" in out
    # The two pre-Phase-7 forward-compat warnings.
    assert "transport_not_emitted_in_computed_reaction_bundle" in out
    assert (
        "thermo_source_calculations_not_emitted_in_computed_reaction_bundle"
        in out
    )


def test_demo_prints_artifact_diagnostic_code(demo_run):
    """Phase-7 artifact diagnostic must surface for both attached calcs."""
    out = demo_run.stdout
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in out
    # The demo attaches one artifact to a TS calc and one to a
    # species-side calc, so the diagnostic path appears twice (one per
    # calc with attachments).
    assert "calculations[ts opt].artifacts" in out
    assert "calculations[ch4 sp].artifacts" in out


def test_demo_prints_artifact_summary(demo_run):
    out = demo_run.stdout
    assert "== Artifacts ==" in out
    # Per-bucket lines — TS and CH4 species — and the per-kind text.
    assert "output_log" in out
    # The explanatory second-phase note is part of the summary.
    assert "second phase" in out
    assert "upload.artifact_plan" in out
    assert "client.upload_artifacts" in out


def test_demo_prints_artifact_plan_preview_with_mock_ids(demo_run):
    """No-server mode should render the artifact plan against synthetic
    calculation ids so producers can see the planned shape."""
    out = demo_run.stdout
    assert "Artifact plan preview" in out
    # Two artifacts in two distinct calcs → two plan entries.
    plan_entries = [
        line for line in out.splitlines()
        if line.startswith("  - calc_key=")
    ]
    assert len(plan_entries) >= 2
    # The mock ids are deterministic; checking for the keys is more
    # informative than pinning the exact integer.
    assert any("calc_key=ts_opt" in line for line in plan_entries)
    assert any("calc_key=ch4_sp" in line for line in plan_entries)


def test_demo_does_not_attempt_network_without_env_vars(demo_run):
    out = demo_run.stdout
    err = demo_run.stderr
    # Short-circuit text is the load-bearing signal.
    assert "skipping live upload" in out
    # Stderr should be empty (or at most warnings, no transport errors).
    # Specifically guard against accidental httpx connect attempts.
    for needle in ("ConnectionError", "ConnectError", "Failed to establish a new connection"):
        assert needle not in err, (
            f"demo attempted network without env vars; stderr:\n{err}"
        )


def test_demo_writes_tempdir_for_artifact_files(demo_run):
    """The temp directory prefix appears in the artifact summary lines
    so producers can find and reuse the demo files. Verifies the
    fake-file materialisation actually ran without requiring the test
    to inspect the filesystem after the fact."""
    out = demo_run.stdout
    assert "tckdb-builder-demo-" in out


def test_demo_diagnostic_codes_match_public_constants(demo_run):
    """The codes printed by the demo must match ``DIAG_CODES`` —
    catches a future renaming that drifted between the demo and the
    public token contract."""
    out = demo_run.stdout
    assert (
        DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in out
    )
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in out
    )
    assert (
        DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE
        in out
    )
