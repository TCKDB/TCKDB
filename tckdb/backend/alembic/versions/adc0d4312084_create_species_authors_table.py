"""Create species authors table

Revision ID: adc0d4312084
Revises: fd9d31ef747d
Create Date: 2024-10-20 10:28:12.195971

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "adc0d4312084"
down_revision: Union[str, None] = "fd9d31ef747d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "species_authors",
        sa.Column("species_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["person.id"],
        ),
        sa.ForeignKeyConstraint(
            ["species_id"],
            ["species.id"],
        ),
        sa.PrimaryKeyConstraint("species_id", "author_id"),
    )
    pass


def downgrade() -> None:
    op.drop_table("species_authors")
    pass
