"""Add an explicit kinetics degeneracy convention.

Adds a non-null enum column and backfills existing kinetics rows as
``unknown`` through the constant server default. The schema change is
reversible, but downgrade permanently discards any convention values stored in
the column. On large databases, adding or dropping the column requires an
``ACCESS EXCLUSIVE`` table lock; the constant-default add is catalog-fast on
supported PostgreSQL versions, but lock acquisition and older-server behavior
can increase deployment runtime.

Revision ID: f2a4c6e8b0d1
Revises: e9a3c5f7b1d2
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2a4c6e8b0d1"
down_revision: Union[str, Sequence[str], None] = "e9a3c5f7b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


kinetics_degeneracy_convention = postgresql.ENUM(
    "already_applied",
    "not_applied",
    "unknown",
    name="kinetics_degeneracy_convention",
    create_type=False,
)


def upgrade() -> None:
    """Add a non-null convention, backfilling every legacy row as unknown."""
    bind = op.get_bind()
    kinetics_degeneracy_convention.create(bind, checkfirst=True)
    op.add_column(
        "kinetics",
        sa.Column(
            "degeneracy_convention",
            kinetics_degeneracy_convention,
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    """Remove the convention column and its enum type."""
    op.drop_column("kinetics", "degeneracy_convention")
    bind = op.get_bind()
    kinetics_degeneracy_convention.drop(bind, checkfirst=True)
