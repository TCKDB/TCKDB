"""calculation: preserve the declared software banner across a correction

DR-0008 follow-up (issue #305 ARC follow-up). Software-identity mismatches
(the parsed banner names a different program than the declared
software_release) are now corrected at write time: software_release_id is
repointed at the parser-observed release rather than staying on the
declared-but-wrong one. That means the declared string is no longer
reachable through software_release_id for a corrected row, so it must be
preserved somewhere else.

Adds to ``calculation``:

* ``declared_software_banner`` — free-text rendering of the originally
  -declared software_release (name/version/revision/build), nullable.
  Populated only on the correction path; NULL for every row where
  software_release_id still points at what was declared (the
  overwhelming majority -- correction only fires when a parsed banner
  positively disagrees on the program itself).

Nullable and backfill-safe: existing ``calculation`` rows keep NULL, so no
data migration is required. ``calculation`` is a deployed table, so this
lands as a new revision per the migration policy.

Revision ID: 2c26fb2a75a4
Revises: a4f7c2e9d651
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2c26fb2a75a4'
down_revision: Union[str, Sequence[str], None] = 'a4f7c2e9d651'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the declared-banner preservation column."""
    op.add_column(
        'calculation',
        sa.Column('declared_software_banner', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the declared-banner preservation column."""
    op.drop_column('calculation', 'declared_software_banner')
