"""Create trans table

Revision ID: bcc7cfb900f3
Revises: b20a3fef2024
Create Date: 2024-10-20 10:17:43.089114

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt


# revision identifiers, used by Alembic.
revision: str = 'bcc7cfb900f3'
down_revision: Union[str, None] = 'b20a3fef2024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('trans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('parameters', MsgpackExt(), nullable=False),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trans_id'), 'trans', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_trans_id'), table_name='trans')
    op.drop_table('trans')
    pass
