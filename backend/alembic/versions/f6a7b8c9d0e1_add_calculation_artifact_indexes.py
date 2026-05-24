"""add btree indexes for calculation_artifact search filters

The pre-launch performance review (follow-up to
``e5f6a7b8c9d0_add_literature_and_workflow_fk_indexes``) flagged
``calculation_artifact`` as the next unindexed hot table on the public
read surface. ``GET/POST /api/v1/scientific/artifacts/search`` filters
artifacts by ``calculation_id`` (FK join to owning calc),
``sha256`` (content-address lookup, ``has_sha256``), and ``kind``
(``artifact_kind`` enum filter). None of those three columns had an
index, so each filter and join was seq-scanning the artifact table.

This migration adds plain B-tree indexes on each of those columns.
``calculation_id`` is NOT NULL; ``sha256`` is nullable; ``kind`` is
NOT NULL. The default B-tree handles NULLs fine and stores them at the
end of the index. No ``WHERE`` predicate is needed.

``bytes`` (range filter) and ``created_at`` (range filter; the sort is
done in Python after fetch, so an index would not help the sort path)
are deliberately **not** indexed in this slice — they are rarely the
selective predicate, and adding them now would pay write amplification
on every artifact insert for filters that are not yet load-bearing.

``filename`` / ``filename_contains`` remain unindexed: ``filename``
exact match is rare in practice and ``filename_contains`` is a
case-insensitive ``LIKE`` over ``lower(filename)``, which would need a
``pg_trgm`` GIN index or a generated lowercase column to be useful.
That design is deferred until there is a real consumer asking for it.

These are normal blocking ``CREATE INDEX`` builds inside an Alembic
transaction. For a small / self-hosted / single-node deployment the
build is essentially instant. For a larger deployed DB, schedule the
upgrade during a low-traffic window — see
``backend/docs/deployment/migrations.md``.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-24
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, column_name)
_ARTIFACT_INDEXES: list[tuple[str, str]] = [
    ("ix_calculation_artifact_calculation_id", "calculation_id"),
    ("ix_calculation_artifact_kind", "kind"),
    ("ix_calculation_artifact_sha256", "sha256"),
]


def upgrade() -> None:
    for index_name, column_name in _ARTIFACT_INDEXES:
        op.create_index(index_name, "calculation_artifact", [column_name])


def downgrade() -> None:
    for index_name, _ in reversed(_ARTIFACT_INDEXES):
        op.drop_index(index_name, table_name="calculation_artifact")
