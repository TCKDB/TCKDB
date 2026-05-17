"""Docs-link sanity test for ``adapter_authoring_quickstart.md``.

Pins the producer-facing adapter quickstart so it cannot silently
drift out of step with the boundary docs and builder surfaces it
summarises:

- doc exists at the path other docs reference,
- README and ``builder_api_mvp.md`` link to it,
- it links each of the three boundary docs (parser / conformer /
  note conventions),
- it names ``SourceCalculations``, the two-phase artifact upload,
  ``summary()`` + ``emission_diagnostics()``, and the raw-payload
  escape hatch,
- it does NOT reintroduce any of the rejected conformer-boundary
  literal tokens (those live only in the boundary doc itself —
  see ``test_docs_conformer_semantic_boundary.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_GUIDE = _DOCS / "adapter_authoring_quickstart.md"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Existence + structural coverage
# ---------------------------------------------------------------------


def test_guide_doc_exists():
    assert _GUIDE.is_file(), (
        f"Adapter authoring quickstart missing at {_GUIDE}."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "## 1. Purpose",
        "## 2. The layering model",
        "## 3. The three boundary rules",
        "## 4. Minimal adapter flow",
        "## 5. Mapping responsibilities",
        "## 6. Source calculations",
        "## 7. Artifacts",
        "## 8. Pre-upload inspection",
        "## 9. Raw payload escape hatch",
        "## 10. Example references",
        "## 11. Non-goals",
    ],
)
def test_guide_covers_section(heading):
    text = _GUIDE.read_text(encoding="utf-8")
    assert heading in text, (
        f"Adapter quickstart is missing heading: {heading!r}"
    )


# ---------------------------------------------------------------------
# Cross-links
# ---------------------------------------------------------------------


def test_readme_links_guide():
    text = _README.read_text(encoding="utf-8")
    assert "adapter_authoring_quickstart.md" in text, (
        "README should link the adapter authoring quickstart."
    )


def test_mvp_links_guide():
    text = _MVP.read_text(encoding="utf-8")
    assert "adapter_authoring_quickstart.md" in text, (
        "builder_api_mvp.md should link the adapter authoring quickstart."
    )


def test_guide_links_parser_boundary():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "parser_validation_boundary.md" in text, (
        "Guide §3 must link the parser/validation boundary doc."
    )


def test_guide_links_conformer_boundary():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "conformer_semantic_boundary.md" in text, (
        "Guide §3 must link the conformer semantic boundary doc."
    )


def test_guide_links_calculation_note_conventions():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "calculation_note_conventions.md" in text, (
        "Guide §3 must link the Calculation.note conventions doc."
    )


# ---------------------------------------------------------------------
# Required surface mentions
# ---------------------------------------------------------------------


def test_guide_mentions_source_calculations():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "SourceCalculations" in text, (
        "Guide §6 must mention the ``SourceCalculations`` helper."
    )
    # The recommended call shape — kwargs + .only(...).
    assert ".only(" in text, (
        "Guide §6 must demonstrate ``SourceCalculations.only(...)``."
    )


def test_guide_mentions_two_phase_artifact_upload():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "artifact_plan(" in text, (
        "Guide §7 must reference ``upload.artifact_plan(result)``."
    )
    assert "upload_artifacts(" in text, (
        "Guide §7 must reference ``client.upload_artifacts(plan)``."
    )
    # The phase framing is the load-bearing concept.
    assert "two-phase" in text.lower() or "second phase" in text.lower(), (
        "Guide §7 must spell out the two-phase artifact contract."
    )


def test_guide_mentions_batch_by_calculation_mode():
    """``batch_by_calculation=True`` shipped in 0.27.0 as an opt-in
    fewer-HTTP-requests path. The guide must surface both forms in
    §7 so adapter authors see the trade-off; the load-bearing
    string ``batch_by_calculation=True`` is pinned here so the
    section can't silently drop back to one-form-only."""
    text = _GUIDE.read_text(encoding="utf-8")
    assert "batch_by_calculation=True" in text, (
        "Guide §7 must show the ``batch_by_calculation=True`` form."
    )
    assert "ArtifactUploadBatchResult" in text, (
        "Guide §7 must name ``ArtifactUploadBatchResult`` so adapter "
        "authors know the return shape of batch mode."
    )
    lowered = text.lower()
    assert "atomicity" in lowered or "atomic" in lowered, (
        "Guide §7 must call out that batch mode relies on backend "
        "artifact-batch atomicity."
    )
    assert "default remains sequential" in lowered, (
        "Guide §7 must spell out that the default remains "
        "sequential one-artifact-per-request."
    )


def test_guide_mentions_summary_and_diagnostics():
    text = _GUIDE.read_text(encoding="utf-8")
    assert "summary()" in text, (
        "Guide §8 must reference ``upload.summary()``."
    )
    assert "emission_diagnostics" in text, (
        "Guide §8 must reference ``upload.emission_diagnostics()``."
    )


def test_guide_mentions_raw_payload_escape_hatch():
    text = _GUIDE.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "raw payload" in lowered or "raw-payload" in lowered, (
        "Guide §9 must describe the raw-payload escape hatch."
    )
    # The §9 framing must make it explicit that the path remains
    # supported even when builders don't yet cover a field.
    assert "escape hatch" in lowered, (
        "Guide §9 must label the raw path as an *escape hatch*, not "
        "the default."
    )


def test_guide_lists_worked_example_demos():
    text = _GUIDE.read_text(encoding="utf-8")
    for example in (
        "builder_computed_species_demo.py",
        "builder_computed_reaction_demo.py",
        "builder_arc_style_dry_run.py",
    ):
        assert example in text, (
            f"Guide §10 should reference the {example!r} demo."
        )


# ---------------------------------------------------------------------
# Boundary token hygiene
# ---------------------------------------------------------------------


def test_guide_does_not_use_rejected_conformer_boundary_tokens():
    """The conformer-boundary policy reserves the rejected literal
    tokens to a single doc (``test_docs_conformer_semantic_boundary.py``
    pins that). The adapter guide must reference the *concept* by
    description, never by the forbidden token names."""
    text = _GUIDE.read_text(encoding="utf-8")
    for forbidden in (
        "selected_conformer",
        "species_conformers",
        "conformers=[",
    ):
        assert forbidden not in text, (
            f"Adapter quickstart reintroduces rejected conformer-boundary "
            f"token {forbidden!r}; reference the policy by description "
            "and link the boundary doc instead."
        )


# ---------------------------------------------------------------------
# Non-goals — the doc explicitly states what is NOT being built.
# ---------------------------------------------------------------------


def test_guide_non_goals_list_is_explicit():
    text = _GUIDE.read_text(encoding="utf-8")
    for forbidden in (
        "No parser implementation",
        "No backend schema change",
        "No ARC-specific",
        "No endorsement of workflow scratchpad uploads",
        "No replacement for backend validation",
    ):
        assert forbidden in text, (
            f"Guide §11 should explicitly list {forbidden!r} as a non-goal."
        )
