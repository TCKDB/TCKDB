"""assembled composite inputs and the ``composite_input`` dependency role (ADR 0021, phase P5)

``f3b7d2a9c514`` (P3a) recorded a composite energy a program printed. This
revision lets a composite be *assembled*: arithmetic over other deposited
calculations, the way a user-built scheme (``extrapolation`` or ``additive``,
``composite_scheme``) defines it.

Schema
------
* ``calculation_dependency_role`` gains ``composite_input``
  (``ALTER TYPE ... ADD VALUE``; nothing in this revision uses the new value,
  which PostgreSQL requires when the statement runs in the migration
  transaction).
* ``energy_component_kind`` gains ``correlation_excluding_triples``: the CCSD part
  of a correlation energy, which a scheme term can read (``composite_scheme_term.energy_component``)
  and a single point never stores (the wire refuses it as a stored component; it is
  derived from ``correlation`` and ``triples``). A CHECK on ``calc_sp_energy_component``
  cannot say so here, because PostgreSQL refuses to use an enum value in the
  transaction that added it.
* ``calc_composite_input`` -- one row per slot an assembled composite filled:
  ``(calculation_id, term_position, slot, input_calculation_id)`` is the primary
  key, ``cardinal_number`` is set only on a ``cardinal`` slot, and
  ``(calculation_id, term_position, slot, cardinal_number)`` is unique with NULLs
  compared equal. ``slot`` reuses ``composite_input_slot`` (created by
  ``d7a3f1b9c284``). The row is mirrored by a ``calculation_dependency`` edge with
  the new role (parent = the input calculation, child = the composite); the
  service writes both, and the row carries the slot the edge cannot.

Accepted-science immutability
-----------------------------
The table carries ``tckdb_guard_accepted_child`` on ``calculation_id`` and the
TRUNCATE refusal, exactly like ``calc_composite_result``: once the composite is
accepted its inputs can be neither added to, edited nor removed.
``trg_as_child_<table>`` / ``trg_as_truncate_<table>`` follow ``b6c1f4a8e703``.

No rows are written
-------------------
Pure DDL. A composite with inputs could not be deposited before this revision
(``assembled`` was refused), so there is nothing to backfill, and no existing
table gains a column (so nothing joins ``snapshot_defaults.UNCHANGED_DEFAULTS``).

Downgrade
---------
Refuses, with counts, while any ``calc_composite_input`` row, any scheme term or
single-point component using ``correlation_excluding_triples``, or any
``calculation_dependency`` edge with the ``composite_input`` role exists: dropping
any of them would delete the evidence that an assembled energy rests on the
calculations it names. It deletes nothing; remove the assembled composite
calculations first. Otherwise it drops the triggers and the table and rebuilds
``calculation_dependency_role`` without ``composite_input`` (PostgreSQL cannot
remove an enum value in place: rename, recreate, recast, drop; done for both enums,
``energy_component_kind`` recast on its two columns). The four partial
unique indexes whose predicates name the column are dropped around the recast and
recreated verbatim, because an index predicate cannot be re-parsed against a
retyped column.

Revision ID: b4d8e2f6a1c9
Revises: f3b7d2a9c514
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d8e2f6a1c9"
down_revision: Union[str, Sequence[str], None] = "f3b7d2a9c514"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ROLE_TYPE = "calculation_dependency_role"
_NEW_VALUE = "composite_input"
_PRIOR_ROLE_VALUES = (
    "optimized_from",
    "freq_on",
    "single_point_on",
    "arkane_source",
    "irc_start",
    "irc_followup",
    "scan_parent",
)

_TABLE = "calc_composite_input"

_COMPONENT_TYPE = "energy_component_kind"
_COMPONENT_NEW_VALUE = "correlation_excluding_triples"
_PRIOR_COMPONENT_VALUES = (
    "total",
    "reference",
    "correlation",
    "triples",
    "dboc",
    "scalar_relativistic",
)
#: ``(table, column)`` using ``energy_component_kind``.
_COMPONENT_COLUMNS = (("composite_scheme_term", "energy_component"), ("calc_sp_energy_component", "component"))

_SLOT = postgresql.ENUM("value", "high", "low", "cardinal", name="composite_input_slot", create_type=False)

#: The partial unique indexes whose predicate names ``dependency_role``, as
#: ``d861dfd60891`` created them: ``(index name, role literal)``.
_ROLE_INDEXES: tuple[tuple[str, str], ...] = (
    ("uq_calculation_dependency_child_calculation_id_freq_on", "freq_on"),
    ("uq_calculation_dependency_child_calculation_id_optimized_from", "optimized_from"),
    ("uq_calculation_dependency_child_calculation_id_scan_parent", "scan_parent"),
    ("uq_calculation_dependency_child_calculation_id_single_point_on", "single_point_on"),
)

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child``
#: against the accepted root named directly on the row. Read by
#: ``tests/db/test_accepted_science_trigger_registry.py``. The input calculation
#: is a *citation*, not ownership: accepting an input must not freeze a composite
#: that cites it, so only ``calculation_id`` is registered.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, "calculation", "calculation_id"),)

#: The table does not reach its root through a parent row.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = ()

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = (_TABLE,)


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
    op.execute(f"ALTER TYPE {_ROLE_TYPE} ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")
    op.execute(f"ALTER TYPE {_COMPONENT_TYPE} ADD VALUE IF NOT EXISTS '{_COMPONENT_NEW_VALUE}'")

    op.create_table(
        _TABLE,
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("term_position", sa.SmallInteger(), nullable=False),
        sa.Column("slot", _SLOT, nullable=False),
        sa.Column("input_calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("cardinal_number", sa.Integer(), nullable=True),
        sa.CheckConstraint("term_position >= 0", name=op.f("ck_calc_composite_input_term_position_non_negative")),
        sa.CheckConstraint(
            "cardinal_number IS NULL OR cardinal_number >= 1",
            name=op.f("ck_calc_composite_input_cardinal_number_positive"),
        ),
        sa.CheckConstraint(
            "slot <> 'cardinal' OR cardinal_number IS NOT NULL",
            name=op.f("ck_calc_composite_input_cardinal_slot_needs_number"),
        ),
        sa.CheckConstraint(
            "slot = 'cardinal' OR cardinal_number IS NULL",
            name=op.f("ck_calc_composite_input_cardinal_only_on_cardinal_slot"),
        ),
        sa.CheckConstraint(
            "input_calculation_id <> calculation_id", name=op.f("ck_calc_composite_input_not_its_own_input")
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_composite_input_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["input_calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_composite_input_input_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            "calculation_id", "term_position", "slot", "input_calculation_id", name=op.f("pk_calc_composite_input")
        ),
    )
    op.create_index(
        "uq_calc_composite_input_slot",
        _TABLE,
        ["calculation_id", "term_position", "slot", "cardinal_number"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_calc_composite_input_input_calculation_id", _TABLE, ["input_calculation_id"], unique=False)

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
            f"Cannot downgrade: {count} {description}. Removing the assembled-composite inputs and the "
            "'composite_input' dependency role would delete the evidence that an assembled energy rests on "
            "the calculations it names. Remove those assembled composite calculations first, then downgrade."
        )


def downgrade() -> None:
    _refuse_if_any(f"{_TABLE} row(s)", f"SELECT count(*) FROM {_TABLE}")
    _refuse_if_any(
        f"calculation_dependency edge(s) with role '{_NEW_VALUE}'",
        f"SELECT count(*) FROM calculation_dependency WHERE dependency_role = '{_NEW_VALUE}'",
    )

    for table, column in _COMPONENT_COLUMNS:
        _refuse_if_any(
            f"{table}.{column} value(s) '{_COMPONENT_NEW_VALUE}'",
            f"SELECT count(*) FROM {table} WHERE {column} = '{_COMPONENT_NEW_VALUE}'",
        )

    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_index("ix_calc_composite_input_input_calculation_id", table_name=_TABLE)
    op.drop_index("uq_calc_composite_input_slot", table_name=_TABLE)
    op.drop_table(_TABLE)

    # PostgreSQL cannot drop an enum value in place: rename, recreate, recast,
    # drop. The partial unique indexes on ``dependency_role`` cannot survive the
    # recast, so they are dropped first and recreated verbatim afterwards.
    for name, _ in _ROLE_INDEXES:
        op.drop_index(name, table_name="calculation_dependency")
    prior = ", ".join(f"'{value}'" for value in _PRIOR_ROLE_VALUES)
    op.execute(f"ALTER TYPE {_ROLE_TYPE} RENAME TO {_ROLE_TYPE}_old")
    op.execute(f"CREATE TYPE {_ROLE_TYPE} AS ENUM ({prior})")
    op.execute(
        f"ALTER TABLE calculation_dependency ALTER COLUMN dependency_role "
        f"TYPE {_ROLE_TYPE} USING dependency_role::text::{_ROLE_TYPE}"
    )
    op.execute(f"DROP TYPE {_ROLE_TYPE}_old")
    for name, role in _ROLE_INDEXES:
        op.create_index(
            name,
            "calculation_dependency",
            ["child_calculation_id"],
            unique=True,
            postgresql_where=sa.text(f"dependency_role = '{role}'"),
        )

    # The same rebuild for ``energy_component_kind``, recast on both columns that use it.
    prior_components = ", ".join(f"'{value}'" for value in _PRIOR_COMPONENT_VALUES)
    op.execute(f"ALTER TYPE {_COMPONENT_TYPE} RENAME TO {_COMPONENT_TYPE}_old")
    op.execute(f"CREATE TYPE {_COMPONENT_TYPE} AS ENUM ({prior_components})")
    for table, column in _COMPONENT_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE {_COMPONENT_TYPE} USING {column}::text::{_COMPONENT_TYPE}"
        )
    op.execute(f"DROP TYPE {_COMPONENT_TYPE}_old")
