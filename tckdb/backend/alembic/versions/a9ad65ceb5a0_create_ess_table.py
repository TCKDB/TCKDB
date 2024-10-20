"""Create ess table

Revision ID: a9ad65ceb5a0
Revises: 97e84f0d0aa5
Create Date: 2024-10-20 08:35:00.093855

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt


# revision identifiers, used by Alembic.
revision: str = 'a9ad65ceb5a0'
down_revision: Union[str, None] = '97e84f0d0aa5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ess',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('version', sa.String(length=100), nullable=False),
    sa.Column('revision', sa.String(length=100), nullable=False),
    sa.Column('url', sa.String(length=255), nullable=False),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_ess_id'), table_name='ess')
    op.drop_table('ess')
    pass
