"""Frequency level of theory on correction schemes (composite-levels plan P6)

``energy_correction_scheme.frequency_level_of_theory_id`` (nullable FK to
``level_of_theory``) joins scheme identity.

Why
---
Arkane keys Petersson and Melius BAC, and some atom-energy tables, on
``CompositeLevelOfTheory(freq=..., energy=...)``: the same energy level with
two different frequency levels is two parameter sets. A scheme held one level
of theory (the energy half), so the frequency half was lost and two such
schemes either collapsed into one row or were refused as a value conflict.
The owner's decision (2026-10-01, decision 10): the frequency level joins
identity.

Identity
--------
Both partial unique identity indexes from ``f2c8a5d1e9b7`` are replaced by
versions with ``frequency_level_of_theory_id`` added, still
``NULLS NOT DISTINCT``:

* ``uq_energy_correction_scheme_identity`` (``WHERE data_revision IS NULL``)
* ``uq_energy_correction_scheme_identity_revised``
  (``WHERE data_revision IS NOT NULL``)

Every existing row has a NULL frequency level, and under NULLS NOT DISTINCT
two NULLs are equal, so every existing row keeps exactly the identity it had:
nothing that was unique becomes duplicate, nothing that was a duplicate
becomes unique. No backfill: a frequency level is not recoverable from a
stored row, and NULL means "no frequency level stated". The public ref string
of a NULL-frequency row is byte-identical to what it was.

Accepted-science immutability
-----------------------------
A scheme is reference data, not an accepted-science table, and this revision
adds a nullable column and issues no UPDATE or INSERT.

Merged levels of theory
-----------------------
``merge_duplicate_levels_of_theory.py`` reads every foreign key into
``level_of_theory`` from ``pg_constraint`` at run time, so the new column is
found without a change to it, and a scheme that cites a duplicate as its
frequency level blocks that group, exactly as it does for ``level_of_theory_id``.
New uploads cannot create that situation: the resolver follows
``level_of_theory_merge`` for the frequency level as for the energy level.

Downgrade
---------
Drops the column and restores the two indexes without it. Two schemes that
differ only in their frequency level would be duplicates under the older
indexes, so the downgrade refuses, naming the number of colliding groups and
rows, when such rows exist, rather than deleting or merging them. It also
prints how many stored frequency levels it is about to forget.

Revision ID: c5e1a8d3f6b9
Revises: a7d3f1c95e28
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c5e1a8d3f6b9"
down_revision: Union[str, Sequence[str], None] = "a7d3f1c95e28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEME = "energy_correction_scheme"
_COLUMN = "frequency_level_of_theory_id"
_FK = "fk_energy_correction_scheme_frequency_level_of_theory_id"
_LEGACY_INDEX = "uq_energy_correction_scheme_identity"
_REVISED_INDEX = "uq_energy_correction_scheme_identity_revised"

_OLD_LEGACY_COLUMNS = [
    "kind",
    "name",
    "level_of_theory_id",
    "source_literature_id",
    "software_release_id",
    "workflow_tool_release_id",
]
_OLD_REVISED_COLUMNS = [
    "kind",
    "name",
    "level_of_theory_id",
    "source_literature_id",
    "software_release_id",
    "data_revision",
]


def _with_frequency(columns: list[str]) -> list[str]:
    """The identity columns with the frequency level after the energy level."""
    at = columns.index("level_of_theory_id") + 1
    return [*columns[:at], _COLUMN, *columns[at:]]


# Groups of rows the OLDER indexes would call duplicates: same old identity
# columns, within the same partial index. GROUP BY treats NULLs as equal,
# which is what NULLS NOT DISTINCT does.
_DOWNGRADE_COLLISIONS = sa.text(
    f"""
    SELECT count(*) AS groups, coalesce(sum(n), 0) AS member_rows FROM (
        SELECT count(*) AS n
        FROM {_SCHEME}
        WHERE data_revision IS NULL
        GROUP BY {", ".join(_OLD_LEGACY_COLUMNS)}
        HAVING count(*) > 1
        UNION ALL
        SELECT count(*) AS n
        FROM {_SCHEME}
        WHERE data_revision IS NOT NULL
        GROUP BY {", ".join(_OLD_REVISED_COLUMNS)}
        HAVING count(*) > 1
    ) AS g
    """
)


def upgrade() -> None:
    op.add_column(_SCHEME, sa.Column(_COLUMN, sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        _FK,
        _SCHEME,
        "level_of_theory",
        [_COLUMN],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )

    # Existing rows are all NULL in the new column, and NULLS NOT DISTINCT
    # makes NULL equal NULL, so the widened indexes accept exactly the rows
    # the old ones did.
    op.drop_index(_LEGACY_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.drop_index(_REVISED_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.create_index(
        _LEGACY_INDEX,
        _SCHEME,
        _with_frequency(_OLD_LEGACY_COLUMNS),
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NULL"),
    )
    op.create_index(
        _REVISED_INDEX,
        _SCHEME,
        _with_frequency(_OLD_REVISED_COLUMNS),
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    groups, member_rows = bind.execute(_DOWNGRADE_COLLISIONS).one()
    if groups:
        raise RuntimeError(
            f"Cannot downgrade: {groups} group(s) ({member_rows} rows) of "
            "energy_correction_scheme differ only in "
            "frequency_level_of_theory_id. The older identity indexes would "
            "call each group duplicates. Nothing has been changed. Resolve "
            "them by hand, deciding which scheme each referencing "
            "applied_energy_correction row should keep, then retry."
        )

    stated = bind.execute(
        sa.text(f"SELECT count(*) FROM {_SCHEME} WHERE {_COLUMN} IS NOT NULL")
    ).scalar_one()
    print(f"c5e1a8d3f6b9 downgrade forgets: {stated} scheme frequency level(s).")

    op.drop_index(_REVISED_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.drop_index(_LEGACY_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.create_index(
        _LEGACY_INDEX,
        _SCHEME,
        _OLD_LEGACY_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NULL"),
    )
    op.create_index(
        _REVISED_INDEX,
        _SCHEME,
        _OLD_REVISED_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NOT NULL"),
    )
    op.drop_constraint(_FK, _SCHEME, type_="foreignkey")
    op.drop_column(_SCHEME, _COLUMN)
