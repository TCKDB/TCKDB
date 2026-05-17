"""Docs-policy tests for the conformer semantic boundary.

This file replaces the previous ``test_docs_multi_conformer_design.py``.

It pins three things at once:

- The new policy doc exists at the documented path.
- The previously-shipped multi-conformer **design** doc and its test
  are gone, and no client doc references them anywhere.
- The names of the rejected future APIs (``conformers=[…]``,
  ``species_conformers``, ``selected_conformer``) appear **only**
  in the boundary doc itself — as rejected terms — and nowhere
  else in client-facing markdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_BOUNDARY = _DOCS / "conformer_semantic_boundary.md"
_OLD_DESIGN = _DOCS / "builder_multi_conformer_design.md"
_OLD_TEST = _ROOT / "tests" / "test_docs_multi_conformer_design.py"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Boundary doc exists and has the load-bearing content
# ---------------------------------------------------------------------


def test_boundary_doc_exists():
    assert _BOUNDARY.is_file(), (
        f"Conformer semantic-boundary doc missing at {_BOUNDARY}."
    )


def test_boundary_doc_states_principle_explicitly():
    text = _BOUNDARY.read_text(encoding="utf-8").lower()
    assert "not a conformer-search scratchpad" in text, (
        "Boundary doc must state the principle in §1 explicitly."
    )


def test_boundary_doc_rejects_selected_conformer():
    text = _BOUNDARY.read_text(encoding="utf-8").lower()
    # The boundary doc is the *only* place ``selected_conformer`` is
    # allowed to appear in client docs (as a rejected term). It must
    # be there, framed as a rejection.
    assert "selected_conformer" in text
    # Confirm the rejection framing — at least one of the rejection
    # phrases the policy uses.
    assert any(
        phrase in text
        for phrase in (
            "must not introduce a global `selected_conformer`",
            "no `selected_conformer` field",
            "no selected_conformer",
        )
    ), "Boundary doc must reject selected_conformer in framing."


def test_boundary_doc_distinguishes_independent_from_candidate_dumping():
    """The §4 case table is the load-bearing content distinguishing
    independent submissions (acceptable) from one workflow dumping its
    candidate list (rejected)."""
    text = _BOUNDARY.read_text(encoding="utf-8").lower()
    assert "independently" in text and "candidate" in text
    # The §4 table contrasts the two outcomes explicitly.
    assert "scratchpad" in text or "workflow" in text


def test_boundary_doc_covers_required_sections():
    text = _BOUNDARY.read_text(encoding="utf-8")
    required_headings = [
        "## 1. TCKDB is not a conformer-search scratchpad",
        "## 2. Default builder model",
        "## 3. No `selected_conformer` concept",
        "## 4. Multiple submissions for the same species",
        "## 5. Advanced edge cases",
        "## 6. Artifacts and provenance",
        "## 7. Non-goals",
    ]
    for heading in required_headings:
        assert heading in text, f"Boundary doc missing heading: {heading!r}"


# ---------------------------------------------------------------------
# Previous multi-conformer design + its test are gone
# ---------------------------------------------------------------------


def test_old_multi_conformer_design_doc_is_removed():
    assert not _OLD_DESIGN.exists(), (
        f"Old multi-conformer design doc still exists at {_OLD_DESIGN}; "
        "it must be removed."
    )


def test_old_multi_conformer_test_file_is_removed():
    assert not _OLD_TEST.exists(), (
        f"Old multi-conformer test file still exists at {_OLD_TEST}; "
        "it must be removed."
    )


# ---------------------------------------------------------------------
# Doc cross-links updated correctly
# ---------------------------------------------------------------------


def test_mvp_doc_links_to_boundary_doc():
    text = _MVP.read_text(encoding="utf-8")
    assert "conformer_semantic_boundary.md" in text, (
        "builder_api_mvp.md should link to the conformer boundary doc."
    )


def test_mvp_doc_does_not_link_to_old_design_doc():
    text = _MVP.read_text(encoding="utf-8")
    assert "builder_multi_conformer_design.md" not in text, (
        "builder_api_mvp.md still references the removed design doc."
    )


def test_readme_links_to_boundary_doc():
    text = _README.read_text(encoding="utf-8")
    assert "conformer_semantic_boundary.md" in text, (
        "README should link to the conformer boundary doc."
    )


def test_readme_does_not_link_to_old_design_doc():
    text = _README.read_text(encoding="utf-8")
    assert "builder_multi_conformer_design.md" not in text, (
        "README still references the removed design doc."
    )


# ---------------------------------------------------------------------
# Rejected-API names appear ONLY in the boundary doc (markdown surface)
# ---------------------------------------------------------------------


def _markdown_files_excluding_boundary() -> list[Path]:
    return [
        p for p in (*_DOCS.glob("*.md"), _README)
        if p.resolve() != _BOUNDARY.resolve()
    ]


@pytest.mark.parametrize(
    "rejected_token",
    [
        "selected_conformer",
        "species_conformers",
        # Builder kwarg form — the dict-literal in raw-payload talk is
        # different (uses ``conformers: [...]``) so we pin the kwarg
        # token specifically.
        "conformers=[",
    ],
)
def test_rejected_api_token_appears_only_in_boundary_doc(rejected_token):
    """The rejected API names may appear in the boundary doc (as
    rejected terms in §3 / §7) but must not leak into the MVP spec,
    stability doc, or README — those documents would otherwise look
    like they're advertising a feature TCKDB has decided not to ship.
    """
    offenders: list[Path] = []
    for path in _markdown_files_excluding_boundary():
        if rejected_token in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(_ROOT))
    assert not offenders, (
        f"Rejected token {rejected_token!r} found in client markdown "
        f"outside the boundary doc: {offenders}. Move the mention "
        "into ``docs/conformer_semantic_boundary.md`` or remove it."
    )


def test_no_remaining_multi_conformer_phrase_in_client_docs():
    """The literal phrase 'multi-conformer' was the headline term of
    the rescinded design. It must not appear in any client markdown
    doc — including the boundary doc, which deliberately frames the
    boundary in terms of "one scientifically meaningful conformer per
    upload" rather than re-using the rescinded label.
    """
    offenders: list[Path] = []
    for path in (*_DOCS.glob("*.md"), _README):
        if "multi-conformer" in path.read_text(encoding="utf-8").lower():
            offenders.append(path.relative_to(_ROOT))
    assert not offenders, (
        f"'multi-conformer' phrase still present in client markdown: "
        f"{offenders}. The rescinded label should not appear; use "
        "phrasing like 'one conformer per species' instead."
    )
