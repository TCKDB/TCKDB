"""Tests for the DBML generator, and for the committed document's freshness.

``schema.dbml`` is the document people read to understand the schema, so a
guarantee the database enforces but the document omits is worse than a missing
file -- it is a confident wrong answer.

Two different claims are made here, and only one of them used to be:

* the generator *renders* correctly -- given a table with these constraints,
  does it emit the right lines;
* the *committed file* matches the live models.

Only the first was tested, and the first is satisfied by a file nobody ever
reads. ``HessianDetectionContext.reclaim_restore`` was added to
``app/db/models/common.py`` and was missing from ``backend/schema.dbml`` for a
full day with every test green, because nothing compared the two.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, UniqueConstraint

_GENERATOR = Path(__file__).parents[2] / "scripts" / "generate_dbml.py"
_COMMITTED = Path(__file__).parents[2] / "schema.dbml"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_dbml", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _render(table: Table) -> list[str]:
    generator = _load_generator()
    return generator._render_indexes(table, pk_col_names={"id"})


def test_a_non_unique_index_does_not_suppress_a_unique_constraint() -> None:
    """A lookup index over the same columns is not the constraint.

    Regression: ``uq_record_review_record`` vanished from ``schema.dbml`` when
    ``ix_record_review_record_lookup`` was added over the same
    ``(record_type, record_id)`` pair. The constraint was untouched in the
    database, so the document silently under-reported what the schema
    guarantees. Matching on the column set alone cannot tell a uniqueness
    guarantee from a lookup aid.
    """
    metadata = MetaData()
    table = Table(
        "record_review_like",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("record_type", String),
        Column("record_id", Integer),
        UniqueConstraint("record_type", "record_id", name="uq_pair"),
        Index("ix_pair_lookup", "record_type", "record_id"),  # NOT unique
    )

    rendered = "\n".join(_render(table))

    assert "uq_pair" in rendered, (
        "a unique constraint covered only by a non-unique index must still be "
        f"rendered; got:\n{rendered}"
    )


def test_a_unique_index_does_suppress_the_duplicate_constraint() -> None:
    """The original de-duplication still holds where it is actually true.

    When the covering index is itself unique it already renders ``unique`` for
    that column set, so emitting the constraint too would double-report one
    guarantee.
    """
    metadata = MetaData()
    table = Table(
        "unique_index_table",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("a", String),
        Column("b", Integer),
        UniqueConstraint("a", "b", name="uq_ab"),
        Index("ix_ab", "a", "b", unique=True),
    )

    rendered = "\n".join(_render(table))

    assert "uq_ab" not in rendered, (
        f"a unique index already conveys this constraint; got:\n{rendered}"
    )
    assert "unique" in rendered


# ---------------------------------------------------------------------------
# The committed document
# ---------------------------------------------------------------------------


def test_committed_schema_dbml_is_in_sync() -> None:
    """``backend/schema.dbml`` must be what the ORM models render today.

    The failure message separates *schema* drift from *prose* drift on
    purpose. A gate that fires on movement carrying no information trains
    people to regenerate reflexively without reading the diff -- and a gate
    nobody reads is a gate that will wave a real drift through. So the
    generator classifies its own diff: a changed column, index, check or enum
    member says "schema", a changed ``Note:`` (the first line of an ORM class
    docstring) says "table descriptions only", and the reader is told which
    before they open anything.

    Both still fail. A stale description is a wrong description, and the fix
    for either is the same one command.
    """
    generator = _load_generator()

    assert _COMMITTED.exists(), (
        f"{_COMMITTED} is missing. Regenerate it: "
        "conda run -n tckdb_env python scripts/generate_dbml.py"
    )

    diff, structural = generator.diff_against_committed()

    assert not diff, (
        f"backend/schema.dbml is out of date with app/db/models/ "
        f"({'schema drift' if structural else 'table descriptions only'}).\n"
        "Regenerate it: conda run -n tckdb_env python scripts/generate_dbml.py\n"
        "Read the diff before you commit it -- this gate exists because the "
        "document was wrong about an enum member for a day and nothing "
        "noticed.\n\n" + diff[:8000]
    )
    # The CLI path CI and humans actually use, not only the function.
    assert generator.main(["--check"]) == 0


def test_the_generator_is_deterministic() -> None:
    """Guard the guard: a generator that varied per run would fire at random.

    ``Table.indexes``, ``Table.constraints`` and ``Table.foreign_key_constraints``
    are plain ``set``s. If any traversal stopped sorting them, the sync test
    above would go red on unchanged models -- the exact "fires on churn"
    failure it is written to avoid -- and the obvious response would be to
    delete it.
    """
    generator = _load_generator()
    first = generator.generate_dbml()
    second = generator.generate_dbml()
    assert first == second, "generate_dbml() is not stable across calls"


def test_the_sync_check_can_actually_fail() -> None:
    """Bite test: a document missing an enum member must be reported.

    Without this the sync test is a claim about a comparison nobody has seen
    fail. Reproduces the real case -- an enum member present in
    ``app/db/models/common.py`` and absent from the committed file -- against
    a temporary copy, so the committed file is never touched.
    """
    generator = _load_generator()
    rendered = generator.generate_dbml()

    lines = rendered.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == "reclaim_restore":
            staled = "".join(lines[:index] + lines[index + 1 :])
            break
    else:  # pragma: no cover - the member is asserted to exist below
        raise AssertionError(
            "HessianDetectionContext.reclaim_restore is no longer rendered into "
            "the DBML; pick another enum member for this bite test."
        )

    diff, structural = generator.diff_against_committed(staled)

    assert diff, "a missing enum member was not reported as drift"
    assert structural, "a missing enum member is schema drift, not prose"
    assert "reclaim_restore" in diff


def test_a_reworded_docstring_is_classified_as_prose() -> None:
    """The other half of the classification, so it is not wishful thinking."""
    generator = _load_generator()
    rendered = generator.generate_dbml()

    lines = rendered.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("Note: '"):
            lines[index] = "  Note: 'reworded, nothing structural'\n"
            break
    else:  # pragma: no cover - the document has hundreds of these
        raise AssertionError("no Note: line in the generated DBML")

    diff, structural = generator.diff_against_committed("".join(lines))

    assert diff, "a changed table note was not reported at all"
    assert not structural, (
        "a changed table note was classified as schema drift; the whole point "
        "of the split is that a reader can tell them apart"
    )
