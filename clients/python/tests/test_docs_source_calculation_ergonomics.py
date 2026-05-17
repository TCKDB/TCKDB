"""Docs-link sanity test for ``source_calculation_ergonomics.md``.

The doc is design-only today (no implementation), so the only thing
the test suite can verify is that:

- the doc exists at the path the rest of the docs reference,
- it covers each of the 12 + 1 prompted sections (12 questions plus
  §13 non-goals),
- the recommended public symbol name is locked in (any rename here is
  a public-facing decision that should be deliberate, not accidental),
- the two cross-linking docs that reference it (``builder_api_mvp.md``
  and ``README.md``) actually link to it.

If any of these drift apart, the test fails before producers see the
rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_SPEC = _DOCS / "source_calculation_ergonomics.md"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Existence + structural coverage
# ---------------------------------------------------------------------


def test_spec_doc_exists():
    assert _SPEC.is_file(), (
        f"Source-calculation ergonomics spec missing at {_SPEC}."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "## 0. Core principles",
        "## 1. Should there be a `SourceCalculations` helper?",
        "## 2. Alternative: role-list / free-function helpers",
        "## 3. Recommended API",
        "## 4. Kinetics-specific helper",
        "## 5. Naming",
        "## 6. Canonical internal representation",
        "## 7. Validation responsibilities",
        "## 8. Interaction with existing builders",
        "## 9. Before / after examples",
        "## 10. What we deliberately do **not** do",
        "## 11. Implementation recommendation",
        "## 12. Test plan for future implementation",
        "## 13. Non-goals",
    ],
)
def test_spec_doc_covers_section(heading):
    text = _SPEC.read_text(encoding="utf-8")
    assert heading in text, (
        f"Source-calculation ergonomics spec is missing heading: {heading!r}"
    )


def test_spec_doc_states_phase_1_shipped():
    """Phase 1 (``SourceCalculations``) shipped in ``tckdb-client`` 0.25.0.

    The doc keeps the original design framing for context but the
    status line must reflect that Phase 1 is now implemented; the
    test pins that so the doc and the released helper don't drift
    apart silently.
    """
    text = _SPEC.read_text(encoding="utf-8").lower()
    assert "phase 1 now shipped" in text, (
        "Spec must state Phase 1 has shipped."
    )
    assert "0.25.0" in text, (
        "Spec must name the release that shipped Phase 1."
    )


# ---------------------------------------------------------------------
# Recommended API surface is the canonical name
# ---------------------------------------------------------------------


def test_recommended_helper_is_source_calculations():
    """The recommendation in §3 must name ``SourceCalculations`` —
    renaming is a deliberate public-facing decision that should not
    happen silently."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "SourceCalculations" in text, (
        "Spec must name the recommended helper class."
    )
    # The §3 method surface that demos and Phase-1 will rely on.
    assert ".only(" in text, "Spec must describe .only(*roles)."
    assert ".as_list(" in text, "Spec must describe .as_list()."
    assert ".add(" in text, "Spec must describe the .add(role, calc) escape hatch."


def test_spec_rejects_workflow_tool_presets():
    """The §10 'what we deliberately do not do' list must explicitly
    rule out workflow-tool-specific presets — the single biggest
    footgun the policy guards against."""
    text = _SPEC.read_text(encoding="utf-8")
    # Mentioned by name as rejected.
    for name in ("ARCDefaults", "RMGDefaults", "ArkaneDefaults"):
        assert name in text, (
            f"Spec should name {name!r} as a rejected preset in §10."
        )
    assert "No automatic inference" in text or "No automatic inference." in text


def test_spec_keeps_existing_shapes_valid():
    """Producers must not be forced to migrate — §8 promises that
    the three existing accepted shapes keep working."""
    text = _SPEC.read_text(encoding="utf-8")
    for shape in (
        "dict[str, Calculation]",
        "dict[str, list[Calculation]]",
        "list[tuple[str, Calculation]]",
    ):
        assert shape in text, (
            f"Spec should mention the existing accepted shape {shape!r}."
        )


# ---------------------------------------------------------------------
# Cross-links from other docs
# ---------------------------------------------------------------------


def test_mvp_doc_links_to_spec():
    text = _MVP.read_text(encoding="utf-8")
    assert "source_calculation_ergonomics.md" in text, (
        "builder_api_mvp.md should link to the source-calculation "
        "ergonomics spec."
    )


def test_readme_links_to_spec():
    text = _README.read_text(encoding="utf-8")
    assert "source_calculation_ergonomics.md" in text, (
        "README should link to the source-calculation ergonomics spec."
    )


# ---------------------------------------------------------------------
# Spec respects existing policy boundaries
# ---------------------------------------------------------------------


def test_spec_defers_to_conformer_boundary_policy():
    """The companion conformer-boundary policy is adjacent enough that
    a passing mention helps producers find both docs together."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "conformer_semantic_boundary.md" in text, (
        "Spec should reference the conformer-boundary policy in §0 or "
        "in the 'see also' footer."
    )
