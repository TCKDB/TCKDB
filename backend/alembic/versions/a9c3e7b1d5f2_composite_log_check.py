"""record what comparing a composite calculation with its output log concluded (ADR 0021, phase P7a)

``P3b`` compared a deposited composite energy with the output log's summary block
when the log was uploaded, and answered with a warning. It recorded nothing, so a
read could not tell a program-run energy a log confirmed from one nobody checked
without parsing the log again. This revision adds the record.

Schema
------
* ``composite_log_outcome`` enum: ``confirmed``, ``mismatch``, ``method_mismatch``,
  ``available``, ``unverifiable``, ``absent``.
* ``calc_composite_log_check`` -- one row per ``(calculation_id, artifact_sha256, parser_version)``:
  the conclusion of the reconciliation hook for one attached log. A *conclusion*,
  not a number: no energy the log stated is stored here, and the deposited result
  is untouched. ``artifact_sha256`` is the log's digest (not a foreign key: the
  content-addressed object may be shared), constrained to 64 lowercase hex digits.
  ``parser_version`` (at least 1) names the version of the composite-log parser that drew the
  conclusion, and is part of the primary key: the same log uploaded again after a parser fix
  records a fresh conclusion beside the old one, and a read prefers the newest version.

Accepted-science immutability
-----------------------------
The table carries ``tckdb_guard_accepted_child`` on ``calculation_id`` and the
TRUNCATE refusal, like ``calc_composite_result`` and ``calc_composite_input``: what
a log said about an accepted calculation can be neither edited nor removed.
``trg_as_child_<table>`` / ``trg_as_truncate_<table>`` follow ``b6c1f4a8e703``.

No rows are written
-------------------
Pure DDL. A log attached before this revision was reconciled and forgotten; those
calculations read as ``program_reported`` (no confirming log on record) until the
log is deposited again. The deployed database holds no composite calculation.

Downgrade
---------
Refuses, with the count, while any ``calc_composite_log_check`` row exists: dropping
it would discard the only record that a program-run energy was confirmed. It deletes
nothing. Otherwise it drops the triggers, the table and the enum.

Revision ID: a9c3e7b1d5f2
Revises: b4d8e2f6a1c9
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a9c3e7b1d5f2"
down_revision: Union[str, Sequence[str], None] = "b4d8e2f6a1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "calc_composite_log_check"
_ENUM = "composite_log_outcome"
_VALUES = ("confirmed", "mismatch", "method_mismatch", "available", "unverifiable", "absent")

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child``
#: against the accepted root named directly on the row. Read by
#: ``tests/db/test_accepted_science_trigger_registry.py``.
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
    outcome = postgresql.ENUM(*_VALUES, name=_ENUM)
    outcome.create(op.get_bind(), checkfirst=True)
    op.create_table(
        _TABLE,
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("artifact_sha256", sa.CHAR(64), nullable=False),
        sa.Column("parser_version", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", postgresql.ENUM(*_VALUES, name=_ENUM, create_type=False), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("artifact_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_calc_composite_log_check_artifact_sha256_hex")),
        sa.CheckConstraint("parser_version >= 1", name=op.f("ck_calc_composite_log_check_parser_version_positive")),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_composite_log_check_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            "calculation_id", "artifact_sha256", "parser_version", name=op.f("pk_calc_composite_log_check")
        ),
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


def downgrade() -> None:
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} {_TABLE} row(s). Dropping the table would discard the only record "
            "that a program-run composite energy was confirmed against its output log. "
            "Remove those composite calculations first, then downgrade."
        )
    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_table(_TABLE)
    postgresql.ENUM(name=_ENUM).drop(op.get_bind(), checkfirst=True)
