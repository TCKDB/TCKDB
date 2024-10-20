"""Create level table

Revision ID: 8967f44bd664
Revises: a9ad65ceb5a0
Create Date: 2024-10-20 08:35:49.368477

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt


# revision identifiers, used by Alembic.
revision: str = '8967f44bd664'
down_revision: Union[str, None] = 'a9ad65ceb5a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('level',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('method', sa.String(length=500), nullable=False),
    sa.Column('basis', sa.String(length=500), nullable=True),
    sa.Column('auxiliary_basis', sa.String(length=500), nullable=True),
    sa.Column('dispersion', sa.String(length=500), nullable=True),
    sa.Column('grid', sa.String(length=500), nullable=True),
    sa.Column('level_arguments', sa.String(length=500), nullable=True),
    sa.Column('solvation_method', sa.String(length=500), nullable=True),
    sa.Column('solvent', sa.String(length=500), nullable=True),
    sa.Column('solvation_description', sa.String(length=1000), nullable=True),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_level_id'), 'level', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_level_id'), table_name='level')
    op.drop_table('level')
    pass
