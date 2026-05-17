"""Smoke test for ``examples/builder_arc_style_dry_run.ipynb``.

Sibling of the ``.py`` ARC-style dry-run; the two stay in lockstep.
Two layers of coverage:

- A **structural** test (stdlib only) — every cell has an id, every
  required section heading appears in order, and the cell body uses
  only public APIs (no private upload attrs, no inline
  ``calculation_keys`` synthesis, no conformer-policy violations).
- An **executable** test (``@jupyter_only``) — runs the notebook
  headless via ``jupyter nbconvert --execute`` and asserts the
  documented output markers appear, with ``stderr`` free of network
  errors.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


_NB_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "builder_arc_style_dry_run.ipynb"
)


# ---------------------------------------------------------------------
# Structural test — stdlib only
# ---------------------------------------------------------------------


def test_notebook_file_exists():
    assert _NB_PATH.is_file(), (
        f"ARC-style notebook missing at {_NB_PATH}; README links to it."
    )


def test_notebook_is_valid_nbformat_45():
    raw = _NB_PATH.read_text(encoding="utf-8")
    nb = json.loads(raw)
    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] >= 5
    assert isinstance(nb["cells"], list)
    assert len(nb["cells"]) >= 20, (
        "ARC-style notebook is a 12-section walk and should have "
        "≥20 cells; got fewer."
    )
    seen_ids: set[str] = set()
    for i, c in enumerate(nb["cells"]):
        assert "cell_type" in c
        assert c["cell_type"] in {"markdown", "code"}
        assert "source" in c and isinstance(c["source"], list), (
            f"cell {i} source must be a list of strings (nbformat 4.5)"
        )
        assert "id" in c, f"cell {i} missing id"
        assert c["id"] not in seen_ids, f"duplicate cell id {c['id']!r}"
        seen_ids.add(c["id"])
        if c["cell_type"] == "code":
            assert "execution_count" in c
            assert "outputs" in c and isinstance(c["outputs"], list)


def test_notebook_section_headings_in_order():
    nb = json.loads(_NB_PATH.read_text(encoding="utf-8"))
    md_text = "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"
    )
    expected = [
        "## 1 · Imports and shared constants",
        "## 2 · Materialise tiny stand-in artifact files",
        "## 3 · Build the calculation trios",
        "## 4 · Source-calculation provenance with `SourceCalculations`",
        "## 5 · Assemble the `ComputedReactionUpload`",
        "## 6 · `upload.summary().to_text()` — preview surface",
        "## 7 · Emission diagnostics",
        "## 8 · Workflow → builder mapping",
        "## 9 · Artifact summary",
        "## 10 · Artifact plan preview (offline, mock IDs)",
        "## 11 · Truncated wire payload preview",
        "## 12 · Optional: live upload",
    ]
    last = 0
    for h in expected:
        idx = md_text.find(h, last)
        assert idx >= 0, f"heading missing from notebook: {h!r}"
        last = idx + 1


def test_notebook_uses_public_apis_only():
    """The notebook is the same public-surface contract as the .py
    dry-run; the static guards from
    ``test_builder_arc_style_dry_run`` apply here too."""
    code_text = _code_text()
    for forbidden in (
        "_species_calc_pairs",
        "_species_thermo_pairs",
        "_species_statmech_pairs",
        "_species_transport_pairs",
    ):
        assert forbidden not in code_text, (
            f"notebook reaches into private upload state ({forbidden!r})."
        )
    for marker in ('"calculation_keys":', "'calculation_keys':"):
        assert marker not in code_text, (
            f"notebook mocks ``calculation_keys`` inline ({marker!r}); "
            "use ``upload.artifact_plan_preview()`` instead."
        )


def test_notebook_exercises_required_public_helpers():
    code_text = _code_text()
    for helper in (
        "summary()",
        "emission_diagnostics",
        "iter_calculation_entries",
        "artifact_plan_preview",
        "to_payload",
        "SourceCalculations(",
    ):
        assert helper in code_text, (
            f"notebook should exercise the public helper {helper!r}."
        )


def test_notebook_uses_note_kwarg_on_calculation_factories():
    """Phase 0.26.3 added ``note=`` to the three factories; the
    notebook should demonstrate it so producers see the new surface."""
    code_text = _code_text()
    assert "note=" in code_text, (
        "notebook should demonstrate the new ``note=`` kwarg on "
        "Calculation.opt/freq/sp."
    )


def test_notebook_respects_conformer_boundary():
    nb_text = _NB_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "selected_conformer",
        "species_conformers",
        "conformer_candidates",
        "candidate_conformers",
    ):
        assert forbidden not in nb_text, (
            f"notebook reintroduces rejected conformer-boundary token "
            f"{forbidden!r}."
        )


def test_notebook_references_sibling_script():
    text = _NB_PATH.read_text(encoding="utf-8")
    assert "builder_arc_style_dry_run.py" in text


def _code_text() -> str:
    nb = json.loads(_NB_PATH.read_text(encoding="utf-8"))
    return "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )


# ---------------------------------------------------------------------
# Executable test — runs the notebook end-to-end via jupyter
# ---------------------------------------------------------------------


def _jupyter_available() -> bool:
    if shutil.which("jupyter") is None:
        return False
    try:
        import nbconvert  # noqa: F401
        import jupyter_client  # noqa: F401
        kspecs = subprocess.run(
            ["jupyter", "kernelspec", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if kspecs.returncode != 0:
            return False
        data = json.loads(kspecs.stdout or "{}")
        return bool(data.get("kernelspecs"))
    except Exception:
        return False


jupyter_only = pytest.mark.skipif(
    not _jupyter_available(),
    reason="jupyter / nbconvert / kernelspec not available in this env",
)


@jupyter_only
def test_notebook_executes_end_to_end(tmp_path):
    env = dict(os.environ)
    env.pop("TCKDB_BASE_URL", None)
    env.pop("TCKDB_API_KEY", None)

    out_path = tmp_path / "executed.ipynb"
    proc = subprocess.run(
        [
            sys.executable, "-m", "jupyter", "nbconvert",
            "--to", "notebook",
            "--execute",
            "--output", str(out_path),
            "--output-dir", str(tmp_path),
            str(_NB_PATH),
        ],
        capture_output=True, text=True, env=env, timeout=180,
    )
    assert proc.returncode == 0, (
        f"nbconvert --execute failed:\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    assert out_path.is_file()

    executed = json.loads(out_path.read_text(encoding="utf-8"))

    def _flatten_text(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(_flatten_text(v) for v in value)
        return ""

    code_stdout_parts: list[str] = []
    for c in executed["cells"]:
        if c["cell_type"] != "code":
            continue
        for out in c.get("outputs", []):
            code_stdout_parts.append(_flatten_text(out.get("text", "")))
            for mime, payload in out.get("data", {}).items():
                if mime == "text/plain":
                    code_stdout_parts.append(_flatten_text(payload))
    code_stdout = "".join(code_stdout_parts)

    # Stable summary section markers.
    for marker in ("Identity:", "Calculations:", "Scientific blocks:",
                   "Artifacts:", "Diagnostics:"):
        assert marker in code_stdout

    # Reaction-shaped signals.
    assert "H_Abstraction" in code_stdout
    for label in ("CH4", "OH", "CH3", "H2O"):
        assert label in code_stdout

    # Diagnostic codes the workflow shape triggers.
    from tckdb_client.builders import DIAG_CODES
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in code_stdout
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in code_stdout
    )

    # ``SourceCalculations`` surfaced indirectly via kinetics source-calc
    # count (2 reactants + 2 products + ts_energy + freq = 6).
    assert "kinetics[0] source_calculations: 6 entries" in code_stdout

    # Mock-IDs plan preview.
    assert "calc_key=ts_opt" in code_stdout
    assert "calc_key=ch3_sp" in code_stdout

    # No-server short-circuit reached.
    assert "skipping live upload" in code_stdout


@jupyter_only
def test_notebook_does_not_attempt_network_when_executed(tmp_path):
    env = dict(os.environ)
    env.pop("TCKDB_BASE_URL", None)
    env.pop("TCKDB_API_KEY", None)
    out_path = tmp_path / "executed.ipynb"
    proc = subprocess.run(
        [
            sys.executable, "-m", "jupyter", "nbconvert",
            "--to", "notebook",
            "--execute",
            "--output", str(out_path),
            "--output-dir", str(tmp_path),
            str(_NB_PATH),
        ],
        capture_output=True, text=True, env=env, timeout=180,
    )
    err = proc.stderr
    for needle in (
        "ConnectionError",
        "ConnectError",
        "Failed to establish a new connection",
    ):
        assert needle not in err, (
            f"notebook attempted network; stderr:\n{err}"
        )
