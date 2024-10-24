"""Create nonphysicalspecies author table

Revision ID: b8e5899e04b9
Revises: 9611c77629a1
Create Date: 2024-10-20 10:24:41.838241

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5899e04b9"
down_revision: Union[str, None] = "9611c77629a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "np_species_authors",
        sa.Column("np_species_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["person.id"],
        ),
        sa.ForeignKeyConstraint(
            ["np_species_id"],
            ["nonphysicalspecies.id"],
        ),
        sa.PrimaryKeyConstraint("np_species_id", "author_id"),
    )
    pass


def downgrade() -> None:
    op.drop_table("np_species_authors")
    pass
