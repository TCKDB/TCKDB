"""add btree indexes on hot calculation FK columns

The backend deployment readiness audit
(``backend/docs/specs/backend_deployment_readiness_audit.md``, finding
P1-2) flagged that ``calculation`` is the hub table for the public
scientific read/search surface, and the FK columns it is filtered on
have no indexes. PostgreSQL does not auto-index FK source columns, so
every search that joined or filtered by ``lot_id``,
``software_release_id``, ``conformer_observation_id``,
``species_entry_id``, ``transition_state_entry_id``, or
``literature_id`` was relying on sequential scans.

This migration adds plain B-tree indexes on each of those columns.
All are nullable FK columns; the default B-tree handles NULLs fine
and stores them at the end of the index. No ``WHERE`` predicate is
needed.

These are normal blocking ``CREATE INDEX`` builds inside an Alembic
transaction. For a small / self-hosted / single-node deployment the
build is essentially instant. For a larger deployed DB, schedule the
upgrade during a low-traffic window — see
``backend/docs/deployment/migrations.md``.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-23
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CALCULATION_FK_INDEXES = [
    ("ix_calculation_lot_id", "lot_id"),
    ("ix_calculation_software_release_id", "software_release_id"),
    ("ix_calculation_conformer_observation_id", "conformer_observation_id"),
    ("ix_calculation_species_entry_id", "species_entry_id"),
    ("ix_calculation_transition_state_entry_id", "transition_state_entry_id"),
    ("ix_calculation_literature_id", "literature_id"),
]


def upgrade() -> None:
    for index_name, column_name in _CALCULATION_FK_INDEXES:
        op.create_index(index_name, "calculation", [column_name])


def downgrade() -> None:
    for index_name, _ in reversed(_CALCULATION_FK_INDEXES):
        op.drop_index(index_name, table_name="calculation")
