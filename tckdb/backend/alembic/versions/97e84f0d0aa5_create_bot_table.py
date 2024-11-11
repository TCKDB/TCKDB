"""Create bot table

Revision ID: 97e84f0d0aa5
Revises: 4fdc896a076e
Create Date: 2024-10-20 08:33:51.399976

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from tckdb.backend.app.models.common import MsgpackExt

# revision identifiers, used by Alembic.
revision: str = "97e84f0d0aa5"
down_revision: Union[str, None] = "4fdc896a076e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=255), nullable=False),
        sa.Column("git_hash", sa.String(length=500), nullable=True),
        sa.Column("git_branch", sa.String(length=100), nullable=True),
        sa.Column("reviewer_flags", MsgpackExt(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("name", "version", name="_bot_name_version_uc"),
    )
    op.create_index(op.f("ix_bot_id"), "bot", ["id"], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_id"), table_name="bot")
    op.drop_table("bot")
    pass
