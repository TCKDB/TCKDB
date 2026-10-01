"""The ``composite`` calculation type and its result tables (ADR 0021, phase P3a)

Adds what a program-run named composite energy (CBS-QB3, G4, W1U, ...) is
recorded in. ``d7a3f1b9c284`` (P2) gave a level of theory a recipe; this gives a
calculation the energy that recipe produced.

Schema
------
* ``calc_type`` gains ``composite`` (``ALTER TYPE ... ADD VALUE``). Nothing in
  this revision *uses* the new value, which is what PostgreSQL requires when the
  statement runs inside the migration transaction.
* ``composite_assembly`` (new enum): ``program_run``, ``assembled``.
* ``calc_composite_result`` -- 1:1 with ``calculation``. ``assembly`` plus three
  nullable energies in hartree: ``electronic_energy_hartree`` (ZPE-free, every
  term of the recipe included), ``e0_hartree`` (0 K, including the recipe's
  scaled zero-point energy) and ``recipe_zpe_hartree``. ``NULL`` means *not
  stated*; TCKDB never stores a total it computed itself.
* ``calc_composite_term`` -- the optional breakdown: ``(calculation_id,
  term_position, value_hartree)``.

Accepted-science immutability
-----------------------------
Both tables carry the ``tckdb_guard_accepted_child`` trigger on
``calculation_id`` and the TRUNCATE refusal, exactly like ``calc_sp_result`` (see
``c6f2a9d4e7b1``): once a calculation is accepted, its composite energy can no
longer be added to, edited or deleted. ``trg_as_child_<table>`` /
``trg_as_truncate_<table>`` follow the naming ``b6c1f4a8e703`` introduced.

No rows are written
-------------------
Pure DDL. No ``calculation`` can have type ``composite`` before this revision, so
there is nothing to backfill, and no existing table gains a column (so no entry
is needed in ``snapshot_defaults.UNCHANGED_DEFAULTS``).

Downgrade
---------
Refuses, with counts, if any ``composite`` calculation or any composite result or
term row exists: removing the type would otherwise delete evidence or leave a
calculation of a type that no longer exists. It does not delete them; remove the
composite calculations first. Otherwise it drops the triggers and both tables,
drops ``composite_assembly``, and rebuilds ``calc_type`` without ``composite``
(PostgreSQL cannot remove an enum value in place: rename, recreate, recast,
drop), the precedent ``b8f3d6a1c9e4`` set for ``molecule_kind``.

Revision ID: f3b7d2a9c514
Revises: d7a3f1b9c284
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3b7d2a9c514"
down_revision: Union[str, Sequence[str], None] = "d7a3f1b9c284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TYPE = "calc_type"
_NEW_VALUE = "composite"
_PRIOR_TYPE_VALUES = ("opt", "freq", "sp", "irc", "scan", "path_search", "conf")

_RESULT = "calc_composite_result"
_TERM = "calc_composite_term"

_ASSEMBLY = postgresql.ENUM("program_run", "assembled", name="composite_assembly", create_type=False)

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child``
#: against the accepted root named directly on the row. Read by
#: ``tests/db/test_accepted_science_trigger_registry.py`` like the other
#: extension revisions' registries.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = (
    (_RESULT, "calculation", "calculation_id"),
    (_TERM, "calculation", "calculation_id"),
)

#: Neither table reaches its root through a parent row.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = ()

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = (_RESULT, _TERM)


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``b6c1f4a8e703``'s naming, truncated to PostgreSQL's 63 bytes."""
    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the direct registry to one trigger per ``(table, record_type)``."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def upgrade() -> None:
    # ADD VALUE is safe inside Alembic's transaction on PostgreSQL 12+ as long
    # as the value is not used in the same transaction, which it is not.
    op.execute(f"ALTER TYPE {_TYPE} ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")
    _ASSEMBLY.create(op.get_bind(), checkfirst=True)

    op.create_table(
        _RESULT,
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("assembly", _ASSEMBLY, nullable=False),
        sa.Column("electronic_energy_hartree", sa.Float(), nullable=True),
        sa.Column("e0_hartree", sa.Float(), nullable=True),
        sa.Column("recipe_zpe_hartree", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "electronic_energy_hartree IS NULL OR "
            "(electronic_energy_hartree > '-Infinity'::float8 AND electronic_energy_hartree < 'Infinity'::float8)",
            name=op.f("ck_calc_composite_result_electronic_energy_finite"),
        ),
        sa.CheckConstraint(
            "e0_hartree IS NULL OR (e0_hartree > '-Infinity'::float8 AND e0_hartree < 'Infinity'::float8)",
            name=op.f("ck_calc_composite_result_e0_finite"),
        ),
        sa.CheckConstraint(
            "recipe_zpe_hartree IS NULL OR (recipe_zpe_hartree >= 0 AND recipe_zpe_hartree < 'Infinity'::float8)",
            name=op.f("ck_calc_composite_result_recipe_zpe_non_negative_finite"),
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_composite_result_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("calculation_id", name=op.f("pk_calc_composite_result")),
    )

    op.create_table(
        _TERM,
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("term_position", sa.SmallInteger(), nullable=False),
        sa.Column("value_hartree", sa.Float(), nullable=False),
        sa.CheckConstraint("term_position >= 0", name=op.f("ck_calc_composite_term_term_position_non_negative")),
        sa.CheckConstraint(
            "value_hartree > '-Infinity'::float8 AND value_hartree < 'Infinity'::float8",
            name=op.f("ck_calc_composite_term_value_finite"),
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_composite_term_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("calculation_id", "term_position", name=op.f("pk_calc_composite_term")),
    )

    for table, record_type, columns in _child_groups():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_child", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child(
                '{record_type}', {arguments}
            )
            """
        )
    for table in _TRUNCATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_truncate", table)}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def _refuse_if_any(description: str, query: str) -> None:
    count = op.get_bind().execute(sa.text(query)).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} {description}. Removing the 'composite' calculation "
            "type would delete that evidence or strand calculations of a type that no longer "
            "exists. Remove those calculations first, then downgrade."
        )


def downgrade() -> None:
    _refuse_if_any(
        "calculation row(s) of type 'composite'",
        f"SELECT count(*) FROM calculation WHERE type = '{_NEW_VALUE}'",
    )
    _refuse_if_any(f"{_RESULT} row(s)", f"SELECT count(*) FROM {_RESULT}")
    _refuse_if_any(f"{_TERM} row(s)", f"SELECT count(*) FROM {_TERM}")

    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_table(_TERM)
    op.drop_table(_RESULT)
    op.execute("DROP TYPE IF EXISTS composite_assembly")

    # PostgreSQL cannot drop an enum value in place: rename, recreate, recast, drop.
    prior = ", ".join(f"'{value}'" for value in _PRIOR_TYPE_VALUES)
    op.execute(f"ALTER TYPE {_TYPE} RENAME TO {_TYPE}_old")
    op.execute(f"CREATE TYPE {_TYPE} AS ENUM ({prior})")
    op.execute(f"ALTER TABLE calculation ALTER COLUMN type TYPE {_TYPE} USING type::text::{_TYPE}")
    op.execute(f"DROP TYPE {_TYPE}_old")
