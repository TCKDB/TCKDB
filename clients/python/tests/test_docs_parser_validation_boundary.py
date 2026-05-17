"""Docs-link sanity test for ``parser_validation_boundary.md``.

Design-only doc today — no implementation, no parsers shipped. This
test pins:

- the doc exists at the path other docs reference,
- it covers every prompted section,
- the load-bearing rules are spelled out (no Gaussian/ORCA parsing
  in builders, parsers must not silently override builder values,
  backend remains authoritative, base ``tckdb-client`` ships
  parser-free),
- artifacts are explicitly described as the audit trail,
- the two cross-linking docs (``builder_api_mvp.md`` and ``README.md``)
  actually link to it.

If any of these drift apart, the test fails before producers see
the rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_README = _ROOT / "README.md"
_SPEC = _DOCS / "parser_validation_boundary.md"
_MVP = _DOCS / "builder_api_mvp.md"


# ---------------------------------------------------------------------
# Existence + structural coverage
# ---------------------------------------------------------------------


def test_spec_doc_exists():
    assert _SPEC.is_file(), (
        f"Parser/validation boundary spec missing at {_SPEC}."
    )


@pytest.mark.parametrize(
    "heading",
    [
        "## 1. Purpose",
        "## 2. What builders should do",
        "## 3. What a future parser / ingestion layer should do",
        "## 4. What workflow-tool adapters should do",
        "## 5. Backend authority",
        "## 6. Cross-checks and warnings",
        "## 7. Artifacts and auditability",
        "## 8. Packaging recommendation",
        "## 9. Example future flow",
        "## 10. Non-goals",
    ],
)
def test_spec_doc_covers_section(heading):
    text = _SPEC.read_text(encoding="utf-8")
    assert heading in text, (
        f"Parser/validation boundary spec is missing heading: {heading!r}"
    )


def test_spec_doc_is_design_only():
    text = _SPEC.read_text(encoding="utf-8").lower()
    assert "design / spec only" in text or "design-only" in text, (
        "Spec must mark itself design-only at the top."
    )
    assert "no parser implementation" in text, (
        "Spec must state no parser implementation in this task."
    )


# ---------------------------------------------------------------------
# Load-bearing rules
# ---------------------------------------------------------------------


def test_builders_do_not_parse_ess_files():
    """The single most important rule this doc encodes: the base
    builder layer must not parse ESS files. The doc has to spell
    out the ESS programs by name so a future contributor can grep
    for the rule."""
    text = _SPEC.read_text(encoding="utf-8")
    assert "Builders must not" in text, (
        "Spec must include a 'Builders must not' list."
    )
    for ess in ("Gaussian", "ORCA", "Arkane"):
        assert ess in text, (
            f"Spec should name {ess!r} explicitly in the builder/"
            "parser boundary."
        )
    # Some form of "do not parse" / "no parsing" appears near the
    # ESS names — the explicit rule, not just an aside.
    lowered = text.lower()
    assert (
        "parse gaussian" in lowered
        or "parse gaussian, orca" in lowered
        or "parse gaussian / orca" in lowered
    ), "Spec must literally state builders do not parse Gaussian/ORCA files."


def test_parsers_must_not_silently_override_builder_values():
    text = _SPEC.read_text(encoding="utf-8")
    # The exact phrasing matters less than the rule being explicit.
    lowered = text.lower()
    assert "never silently modify" in lowered or "must not silently modify" in lowered, (
        "Spec must state parsers cannot silently modify builder values."
    )
    # The doc should at minimum describe the worked example
    # (parsed charge/multiplicity vs builder-supplied charge/multiplicity).
    assert "charge" in text and "multiplicity" in text, (
        "Spec should illustrate the charge/multiplicity mismatch case."
    )


def test_backend_remains_authoritative():
    text = _SPEC.read_text(encoding="utf-8")
    assert "authoritative" in text, (
        "Spec must say the backend is authoritative."
    )
    # The §5 list should call out the things the backend owns.
    for item in (
        "Deduplication",
        "Permissions",
        "Schema validation",
    ):
        assert item in text, (
            f"Spec §5 should name {item!r} as a backend responsibility."
        )
    assert "Client-side validation is **convenience only**" in text or \
        "Client-side validation is convenience only" in text, (
            "Spec must state client-side validation is convenience only."
        )


def test_spec_recommends_parser_free_base_client():
    text = _SPEC.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "parser-free" in lowered, (
        "Spec must recommend a parser-free base client somewhere."
    )
    # The packaging section must spell out that RDKit / ESS deps
    # are not transitive dependencies of the base install.
    assert "RDKit" in text, (
        "Spec should explicitly mention RDKit in the packaging boundary."
    )
    assert "must not pull in" in text or "does not pull in" in text, (
        "Spec should explicitly forbid pulling ESS/RDKit deps into base install."
    )


def test_spec_mentions_artifacts_as_audit_trail():
    text = _SPEC.read_text(encoding="utf-8")
    assert "audit" in text.lower(), (
        "Spec §7 must describe artifacts as an audit trail."
    )
    assert "Artifacts and auditability" in text, (
        "Spec must have an Artifacts and auditability section."
    )
    # The doc should call out that audit survives parser absence —
    # i.e. an upload made today remains parseable later.
    assert "without parser" in text.lower() or "incomplete parser support" in text.lower(), (
        "Spec should argue audit works even without parsers shipped."
    )


# ---------------------------------------------------------------------
# Cross-links from other docs
# ---------------------------------------------------------------------


def test_mvp_doc_links_to_spec():
    text = _MVP.read_text(encoding="utf-8")
    assert "parser_validation_boundary.md" in text, (
        "builder_api_mvp.md should link to the parser/validation "
        "boundary spec."
    )


def test_readme_links_to_spec():
    text = _README.read_text(encoding="utf-8")
    assert "parser_validation_boundary.md" in text, (
        "README should link to the parser/validation boundary spec."
    )


# ---------------------------------------------------------------------
# Non-goals — the doc explicitly lists what is NOT being done.
# ---------------------------------------------------------------------


def test_spec_non_goals_list_is_explicit():
    text = _SPEC.read_text(encoding="utf-8")
    # A handful of items from §10 that should be visibly forbidden.
    for forbidden in (
        "No parser implementation",
        "No backend schema changes",
        "No RDKit dependency",
        "No ESS-specific logic in core builders",
        "No ARC-specific",
        "No automatic correction",
    ):
        assert forbidden in text, (
            f"Spec §10 should explicitly list {forbidden!r} as a non-goal."
        )
