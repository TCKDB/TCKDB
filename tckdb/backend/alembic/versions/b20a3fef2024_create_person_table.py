"""Create person table

Revision ID: b20a3fef2024
Revises: a967ee81bbeb
Create Date: 2024-10-20 10:16:29.371014

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from tckdb.backend.app.models.common import MsgpackExt

# revision identifiers, used by Alembic.
revision: str = "b20a3fef2024"
down_revision: Union[str, None] = "a967ee81bbeb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("affiliation", sa.String(length=255), nullable=False),
        sa.Column("uploaded_species", sa.Integer(), nullable=True),
        sa.Column("uploaded_non_physical_species", sa.Integer(), nullable=True),
        sa.Column("uploaded_reactions", sa.Integer(), nullable=True),
        sa.Column("uploaded_networks", sa.Integer(), nullable=True),
        sa.Column("reviewed_species", sa.Integer(), nullable=True),
        sa.Column("reviewed_non_physical_species", sa.Integer(), nullable=True),
        sa.Column("reviewed_reactions", sa.Integer(), nullable=True),
        sa.Column("reviewed_networks", sa.Integer(), nullable=True),
        sa.Column("reviewer_flags", MsgpackExt(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_person_id"), "person", ["id"], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f("ix_person_id"), table_name="person")
    op.drop_table("person")
    pass
