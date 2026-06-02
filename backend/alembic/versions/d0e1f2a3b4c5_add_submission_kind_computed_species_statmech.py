"""add computed_species and statmech to submission_kind enum

Universal-ingestion change: every accepted ``/uploads/*`` upload now creates a
``submission`` wrapper. Two upload kinds had no matching ``submission_kind``
value — the computed-species bundle upload and the standalone statmech upload —
so the enum is extended to classify them. The values are additive; existing
rows and the async ``upload_job_kind`` enum are untouched.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL 12+ allows ADD VALUE inside a transaction as long as the new
    # value is not *used* in the same transaction; this migration only adds it.
    op.execute(
        "ALTER TYPE submission_kind ADD VALUE IF NOT EXISTS 'computed_species'"
    )
    op.execute(
        "ALTER TYPE submission_kind ADD VALUE IF NOT EXISTS 'statmech'"
    )


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value; reversing this would require
    # rebuilding submission_kind and is unsafe while submission rows may carry
    # the new values.
    raise NotImplementedError(
        "Downgrade would require rebuilding the submission_kind enum and "
        "cannot be run while computed_species/statmech submissions may exist."
    )
