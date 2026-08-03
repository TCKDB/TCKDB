"""Tests for the DBML generator's constraint rendering.

``schema.dbml`` is the document people read to understand the schema, so a
guarantee the database enforces but the document omits is worse than a missing
file -- it is a confident wrong answer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, UniqueConstraint

_GENERATOR = Path(__file__).parents[2] / "scripts" / "generate_dbml.py"


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
