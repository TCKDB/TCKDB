"""add btree indexes for literature inverse-record and workflow-tool filters

These support the scientific literature inverse-record endpoint and
workflow-tool calculation filters.

The performance reviewer pre-launch audit flagged two gaps left over
after ``c3d4e5f6a7b8`` indexed the hot ``calculation`` FK columns:

1. ``GET /api/v1/scientific/literature/{ref}/records`` issues direct
   ``WHERE literature_id = :id`` queries against ``thermo``,
   ``kinetics``, ``statmech``, ``transport``, ``network``, and
   ``network_solve``. Only ``calculation.literature_id`` was indexed
   in the earlier migration; the other six were seq-scanned on every
   inverse-records request.

2. ``calculation.workflow_tool_release_id`` was missed when the other
   hot calculation FKs were indexed, even though the calculations,
   artifact, and species-calculations search surfaces all filter by
   ``workflow_tool`` / ``workflow_tool_version`` through this FK.

This migration adds plain B-tree indexes on each of those columns.
All are nullable FK columns; the default B-tree handles NULLs fine
and stores them at the end of the index. No ``WHERE`` predicate is
needed.

These are normal blocking ``CREATE INDEX`` builds inside an Alembic
transaction. For a small / self-hosted / single-node deployment the
build is essentially instant. For a larger deployed DB, schedule the
upgrade during a low-traffic window — see
``backend/docs/deployment/migrations.md``.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-24
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table_name, column_name)
_FK_INDEXES: list[tuple[str, str, str]] = [
    ("ix_thermo_literature_id", "thermo", "literature_id"),
    ("ix_kinetics_literature_id", "kinetics", "literature_id"),
    ("ix_statmech_literature_id", "statmech", "literature_id"),
    ("ix_transport_literature_id", "transport", "literature_id"),
    ("ix_network_literature_id", "network", "literature_id"),
    ("ix_network_solve_literature_id", "network_solve", "literature_id"),
    (
        "ix_calculation_workflow_tool_release_id",
        "calculation",
        "workflow_tool_release_id",
    ),
]


def upgrade() -> None:
    for index_name, table_name, column_name in _FK_INDEXES:
        op.create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    for index_name, table_name, _ in reversed(_FK_INDEXES):
        op.drop_index(index_name, table_name=table_name)
