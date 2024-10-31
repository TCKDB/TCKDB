"""Create literature author table

Revision ID: e69c2652ee45
Revises: fbac9cf75cb0
Create Date: 2024-10-20 10:21:55.272313

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e69c2652ee45"
down_revision: Union[str, None] = "fbac9cf75cb0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "literature_author",
        sa.Column("literature_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["author.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["literature_id"], ["literature.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("literature_id", "author_id"),
    )
    pass


def downgrade() -> None:
    op.drop_table("literature_author")
    pass
