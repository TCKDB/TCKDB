"""Create nonphysicalspecies table

Revision ID: 9611c77629a1
Revises: e69c2652ee45
Create Date: 2024-10-20 10:23:11.952121

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from tckdb.backend.app.models.common import MsgpackExt

# revision identifiers, used by Alembic.
revision: str = "9611c77629a1"
down_revision: Union[str, None] = "e69c2652ee45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nonphysicalspecies",
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
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retracted", sa.String(length=255), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("reviewer_flags", MsgpackExt(), nullable=True),
        sa.Column("smiles", sa.String(length=5000), nullable=False),
        sa.Column("inchi", sa.String(length=5000), nullable=False),
        sa.Column("inchi_key", sa.String(length=5000), nullable=False),
        sa.Column("charge", sa.Integer(), nullable=False),
        sa.Column("multiplicity", sa.Integer(), nullable=False),
        sa.Column("electronic_state", sa.String(length=150), nullable=False),
        sa.Column("coordinates", MsgpackExt(), nullable=False),
        sa.Column("graph", sa.String(length=100000), nullable=True),
        sa.Column(
            "fragments",
            postgresql.ARRAY(sa.Integer(), zero_indexes=True),
            nullable=True,
        ),
        sa.Column("fragment_orientation", MsgpackExt(), nullable=True),
        sa.Column("chirality", MsgpackExt(), nullable=True),
        sa.Column("conformation_method", sa.String(length=500), nullable=False),
        sa.Column("is_well", sa.Boolean(), nullable=False),
        sa.Column("is_global_min", sa.Boolean(), nullable=False),
        sa.Column("global_min_geometry", MsgpackExt(), nullable=True),
        sa.Column("is_ts", sa.Boolean(), nullable=False),
        sa.Column("irc_trajectories", MsgpackExt(), nullable=True),
        sa.Column("literature_id", sa.Integer(), nullable=True),
        sa.Column("bot_id", sa.Integer(), nullable=True),
        sa.Column("opt_level_id", sa.Integer(), nullable=True),
        sa.Column("freq_level_id", sa.Integer(), nullable=True),
        sa.Column("scan_level_id", sa.Integer(), nullable=True),
        sa.Column("irc_level_id", sa.Integer(), nullable=True),
        sa.Column("sp_level_id", sa.Integer(), nullable=False),
        sa.Column("opt_ess_id", sa.Integer(), nullable=True),
        sa.Column("freq_ess_id", sa.Integer(), nullable=True),
        sa.Column("scan_ess_id", sa.Integer(), nullable=True),
        sa.Column("irc_ess_id", sa.Integer(), nullable=True),
        sa.Column("sp_ess_id", sa.Integer(), nullable=False),
        sa.Column("opt_path", sa.String(length=5000), nullable=True),
        sa.Column("freq_path", sa.String(length=5000), nullable=True),
        sa.Column("scan_paths", MsgpackExt(), nullable=True),
        sa.Column("irc_paths", MsgpackExt(), nullable=True),
        sa.Column("sp_path", sa.String(length=5000), nullable=False),
        sa.Column("unconverged_jobs", MsgpackExt(), nullable=True),
        sa.Column("extras", MsgpackExt(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bot_id"],
            ["bot.id"],
        ),
        sa.ForeignKeyConstraint(
            ["freq_ess_id"],
            ["ess.id"],
        ),
        sa.ForeignKeyConstraint(
            ["freq_level_id"],
            ["level.id"],
        ),
        sa.ForeignKeyConstraint(
            ["irc_ess_id"],
            ["ess.id"],
        ),
        sa.ForeignKeyConstraint(
            ["irc_level_id"],
            ["level.id"],
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
        ),
        sa.ForeignKeyConstraint(
            ["opt_ess_id"],
            ["ess.id"],
        ),
        sa.ForeignKeyConstraint(
            ["opt_level_id"],
            ["level.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scan_ess_id"],
            ["ess.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scan_level_id"],
            ["level.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sp_ess_id"],
            ["ess.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sp_level_id"],
            ["level.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_nonphysicalspecies_id"), "nonphysicalspecies", ["id"], unique=False
    )
    pass


def downgrade() -> None:
    op.drop_index(op.f("ix_nonphysicalspecies_id"), table_name="nonphysicalspecies")
    op.drop_table("nonphysicalspecies")
    pass
