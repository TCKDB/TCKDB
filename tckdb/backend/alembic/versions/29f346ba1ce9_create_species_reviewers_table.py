"""Create species reviewers table

Revision ID: 29f346ba1ce9
Revises: adc0d4312084
Create Date: 2024-10-20 10:28:55.788662

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "29f346ba1ce9"
down_revision: Union[str, None] = "adc0d4312084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "species_reviewers",
        sa.Column("species_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["person.id"],
        ),
        sa.ForeignKeyConstraint(
            ["species_id"],
            ["species.id"],
        ),
        sa.PrimaryKeyConstraint("species_id", "reviewer_id"),
    )
    pass


def downgrade() -> None:
    op.drop_table("species_reviewers")
    pass
