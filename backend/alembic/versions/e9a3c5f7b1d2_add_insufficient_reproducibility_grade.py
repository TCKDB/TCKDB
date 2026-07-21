"""add insufficient reproducibility grade

Adds an explicit level-zero outcome so a completed, fail-closed assessment can
be persisted without falsely claiming that the record is described.

Revision ID: e9a3c5f7b1d2
Revises: b8e3f1a9c2d4
Create Date: 2026-07-21
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9a3c5f7b1d2"
down_revision: Union[str, Sequence[str], None] = "b8e3f1a9c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the truthful below-described assessment outcome."""
    op.execute("ALTER TYPE reproducibility_grade ADD VALUE IF NOT EXISTS 'insufficient' BEFORE 'described'")


def downgrade() -> None:
    """Remove ``insufficient`` only when no assessment uses it."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM record_reproducibility_assessment
                WHERE grade = 'insufficient'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove reproducibility grade insufficient while assessments use it';
            END IF;
        END;
        $$
        """
    )
    op.execute("ALTER TYPE reproducibility_grade RENAME TO reproducibility_grade_with_insufficient")
    op.execute("CREATE TYPE reproducibility_grade AS ENUM ('described', 'auditable', 'rerunnable')")
    op.execute(
        "ALTER TABLE record_reproducibility_assessment "
        "ALTER COLUMN grade TYPE reproducibility_grade "
        "USING grade::text::reproducibility_grade"
    )
    op.execute("DROP TYPE reproducibility_grade_with_insufficient")
