"""Docs-link sanity test for ``builder_summary_design.md``.

Design-only doc — no implementation yet. This test pins:

- the doc exists at the path other docs reference,
- it covers every prompted section,
- the load-bearing rules are spelled out (summary is not a second
  schema, ``.to_text()`` is not stable, ``.to_dict()`` keys are
  public beta, the §4 exclusions are explicit),
- the two cross-linking docs (``builder_api_mvp.md`` and
  ``README.md``) actually link to it.

If any of these drift apart, the test fails before producers see
the rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_SPEC = _DOCS / "builder_summary_design.md"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Existence + structural coverage
# ---------------------------------------------------------------------


def test_spec_doc_exists():
    assert _SPEC.is_file(), (
        f"Builder summary design spec missing at {_SPEC}."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "## 0. Why this surface exists",
        "## 1. API name",
        "## 2. Return type",
        "## 3. What `summary()` should include",
        "## 4. What `summary()` should NOT include",
        "## 5. Relationship to emission diagnostics",
        "## 6. Relationship to artifact planning",
        "## 7. Stability",
        "## 8. Example",
        "## 9. Test plan for future implementation",
        "## 10. Non-goals",
        "## 11. Suggested implementation phases",
    ],
)
def test_spec_doc_covers_section(heading):
    text = _SPEC.read_text(encoding="utf-8")
    assert heading in text, (
        f"Builder summary spec is missing heading: {heading!r}"
    )


def test_spec_doc_states_phase_1_shipped():
    """Phase 1 (``UploadSummary`` + ``upload.summary()``) shipped in
    ``tckdb-client`` 0.26.0. The doc keeps the original design framing
    for context but the status line must reflect that Phase 1 is now
    implemented; the test pins that so the doc and the released helper
    don't drift apart silently."""
    text = _SPEC.read_text(encoding="utf-8").lower()
    assert "phase 1 now shipped" in text, (
        "Spec must state Phase 1 has shipped."
    )
    assert "0.26.0" in text, (
        "Spec must name the release that shipped Phase 1."
    )


# ---------------------------------------------------------------------
# Recommendations are explicit
# ---------------------------------------------------------------------


def test_spec_recommends_summary_method_name():
    """The §1 recommendation must name ``summary()`` so a rename
    later is a deliberate public-facing decision."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "upload.summary()" in text, (
        "Spec must show the recommended ``upload.summary()`` call."
    )
    assert "**Recommended.**" in text, (
        "Spec must mark exactly one API-name candidate as Recommended."
    )


def test_spec_recommends_to_text_and_to_dict_surface():
    """§2 must commit to the ``.to_text()`` + ``.to_dict()`` shape."""
    text = _SPEC.read_text(encoding="utf-8")
    assert ".to_text()" in text, (
        "Spec must specify the ``.to_text()`` emission method."
    )
    assert ".to_dict()" in text, (
        "Spec must specify the ``.to_dict()`` emission method."
    )
    assert "UploadSummary" in text, (
        "Spec must name the recommended wrapper type ``UploadSummary``."
    )


# ---------------------------------------------------------------------
# Load-bearing rules
# ---------------------------------------------------------------------


def test_spec_lists_required_fields_for_both_uploads():
    """§3 must enumerate the species-side and reaction-side digest
    fields the test plan will pin."""
    text = _SPEC.read_text(encoding="utf-8")
    # Species-side keys.
    for key in (
        "species_smiles",
        "charge",
        "multiplicity",
        "calculation_counts_by_type",
        "primary_calculation_label",
        "has_thermo",
        "has_statmech",
        "has_transport",
        "artifact_count",
        "diagnostic_count",
        "diagnostic_codes",
    ):
        assert key in text, (
            f"Spec §3 should list the species-side summary key {key!r}."
        )
    # Reaction-side keys.
    for key in (
        "reactant_smiles",
        "product_smiles",
        "reaction_family",
        "kinetics_count",
        "ts_calculation_counts_by_type",
    ):
        assert key in text, (
            f"Spec §3 should list the reaction-side summary key {key!r}."
        )


def test_spec_excludes_payload_and_xyz_and_base64():
    """§4 must explicitly forbid embedding the wire payload, raw
    XYZ blocks, base64 content, full NASA coefficients, and
    full frequency lists."""
    text = _SPEC.read_text(encoding="utf-8")
    for forbidden in (
        "full payload JSON",
        "XYZ",
        "base64",
        "NASA coefficients",
        "frequency lists",
        "Database / server-minted IDs",
    ):
        assert forbidden in text, (
            f"Spec §4 should explicitly exclude {forbidden!r}."
        )


def test_spec_pins_to_text_not_stable_to_dict_stable():
    """§7 stability layering: ``.to_dict()`` keys are public beta;
    ``.to_text()`` formatting is not stable."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "Not stable" in text or "not stable" in text, (
        "Spec §7 must call out that ``.to_text()`` formatting is not stable."
    )
    assert "Public beta" in text, (
        "Spec §7 must mark the public-beta surfaces explicitly."
    )
    # ``DIAG_CODES`` is the existing stability anchor for codes.
    assert "DIAG_CODES" in text, (
        "Spec §7 should reference ``DIAG_CODES`` as the diagnostic "
        "code stability anchor."
    )


