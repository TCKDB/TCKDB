"""calculation: software provenance reconciliation outcome

DR-0008. Persists the outcome of reconciling the user-declared software
release against the version banner observed by the ESS output parser.

Adds to ``calculation``:

* ``software_reconciliation_status`` — new ``software_reconciliation_status``
  enum (matched / enriched / mismatch / declared_only / parsed_only),
  nullable. NULL means reconciliation was never run for the row.
* ``observed_software_banner`` — free-text banner string observed by the
  parser, nullable.

Both columns are nullable and backfill-safe: existing ``calculation`` rows
keep NULLs (reconciliation was not run for them), so no data migration is
required. ``calculation`` is a deployed table, so this lands as a new
revision per the migration policy.

Revision ID: f3a9c1d7b2e4
Revises: a3f1c7e9b2d5
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d7b2e4'
down_revision: Union[str, Sequence[str], None] = '94daa2c345fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


software_reconciliation_status = postgresql.ENUM(
    'matched',
    'enriched',
    'mismatch',
    'declared_only',
    'parsed_only',
    name='software_reconciliation_status',
    create_type=False,
)


def upgrade() -> None:
    """Add the reconciliation enum type and the two calculation columns."""
    software_reconciliation_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'calculation',
        sa.Column(
            'software_reconciliation_status',
            software_reconciliation_status,
            nullable=True,
        ),
    )
    op.add_column(
        'calculation',
        sa.Column('observed_software_banner', sa.Text(), nullable=True),
    )
    op.create_index(
        'ix_calculation_software_reconciliation_status',
        'calculation',
        ['software_reconciliation_status'],
    )


def downgrade() -> None:
    """Drop the two columns and the reconciliation enum type."""
    op.drop_index(
        'ix_calculation_software_reconciliation_status',
        table_name='calculation',
    )
    op.drop_column('calculation', 'observed_software_banner')
    op.drop_column('calculation', 'software_reconciliation_status')
    software_reconciliation_status.drop(op.get_bind(), checkfirst=True)
