"""Let a reaction-level kinetics record state the bath gas it was obtained for.

A PLOG or Chebyshev fit, or an apparent rate at a stated pressure, is a
statement about a collider: a table fitted for Ar is not one for N2. A network
solve already records its bath gas (``network_solve_bath_gas``); a
reaction-level ``kinetics`` record had nowhere to say it.

Schema
------
``kinetics_bath_gas`` -- one row per bath-gas component, the same shape as
``network_solve_bath_gas``: ``(kinetics_id, species_entry_id, mole_fraction)``,
primary key on the pair, ``CHECK (mole_fraction > 0 AND mole_fraction <= 1)``,
foreign keys deferrable like the rest of the schema.

Optional, and never inferred
----------------------------
A record that states no bath gas has no rows, which reads as "not stated". The
rules that decide whether a stated bath gas is acceptable (not at the
high-pressure limit, one row per species entry, mole fractions summing to 1) are
cross-row and cross-column facts the upload refuses with a code; the database
keeps only the per-row range check, as it does for the network table.

Accepted-science immutability
-----------------------------
The new table is an ownership child of ``kinetics`` (guarded on
``kinetics_id`` by ``tckdb_guard_accepted_child``), with the TRUNCATE refusal,
exactly like ``kinetics_plog`` and the other children ``c6f2a9d4e7b1`` and
``a1f6c3e9b527`` registered: once a kinetics record has been approved its bath
gas can be neither added to, edited nor removed. ``species_entry_id`` is a
citation, not ownership, and is not guarded.

No backfill
-----------
Existing records keep no rows. Nothing about a bath gas can be recovered from a
stored rate, and a falloff's third-body efficiencies are a different statement
(how efficiently each collider transfers energy), so none is converted.

Downgrade refuses while any row exists (rows under an approved, frozen kinetics
record cannot be deleted, so for those the refusal is permanent); it deletes
nothing. Otherwise it drops the triggers and the table.

Revision ID: a4c8e1f7b392
Revises: d3f8a1c6e92b
Create Date: 2026-10-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a4c8e1f7b392"
down_revision: Union[str, Sequence[str], None] = "d3f8a1c6e92b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "kinetics_bath_gas"

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child`` against the accepted
#: root named directly on the row. Read by ``tests/db/test_accepted_science_trigger_registry.py``.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, "kinetics", "kinetics_id"),)

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
    op.create_table(
        _TABLE,
        sa.Column("kinetics_id", sa.BigInteger(), nullable=False),
        sa.Column("species_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("mole_fraction", sa.Double(), nullable=False),
        sa.CheckConstraint(
            "mole_fraction > 0 AND mole_fraction <= 1",
            name=op.f("ck_kinetics_bath_gas_mole_fraction_range"),
        ),
        sa.ForeignKeyConstraint(
            ["kinetics_id"],
            ["kinetics.id"],
            name=op.f("fk_kinetics_bath_gas_kinetics_id_kinetics"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["species_entry_id"],
            ["species_entry.id"],
            name=op.f("fk_kinetics_bath_gas_species_entry_id_species_entry"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("kinetics_id", "species_entry_id", name=op.f("pk_kinetics_bath_gas")),
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
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(
            f"Cannot downgrade: {rows} {_TABLE} row(s) state the bath gas a rate was obtained for. "
            "Dropping them would discard the only statement of which collider a pressure-dependent "
            "fit applies to. Nothing is deleted; resolve those rows first. Rows under an approved "
            "(frozen) kinetics record cannot be deleted or edited, so for them this refusal is permanent."
        )
    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_table(_TABLE)