def test_spec_keeps_to_payload_authoritative():
    """The §0 rule: the summary must not become a second schema."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "to_payload()" in text, (
        "Spec must reference the authoritative ``to_payload()`` "
        "representation."
    )
    lowered = text.lower()
    assert "not a second schema" in lowered or "not a substitute" in lowered, (
        "Spec must state the summary is not a second schema / "
        "not a substitute for ``to_payload()``."
    )


def test_spec_artifact_summary_count_only():
    """§6 must scope summary's artifact reporting to counts; full
    plans remain on ``artifact_plan(...)`` / ``artifact_plan_preview()``."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "artifact_count" in text, (
        "Spec §6 should name the ``artifact_count`` field."
    )
    assert "artifact_plan_preview" in text, (
        "Spec §6 should preserve ``artifact_plan_preview`` as the "
        "detail surface."
    )
    assert "artifact_plan" in text, (
        "Spec §6 should reference ``artifact_plan(result)``."
    )


def test_spec_diagnostic_codes_only_not_messages():
    """§5 must restrict the summary's diagnostic detail to codes /
    counts; long messages remain on ``emission_diagnostics()``."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "diagnostic_codes" in text, (
        "Spec §5 should name the ``diagnostic_codes`` summary key."
    )
    assert "emission_diagnostics()" in text, (
        "Spec §5 should preserve ``emission_diagnostics()`` as the "
        "detail surface."
    )
    # The §5 design rule: messages do not enter the summary.
    lowered = text.lower()
    assert "no diagnostic messages" in lowered, (
        "Spec §5 should state diagnostic messages do not enter the summary."
    )


# ---------------------------------------------------------------------
# Cross-links from other docs
# ---------------------------------------------------------------------


def test_mvp_doc_links_to_spec():
    text = _MVP.read_text(encoding="utf-8")
    assert "builder_summary_design.md" in text, (
        "builder_api_mvp.md should link to the builder summary "
        "design spec."
    )


def test_readme_links_to_spec():
    text = _README.read_text(encoding="utf-8")
    assert "builder_summary_design.md" in text, (
        "README should link to the builder summary design spec."
    )


# ---------------------------------------------------------------------
# Non-goals — the doc explicitly lists what is NOT being built.
# ---------------------------------------------------------------------


def test_spec_non_goals_list_is_explicit():
    text = _SPEC.read_text(encoding="utf-8")
    for forbidden in (
        "No implementation in this task",
        "No backend schema changes",
        "No server-side",
        "No full payload pretty-printer",
        "No `rich`",
        "No notebook-specific rendering",
    ):
        assert forbidden in text, (
            f"Spec §10 should explicitly list {forbidden!r} as a non-goal."
        )
