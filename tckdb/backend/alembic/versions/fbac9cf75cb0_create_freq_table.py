"""Create freq table

Revision ID: fbac9cf75cb0
Revises: 3d67b1f72380
Create Date: 2024-10-20 10:20:57.425229

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from tckdb.backend.app.models.common import MsgpackExt

# revision identifiers, used by Alembic.
revision: str = "fbac9cf75cb0"
down_revision: Union[str, None] = "3d67b1f72380"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "freqscale",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("level_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("reviewer_flags", MsgpackExt(), nullable=True),
        sa.ForeignKeyConstraint(
            ["level_id"],
            ["level.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("level_id"),
    )
    op.create_index(op.f("ix_freqscale_id"), "freqscale", ["id"], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f("ix_freqscale_id"), table_name="freqscale")
    op.drop_table("freqscale")
    pass
