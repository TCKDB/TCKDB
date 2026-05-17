"""Docs-link sanity tests for the examples README and the back-links
that point at ``adapter_authoring_quickstart.md`` from the boundary /
stability / summary / source-calc design docs.

Two related concerns share this module:

- ``examples/README.md`` must list every shipped demo (the two
  computed-* scripts, the reaction notebook, the ARC-style dry-run)
  and must link the adapter quickstart so a reader landing in
  ``examples/`` has a one-click path to the producer-facing guide.
- The five upstream design docs that already host a ``See also``
  footer (or for which the prompt allows one) must back-link the
  adapter quickstart so the navigation closes both ways.

No rejected conformer-boundary tokens may slip into the new
``examples/README.md`` — the existing
``test_docs_conformer_semantic_boundary.py`` rule is enforced here
as a defensive double-check.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES_README = _ROOT / "examples" / "README.md"
_QUICKSTART_FILENAME = "adapter_authoring_quickstart.md"


# ---------------------------------------------------------------------
# examples/README.md
# ---------------------------------------------------------------------


def test_examples_readme_exists():
    assert _EXAMPLES_README.is_file(), (
        f"examples README missing at {_EXAMPLES_README}; package "
        "README points adapter authors here."
    )


@pytest.mark.parametrize(
    "demo",
    [
        "builder_computed_species_demo.py",
        "builder_computed_reaction_demo.py",
        "builder_computed_reaction_demo.ipynb",
        "builder_arc_style_dry_run.py",
    ],
)
def test_examples_readme_references_each_demo(demo):
    text = _EXAMPLES_README.read_text(encoding="utf-8")
    assert demo in text, (
        f"examples/README.md must reference {demo!r}; otherwise a "
        "reader landing there will miss the example."
    )


def test_examples_readme_links_adapter_quickstart():
    text = _EXAMPLES_README.read_text(encoding="utf-8")
    # Relative path from examples/ to docs/ is ../docs/.
    assert f"../docs/{_QUICKSTART_FILENAME}" in text, (
        "examples/README.md must link the adapter authoring quickstart "
        "via a relative path that survives a ``cd examples`` open."
    )


def test_examples_readme_links_builder_api_mvp():
    text = _EXAMPLES_README.read_text(encoding="utf-8")
    assert "../docs/builder_api_mvp.md" in text, (
        "examples/README.md should link the full builder spec for "
        "readers ready to leave the quickstart."
    )


def test_examples_readme_does_not_introduce_rejected_tokens():
    """The conformer-boundary policy reserves its rejected literal
    token names to a single doc; this test prevents the new
    ``examples/README.md`` from accidentally leaking them."""
    text = _EXAMPLES_README.read_text(encoding="utf-8")
    for forbidden in (
        "selected_conformer",
        "species_conformers",
        "conformers=[",
    ):
        assert forbidden not in text, (
            f"examples/README.md reintroduces rejected conformer-boundary "
            f"token {forbidden!r}; reference the policy by description "
            "and link the boundary doc instead."
        )


# ---------------------------------------------------------------------
# Back-links into adapter_authoring_quickstart.md
# ---------------------------------------------------------------------


_BACKLINK_TARGETS = [
    "parser_validation_boundary.md",
    "conformer_semantic_boundary.md",
    "source_calculation_ergonomics.md",
    "builder_summary_design.md",
    "builder_api_stability.md",
]


@pytest.mark.parametrize("doc", _BACKLINK_TARGETS)
def test_design_doc_back_links_adapter_quickstart(doc):
    """Every upstream design doc that hosts a ``See also`` section
    (and the stability doc, where a minimal one now lives) must
    back-link the adapter quickstart, so an adapter author who lands
    on a deep design doc can find the producer-facing path in one
    click."""
    path = _ROOT / "docs" / doc
    assert path.is_file(), f"design doc missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert "## See also" in text, (
        f"{doc} should host a ``See also`` section that hosts the "
        "back-link to the adapter quickstart."
    )
    assert _QUICKSTART_FILENAME in text, (
        f"{doc} must back-link the adapter authoring quickstart so "
        "the design-doc → quickstart navigation closes."
    )
