"""Tests for ``examples/builder_computed_reaction_demo.ipynb``.

Two layers of coverage:

- A **structural** test that only depends on the stdlib ``json``
  module. It runs in any environment the rest of the test suite
  runs in, and pins:

    - the file exists, is parseable JSON, and is nbformat ≥ 4.5,
    - every cell has the required ``cell_type``, ``source``, and
      stable ``id`` fields,
    - the documented section markdown headings appear in order,
    - the artifact diagnostic code is referenced somewhere.

- An **executable** test that runs the notebook end-to-end via
  ``jupyter nbconvert --execute``. Skipped automatically when
  ``jupyter`` / ``nbconvert`` / a kernel are not installed, so the
  rest of the suite stays green on slim environments. When the
  notebook does run, it must:

    - exit without raising (no live server, no ``TCKDB_BASE_URL``),
    - print the same artifact diagnostic code the public API exposes,
    - print the mock plan preview shape.

The notebook is a sibling of the ``.py`` script demo — keeping the
two in lockstep is enforced by both files having matching expected
output strings.
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
    / "builder_computed_reaction_demo.ipynb"
)


# ---------------------------------------------------------------------
# Structural test — stdlib only
# ---------------------------------------------------------------------


def test_notebook_file_exists():
    assert _NB_PATH.is_file(), (
        f"Demo notebook missing at {_NB_PATH}; README links to it."
    )


def test_notebook_is_valid_nbformat_45():
    raw = _NB_PATH.read_text(encoding="utf-8")
    nb = json.loads(raw)
    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] >= 5
    assert nb["metadata"]["kernelspec"]["name"] == "python3"
    assert isinstance(nb["cells"], list)
    assert len(nb["cells"]) >= 10  # the demo has 18 cells today

    seen_ids: set[str] = set()
    for i, c in enumerate(nb["cells"]):
        assert "cell_type" in c, f"cell {i} missing cell_type"
        assert c["cell_type"] in {"markdown", "code"}
        assert "source" in c and isinstance(c["source"], list), (
            f"cell {i} source must be a list of strings (nbformat 4.5)"
        )
        # 4.5 wants every cell to have an id; we set them deterministically
        # at generation time.
        assert "id" in c, f"cell {i} missing id"
        assert c["id"] not in seen_ids, f"duplicate cell id {c['id']!r}"
        seen_ids.add(c["id"])
        if c["cell_type"] == "code":
            # Code cells in 4.5 must carry execution_count + outputs slots.
            assert "execution_count" in c
            assert "outputs" in c and isinstance(c["outputs"], list)


def test_notebook_section_headings_in_order():
    """The notebook is the demo's documented walk; a future refactor
    that drops a section heading will surface here before it surfaces
    on a producer."""
    nb = json.loads(_NB_PATH.read_text(encoding="utf-8"))
    md_text = "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"
    )
    expected_headings = [
        "## 1 · Imports and shared constants",
        "## 2 · Materialise tiny stand-in artifact files",
        "## 3 · Build the upload",
        "## 4 · Payload summary",
        "## 5 · Emission diagnostics",
        "## 6 · Artifact summary",
        "## 7 · Artifact plan preview",
        "## 8 · Live upload",
    ]
    last = 0
    for h in expected_headings:
        idx = md_text.find(h, last)
        assert idx >= 0, f"heading missing from notebook: {h!r}"
        last = idx + 1


def test_notebook_references_artifact_diagnostic_code():
    """The notebook's narrative must surface the artifact diagnostic
    code or invoke ``emission_diagnostics`` so the producer sees it
    after running the cell."""
    nb = json.loads(_NB_PATH.read_text(encoding="utf-8"))
    code_text = "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )
    assert "emission_diagnostics" in code_text
    assert "artifact_plan" in code_text
    # The mock IDs branch — exercises the offline preview path via the
    # public ``artifact_plan_preview`` helper (Phase-8 cleanup).
    assert "artifact_plan_preview" in code_text


def test_notebook_references_sibling_script():
    """The notebook should link the ``.py`` demo so producers know
    the two are equivalent."""
    nb = json.loads(_NB_PATH.read_text(encoding="utf-8"))
    text = "".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"
    )
    assert "builder_computed_reaction_demo.py" in text


# ---------------------------------------------------------------------
# Executable test — runs the notebook end-to-end via jupyter
# ---------------------------------------------------------------------


def _jupyter_available() -> bool:
    """``jupyter nbconvert`` is part of the Phase-7 dev env but isn't
    required to run the structural test above. Skip the executable
    test when it isn't installed so slim CI environments stay green.
    """
    if shutil.which("jupyter") is None:
        return False
    try:
        import nbconvert  # noqa: F401
        import jupyter_client  # noqa: F401
        # A kernel spec must exist or ``--execute`` fails late.
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
    """Run the notebook headless without env vars and assert the
    outputs match the documented behaviour. ``--execute`` raises a
    non-zero exit code if any cell raises, so a clean returncode here
    means every cell ran without error."""
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
    assert out_path.is_file(), (
        f"executed notebook not written to {out_path}"
    )

    executed = json.loads(out_path.read_text(encoding="utf-8"))

    def _flatten_text(value) -> str:
        # nbformat outputs may carry ``text`` as either a single
        # string or a list of strings; ``data`` mime-bundles are
        # similar. Normalise both into a flat string so substring
        # checks below work uniformly.
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

    # --- canonical no-server output markers ---------------------------
    # ``upload.summary().to_text()`` replaces the demo-local
    # ``payload_summary(payload)`` helper. The section markers are the
    # stable public contract; exact wording around them may evolve.
    assert "ComputedReactionUpload" in code_stdout
    for marker in ("Identity:", "Calculations:", "Scientific blocks:",
                   "Artifacts:", "Diagnostics:"):
        assert marker in code_stdout
    assert "H_Abstraction" in code_stdout
    # Reactant / product species labels still surface; ordering and
    # punctuation are not stable, the label tokens themselves are.
    for label in ("CH3", "H", "CH4"):
        assert label in code_stdout

    # Emission diagnostics: all three relevant codes appear.
    from tckdb_client.builders import DIAG_CODES

    assert DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE in code_stdout
    assert (
        DIAG_CODES.THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
        in code_stdout
    )
    assert DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE in code_stdout

    # Artifact summary lines.
    assert "2 artifact(s) across 2 calculation(s)" in code_stdout
    assert "ts opt" in code_stdout and "ch4 sp" in code_stdout

    # Mock-IDs plan preview.
    assert "calc_key=ts_opt" in code_stdout
    assert "calc_key=ch4_sp" in code_stdout

    # No-server short-circuit reached.
    assert "skipping live upload" in code_stdout


@jupyter_only
def test_notebook_does_not_attempt_network_when_executed(tmp_path):
    """A clean second pass of the executable test, asserting that
    ``stderr`` is free of httpx-style connection errors. Keeps the
    no-network contract auditable separately from the output checks."""
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
    assert proc.returncode == 0
    for needle in (
        "ConnectionError",
        "ConnectError",
        "Failed to establish a new connection",
    ):
        assert needle not in proc.stderr, (
            f"notebook attempted network without env vars; stderr:\n{proc.stderr}"
        )
