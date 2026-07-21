"""enforce complete integrity metadata for calculation artifacts

Every artifact accepted through the upload services already receives a
server-computed lowercase SHA-256 and positive byte count. The columns remained
nullable from the original schema, allowing direct SQL/ORM inserts to create
metadata rows that could neither address nor verify object-store content.

This revision fails fast if a deployed database contains legacy incomplete or
malformed rows; operators must repair those rows from the object store before
retrying. It then makes both columns non-null and adds format/value checks. The
hosted Pi was audited before this revision and had zero artifact rows, so no
backfill is required there.

Upgrade cost is one bounded validation scan of ``calculation_artifact`` plus
metadata-only column/constraint changes. Downgrade restores nullable columns
and removes the checks; it does not alter artifact data.

Revision ID: a7c9e2f4b6d8
Revises: f4a7c2e9b1d3
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c9e2f4b6d8"
down_revision: Union[str, Sequence[str], None] = "f4a7c2e9b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Reject incomplete legacy rows, then enforce integrity metadata."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM calculation_artifact
                WHERE sha256 IS NULL
                   OR sha256 !~ '^[0-9a-f]{64}$'
                   OR bytes IS NULL
                   OR bytes <= 0
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'calculation_artifact contains incomplete integrity metadata',
                    HINT = 'Backfill sha256 and bytes from verified object-store content before retrying.';
            END IF;
        END
        $$;
        """
    )
    op.alter_column(
        "calculation_artifact",
        "sha256",
        existing_type=sa.CHAR(length=64),
        nullable=False,
    )
    op.alter_column(
        "calculation_artifact",
        "bytes",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_calculation_artifact_sha256_lower_hex",
        "calculation_artifact",
        "sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_calculation_artifact_bytes_gt_0",
        "calculation_artifact",
        "bytes > 0",
    )


def downgrade() -> None:
    """Restore the original nullable metadata shape."""
    op.drop_constraint(
        "ck_calculation_artifact_bytes_gt_0",
        "calculation_artifact",
        type_="check",
    )
    op.drop_constraint(
        "ck_calculation_artifact_sha256_lower_hex",
        "calculation_artifact",
        type_="check",
    )
    op.alter_column(
        "calculation_artifact",
        "bytes",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.alter_column(
        "calculation_artifact",
        "sha256",
        existing_type=sa.CHAR(length=64),
        nullable=True,
    )
