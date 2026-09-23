"""Tests for the schema-reference generator, and for the committed document's freshness.

``docs/guides/schema_reference.md`` is the page a depositor reads to look up
what a table or column means without opening ``app/db/models/``, so a
document that can go stale without anything noticing is worse than no
document -- it is a confident wrong answer. This mirrors
``tests/scripts/test_generate_dbml.py`` in shape and for the same reason:
that file's docstring records the day ``schema.dbml`` silently disagreed with
the models for a full day because nothing compared the two. Two different
claims are made here, same as there:

* the generator *renders* correctly -- given a docstring shaped like this,
  does it extract the role/purpose/column meaning it should (and, just as
  important, refuse to extract one it should not);
* the *committed file* matches what the live models render today.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_GENERATOR = Path(__file__).parents[2] / "scripts" / "generate_schema_reference.py"
_COMMITTED = Path(__file__).parents[3] / "docs" / "guides" / "schema_reference.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_schema_reference", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Role derivation: exactly one role word trusted, everything else refused
# ---------------------------------------------------------------------------


def test_a_single_stated_role_is_trusted() -> None:
    generator = _load_generator()
    assert generator._derive_role("One append-only statement. This row is curation overlay.") == "curation"


def test_an_undocumented_class_has_no_role() -> None:
    generator = _load_generator()
    assert generator._derive_role(None) == generator.ROLE_NOT_STATED
    assert generator._derive_role("Store one thing. No role word here at all.") == generator.ROLE_NOT_STATED


def test_an_incidental_mention_of_two_role_words_is_not_guessed() -> None:
    """Regression against the real case: ``calc_geometry_validation`` says

    "preserves the intended molecular identity" -- "identity" is present, but
    the table is evidence about a *result*, not a self-declaration of an
    identity bucket. Two distinct role words in one docstring means neither
    is trusted, rather than picking the first (or any) one by convention.
    """
    generator = _load_generator()
    doc = (
        "Evidence that a calculation's output geometry preserves the intended "
        "molecular identity. This is the result of an automated check."
    )
    assert generator._derive_role(doc) == generator.ROLE_NOT_STATED


# ---------------------------------------------------------------------------
# Purpose: first paragraph, verbatim, collapsed to one line
# ---------------------------------------------------------------------------


def test_purpose_is_the_first_paragraph_only() -> None:
    generator = _load_generator()
    doc = """One-line summary that
    wraps across two lines.

    A second paragraph that must NOT appear in the purpose cell.
    """
    purpose = generator._first_paragraph(doc)
    assert purpose == "One-line summary that wraps across two lines."
    assert "second paragraph" not in purpose


# ---------------------------------------------------------------------------
# Column meanings: only a block that OPENS with the column's own backticked
# name is attributed to it. See the generator module docstring's "Never
# invent a description" section for why this is deliberately conservative.
# ---------------------------------------------------------------------------


def test_a_leading_backtick_bullet_documents_its_column() -> None:
    generator = _load_generator()
    doc = """Summary paragraph.

    * ``foo_id`` is the thing this column points at, spanning
      a second wrapped line that must be joined in.
    * ``bar`` is unrelated.
    """
    found = generator._extract_documented_columns(doc, {"foo_id", "bar"})
    assert found["foo_id"].startswith("``foo_id`` is the thing")
    assert "second wrapped line" in found["foo_id"]
    assert found["bar"] == "``bar`` is unrelated."


def test_two_columns_joined_by_a_slash_are_both_documented() -> None:
    generator = _load_generator()
    doc = "* ``h298_kj_mol`` / ``s298_j_mol_k`` are the standard enthalpy and entropy."
    found = generator._extract_documented_columns(doc, {"h298_kj_mol", "s298_j_mol_k"})
    assert found["h298_kj_mol"] == found["s298_j_mol_k"]
    assert "standard enthalpy" in found["h298_kj_mol"]


def test_a_definition_list_term_documents_its_column() -> None:
    generator = _load_generator()
    doc = """Summary.

    ``kind``
        Says what stands behind the row, spanning a second
        indented line.
    """
    found = generator._extract_documented_columns(doc, {"kind"})
    assert found["kind"].startswith("Says what stands behind the row")
    assert "second" in found["kind"]


def test_an_incidental_backtick_mention_is_not_attributed() -> None:
    """The column name appears, but not at the head of a structured block --
    this must NOT be read as documenting it. This is the honesty invariant
    the whole document exists to keep: a wrong attribution is worse than
    printing "not documented".
    """
    generator = _load_generator()
    doc = (
        "This table records evidence. The optimiser may have changed the "
        "molecule, and ``foo_id`` is mentioned here only in passing, not as "
        "a definition."
    )
    found = generator._extract_documented_columns(doc, {"foo_id"})
    assert found == {}


def test_an_undocumented_column_is_absent_not_invented() -> None:
    generator = _load_generator()
    found = generator._extract_documented_columns("* ``foo`` is documented.", {"foo", "bar"})
    assert "bar" not in found


# ---------------------------------------------------------------------------
# The committed document
# ---------------------------------------------------------------------------


def test_committed_schema_reference_is_in_sync() -> None:
    """``docs/guides/schema_reference.md`` must be what the ORM models render today."""
    generator = _load_generator()

    assert _COMMITTED.exists(), (
        f"{_COMMITTED} is missing. Regenerate it: "
        "conda run -n tckdb_env python scripts/generate_schema_reference.py"
    )

    diff = generator.diff_against_committed()

    assert not diff, (
        "docs/guides/schema_reference.md is out of date with app/db/models/.\n"
        "Regenerate it: conda run -n tckdb_env python scripts/generate_schema_reference.py\n\n" + diff[:8000]
    )
    # The CLI path CI and humans actually use, not only the function.
    assert generator.main(["--check"]) == 0


def test_the_generator_is_deterministic() -> None:
    """Guard the guard: a generator that varied per run would fire at random.

    ``Table.constraints`` and ``col.foreign_keys`` are plain ``set``s. If any
    traversal stopped sorting them, the sync test above would go red on
    unchanged models -- the exact "fires on churn" failure it exists to
    avoid -- and the obvious response would be to delete it.
    """
    generator = _load_generator()
    first = generator.generate_schema_reference()
    second = generator.generate_schema_reference()
    assert first == second, "generate_schema_reference() is not stable across calls"


def test_the_sync_check_can_actually_fail() -> None:
    """Bite test: a document whose purpose line drifted from the model must be reported.

    Without this the sync test is a claim about a comparison nobody has seen
    fail. Reproduces a real class of drift -- a docstring reworded in the
    model but not regenerated into the document -- against a temporary copy,
    so the committed file is never touched.
    """
    generator = _load_generator()
    rendered = generator.generate_schema_reference()

    lines = rendered.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("**Purpose:** ") and "not documented" not in line:
            replacement = "**Purpose:** stale, nothing regenerated it.\n"
            staled = "".join([*lines[:index], replacement, *lines[index + 1 :]])
            break
    else:  # pragma: no cover - the document has well over a hundred of these
        raise AssertionError("no populated '**Purpose:**' line in the generated document")

    diff = generator.diff_against_committed(staled)

    assert diff, "a stale purpose line was not reported as drift"
    assert "stale, nothing regenerated it." in diff
