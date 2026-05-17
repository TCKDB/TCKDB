"""Docs-link sanity test for ``calculation_note_conventions.md``.

Pins the producer-facing conventions doc so it cannot silently drift
out of step with the builder behaviour it describes:

- doc exists at the path other docs reference,
- README and ``builder_api_mvp.md`` link to it,
- the load-bearing rules are spelled out (``note`` is builder-local,
  not emitted on either upload path, artifacts are the right home
  for logs/files, conformer-candidate-list narratives are forbidden,
  the conformer-boundary policy is linked).
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_SPEC = _DOCS / "calculation_note_conventions.md"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Existence + structural coverage
# ---------------------------------------------------------------------


def test_spec_doc_exists():
    assert _SPEC.is_file(), (
        f"Calculation note conventions spec missing at {_SPEC}."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "## 1. Purpose",
        "## 2. Current emission behaviour",
        "## 3. Good uses",
        "## 4. Bad uses",
        "## 5. Relationship to artifacts",
        "## 6. Relationship to the conformer boundary",
        "## 7. Future backend support",
        "## 8. Non-goals",
    ],
)
def test_spec_doc_covers_section(heading):
    text = _SPEC.read_text(encoding="utf-8")
    assert heading in text, (
        f"Calculation note conventions spec is missing heading: {heading!r}"
    )


# ---------------------------------------------------------------------
# Load-bearing rules
# ---------------------------------------------------------------------


def test_spec_states_note_is_builder_local():
    text = _SPEC.read_text(encoding="utf-8")
    assert "builder-local" in text, (
        "Spec must explicitly call ``Calculation.note`` builder-local."
    )
    assert "not emitted" in text.lower(), (
        "Spec must say the field is not emitted on the wire."
    )


def test_spec_pins_both_payload_paths_as_not_emitting():
    """§2 must spell out that *both* upload paths skip the note —
    otherwise a producer might assume only one path drops it."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "ComputedSpeciesUpload.to_payload()" in text
    assert "ComputedReactionUpload.to_payload()" in text


def test_spec_links_conformer_boundary():
    text = _SPEC.read_text(encoding="utf-8")
    assert "conformer_semantic_boundary.md" in text, (
        "Spec §6 must link the conformer-boundary policy."
    )


def test_spec_says_artifacts_are_for_logs_and_files():
    """§5 — the bytes story belongs to artifacts, not notes."""
    text = _SPEC.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "artifacts" in lowered, "Spec must reference artifacts."
    # The §5 framing must explicitly call out logs / files / inputs /
    # outputs as artifact concerns, not note concerns.
    for noun in ("log", "file"):
        assert noun in lowered, (
            f"Spec §5 should reference {noun!r} as an artifact concern."
        )
    # The §5 rule explicitly says input/output files belong to artifacts.
    assert "input / output files" in text or "input/output files" in text, (
        "Spec §5 should state input/output files belong on artifacts."
    )


def test_spec_rejects_conformer_candidate_lists_in_notes():
    """§6 — notes are not a smuggle channel for candidate-conformer
    narratives or selected-of-N flags.

    The conformer-boundary policy reserves the literal forbidden
    token names to a single doc
    (``test_docs_conformer_semantic_boundary.py`` pins that),
    so this test asserts the conventions doc rejects the *concept*
    by description rather than by token.
    """
    text = _SPEC.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "candidate" in lowered, (
        "Spec §6 should explicitly reject candidate-conformer narratives."
    )
    # "selected from N", "lowest-energy of N candidates" — the
    # workflow-preferred-from-N framing the conventions must rule out.
    assert (
        "selected from" in lowered
        or "preferred-from-n" in lowered
        or "preferred from n" in lowered
        or "workflow-preferred-from-n" in lowered
    ), (
        "Spec §6 should reject the workflow-preferred-from-N narrative "
        "by description."
    )
    # Conformer-boundary policy is cross-linked in §6.
    assert "conformer_semantic_boundary.md" in text, (
        "Spec §6 must reference the conformer-boundary policy doc."
    )


def test_spec_lists_good_and_bad_uses():
    text = _SPEC.read_text(encoding="utf-8")
    # The §3 / §4 framing should mention several of the prompt's
    # exemplar good uses without being a closed list.
    for good in (
        "Single-point refinement",
        "Frequency calculation",
    ):
        assert good in text, (
            f"Spec §3 should list {good!r} as a good-use example."
        )
    # Bad uses cover the worst footguns.
    for bad in (
        "Full log files",
        "Large text blobs",
        "Raw input / output",
    ):
        assert bad in text, (
            f"Spec §4 should list {bad!r} as a bad-use example."
        )


def test_spec_future_backend_section_is_non_binding():
    """§7 sketches likely constraints without promising any of them."""
    text = _SPEC.read_text(encoding="utf-8")
    # The section must be present and must not promise emission;
    # phrasing like "may be added later" / "non-binding sketch" is
    # the safe shape.
    assert "may be added later" in text or "may be added" in text, (
        "Spec §7 should mark future backend emission as non-binding."
    )
    # Must explicitly say notes are not used for deduplication or
    # scientific identity.
    assert "Not used for deduplication" in text or \
        "not used for deduplication" in text.lower(), (
            "Spec §7 must state notes are not used for deduplication."
        )


# ---------------------------------------------------------------------
# Cross-links from other docs
# ---------------------------------------------------------------------


def test_mvp_doc_links_to_spec():
    text = _MVP.read_text(encoding="utf-8")
    assert "calculation_note_conventions.md" in text, (
        "builder_api_mvp.md should link to the calculation note "
        "conventions doc."
    )


def test_readme_links_to_spec():
    text = _README.read_text(encoding="utf-8")
    assert "calculation_note_conventions.md" in text, (
        "README should link to the calculation note conventions doc."
    )


# ---------------------------------------------------------------------
# Non-goals — the doc explicitly lists what is NOT being built.
# ---------------------------------------------------------------------


def test_spec_non_goals_list_is_explicit():
    text = _SPEC.read_text(encoding="utf-8")
    for forbidden in (
        "No backend schema change",
        "No wire emission",
        "No parser implementation",
        "No ARC changes",
    ):
        assert forbidden in text, (
            f"Spec §8 should explicitly list {forbidden!r} as a non-goal."
        )
