"""Stage 4: two measured indexes for the bounded analytics surface.

Revision ID: a7c2e4f8b6d9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-01

Both indexes in this revision were kept only because a measured plan on the
Stage 4 benchmark corpus (``tckdb_bench_s4``: 50,000 species, 418,182
calculations, 70,000 statmech, 60,000 thermo, 13,636 kinetics, 386,514
record_review) actually improved. Seven other candidates were measured and
**rejected** — they are listed in
``backend/docs/specs/scientific_analytics_surface.md`` with their numbers so
nobody has to re-derive that they do not help.

``ix_record_review_record_lookup``
    ``(record_type, record_id) INCLUDE (status)``. Every scientific read joins
    ``record_review`` on exactly this key and then reads ``status``. The
    existing ``uq_record_review_record`` unique constraint indexes the key but
    not the payload, so the join visited the heap once per candidate. With
    ``status`` included the join is index-only (``Heap Fetches: 0``).

    Measured, median of 7 EXPLAIN (ANALYZE, BUFFERS) runs:

    * thermo analytics page (7,556 candidates): 19.6 ms / 61,602 shared
      buffers -> 9.7 ms / 1,711 buffers.
    * kinetics analytics page (8,128 candidates): 6.4 ms -> 4.0 ms.
    * calculation analytics page (119,701 candidates): the ``record_review``
      side dropped from a 3,904-buffer parallel seq scan to an 805-buffer
      parallel index-only scan.

``ix_calculation_type_id``
    ``(type, id)``. The analytics filter pass selects only ``calculation.id``
    for a given ``type``, always bounded by ``id <= watermark``, so both
    columns are in the index and the scan is index-only.

    Measured: the ``calculation_type=sp`` page query (119,701 candidates —
    the shape that used to fail outright) went from 37.4 ms / 10,622 shared
    buffers to 28.8 ms / 1,860 buffers.

Two shapes measurably regressed under the ``record_review`` covering index and
are recorded rather than hidden: ``thermo?phase=gas`` count (11.4 ms ->
16.1 ms, a planner flip to a merge join) and ``statmech?point_group=`` (10.2 ->
11.0 ms count). Both are small in absolute terms next to the wins, and both
are plan-choice artifacts rather than index defects.

Neither index changes any column, constraint or row, so ``downgrade()`` is a
plain drop and is safe on a deployed database.
"""

from __future__ import annotations

from alembic import op

revision = "a7c2e4f8b6d9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_record_review_record_lookup",
        "record_review",
        ["record_type", "record_id"],
        unique=False,
        postgresql_include=["status"],
    )
    op.create_index(
        "ix_calculation_type_id",
        "calculation",
        ["type", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_calculation_type_id", table_name="calculation")
    op.drop_index("ix_record_review_record_lookup", table_name="record_review")
