"""name every participant's source calculation on a network state energy (issue #678)

A bimolecular state's energy is a sum over its species, but ``network_solve_state_energy`` has
one ``source_calculation_id``. Since #675 the single source is checked to belong to *a* participant,
which stops a wrong molecule but leaves the stored source one summand of several. This revision adds
the place for one source per participant and the place to record whether the stated energy was held
against their sum.

Schema
------
* ``network_solve_state_energy_source`` -- one row per participant of a state energy, keyed
  ``(solve_id, state_id, species_entry_id)``. ``stoichiometry`` is not repeated: it lives on
  ``network_state_participant`` (``2A`` is one participant with coefficient 2), so no copy index is
  needed. Composite foreign keys tie the row to an existing state energy and to a participant of that
  very state; ``calculation_id`` is a plain foreign key (the upload service checks type and owner).
* Three nullable columns on ``network_solve_state_energy``:
  ``source_sum_comparison`` (``agrees`` or ``not_compared``; a disagreement is never stored, it refuses
  the deposit) and ``source_sum_not_compared_reason`` (a fixed set of tokens, present exactly when the
  status is ``not_compared``). No computed total is stored anywhere.
* ``source_calculation_id`` stays, for back-compat and for the single-source form.

Accepted-science immutability
-----------------------------
The new table is an ownership child of ``network_solve`` (guarded on ``solve_id``), exactly like
``network_solve_state_energy`` (``a1f6c3e9b527``), with the TRUNCATE refusal. ``calculation_id`` is
provenance, not ownership. ``ALTER TABLE`` on the existing table does not fire the row guard.

No backfill
-----------
Existing state energies keep both new columns NULL, which reads as "the comparison did not exist",
never as a pass, and have no participant-source rows: a read shows their one stored source as
partial when the state has more participants. Nothing can be recomputed honestly after the fact, and
the rows are frozen under accepted solves.

Downgrade
---------
Refuses, with the counts, while any participant-source row exists or any state energy recorded a
comparison outcome: dropping them would discard the only statement of where a summed energy came from.
It deletes nothing. Otherwise it drops the triggers, the table, the constraints and the columns.

Revision ID: d7a1c4e9b258
Revises: b3d8f1a6c924
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d7a1c4e9b258"
down_revision: Union[str, Sequence[str], None] = "b3d8f1a6c924"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "network_solve_state_energy_source"
_ENERGY = "network_solve_state_energy"
_STATUS_CHECK = "ck_network_solve_state_energy_sum_comparison"
_SHAPE_CHECK = "ck_network_solve_state_energy_sum_reason_shape"
_TOKEN_CHECK = "ck_network_solve_state_energy_sum_reason_token"

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child`` against the accepted
#: root named directly on the row. Read by ``tests/db/test_accepted_science_trigger_registry.py``.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, "network_solve", "solve_id"),)

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
    op.add_column(_ENERGY, sa.Column("source_sum_comparison", sa.Text(), nullable=True))
    op.add_column(_ENERGY, sa.Column("source_sum_not_compared_reason", sa.Text(), nullable=True))
    op.execute(
        f"ALTER TABLE public.{_ENERGY} ADD CONSTRAINT {_STATUS_CHECK} "
        "CHECK (source_sum_comparison IS NULL OR source_sum_comparison IN ('agrees', 'not_compared'))"
    )
    op.execute(
        f"ALTER TABLE public.{_ENERGY} ADD CONSTRAINT {_SHAPE_CHECK} "
        "CHECK ((source_sum_comparison IS NOT DISTINCT FROM 'not_compared') = "
        "(source_sum_not_compared_reason IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE public.{_ENERGY} ADD CONSTRAINT {_TOKEN_CHECK} "
        "CHECK (source_sum_not_compared_reason IS NULL OR source_sum_not_compared_reason IN ("
        "'no_source_stated', 'sources_incomplete', 'convention_not_summable', "
        "'energy_zero_not_comparable', 'stored_energy_not_stated', 'zpe_not_in_source', "
        "'no_second_state_on_the_same_zero'))"
    )

    op.create_table(
        _TABLE,
        sa.Column("solve_id", sa.BigInteger(), nullable=False),
        sa.Column("state_id", sa.BigInteger(), nullable=False),
        sa.Column("species_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["solve_id", "state_id"],
            [f"{_ENERGY}.solve_id", f"{_ENERGY}.state_id"],
            name="fk_nsse_source_state_energy",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["state_id", "species_entry_id"],
            ["network_state_participant.state_id", "network_state_participant.species_entry_id"],
            name="fk_nsse_source_participant",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name="fk_nsse_source_calculation_id_calculation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("solve_id", "state_id", "species_entry_id", name=op.f(f"pk_{_TABLE}")),
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
    sources = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    compared = bind.execute(
        sa.text(f"SELECT count(*) FROM {_ENERGY} WHERE source_sum_comparison IS NOT NULL")
    ).scalar_one()
    if sources or compared:
        raise RuntimeError(
            f"Cannot downgrade: {sources} {_TABLE} row(s) and {compared} {_ENERGY} row(s) that recorded "
            "the outcome of comparing a stated state energy with the sum of its sources. Dropping them "
            "would discard the only statement of where a summed energy came from, or that it was "
            "checked. Nothing is deleted; resolve those rows first."
        )
    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_table(_TABLE)
    for name in (_TOKEN_CHECK, _SHAPE_CHECK, _STATUS_CHECK):
        op.execute(f"ALTER TABLE public.{_ENERGY} DROP CONSTRAINT {name}")
    op.drop_column(_ENERGY, "source_sum_not_compared_reason")
    op.drop_column(_ENERGY, "source_sum_comparison")
