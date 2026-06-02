"""add submission failed status and artifact record type

Closes ingestion-audit gaps:

* ``submission_status`` gains ``failed`` — the system-set terminal state for an
  upload event whose ingestion failed (async job out of retries, or a sync
  upload that raised during persistence). Distinct from curator ``rejected``.
* ``submission_record_type`` gains ``artifact`` — so uploaded calculation
  artifacts can be linked to their submission as contribution evidence.

Both additions are additive enum values; no rows are rewritten.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL 12+ allows ADD VALUE inside a transaction provided the new
    # value is not *used* in the same transaction; these only add.
    op.execute(
        "ALTER TYPE submission_status ADD VALUE IF NOT EXISTS 'failed'"
    )
    op.execute(
        "ALTER TYPE submission_record_type ADD VALUE IF NOT EXISTS 'artifact'"
    )


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value; reversing would require
    # rebuilding both types and is unsafe while submissions/links may carry
    # the new values.
    raise NotImplementedError(
        "Downgrade would require rebuilding submission_status and "
        "submission_record_type enums and cannot be run while failed "
        "submissions or artifact record links may exist."
    )
