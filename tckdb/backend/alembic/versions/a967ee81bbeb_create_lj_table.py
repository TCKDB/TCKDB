"""Create lj table

Revision ID: a967ee81bbeb
Revises: cf5bfc2a784f
Create Date: 2024-10-20 08:46:53.871617

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt


# revision identifiers, used by Alembic.
revision: str = 'a967ee81bbeb'
down_revision: Union[str, None] = 'cf5bfc2a784f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('lj',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sigma', MsgpackExt(), nullable=False),
    sa.Column('epsilon', MsgpackExt(), nullable=False),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lj_id'), 'lj', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_lj_id'), table_name='lj')
    op.drop_table('lj')
    pass
