"""Create encorr table

Revision ID: 3d67b1f72380
Revises: bcc7cfb900f3
Create Date: 2024-10-20 10:18:41.191687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3d67b1f72380'
down_revision: Union[str, None] = 'bcc7cfb900f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('encorr',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('supported_elements', postgresql.ARRAY(sa.String(), zero_indexes=True), nullable=False),
    sa.Column('energy_unit', sa.String(length=255), nullable=False),
    sa.Column('aec', MsgpackExt(), nullable=True),
    sa.Column('bac', MsgpackExt(), nullable=True),
    sa.Column('isodesmic_reactions', MsgpackExt(), nullable=True),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.Column('level_id', sa.Integer(), nullable=True),
    sa.Column('isodesmic_high_level_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['isodesmic_high_level_id'], ['level.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['level_id'], ['level.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_encorr_id'), 'encorr', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_encorr_id'), table_name='encorr')
    op.drop_table('encorr')
    pass
