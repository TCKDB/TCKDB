"""Create literature table

Revision ID: cf5bfc2a784f
Revises: 8967f44bd664
Create Date: 2024-10-20 08:36:43.635172

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from tckdb.backend.app.models.common import MsgpackExt


# revision identifiers, used by Alembic.
revision: str = 'cf5bfc2a784f'
down_revision: Union[str, None] = '8967f44bd664'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('literature',
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('journal', sa.String(length=255), nullable=True),
    sa.Column('publisher', sa.String(length=255), nullable=True),
    sa.Column('volume', sa.Integer(), nullable=True),
    sa.Column('issue', sa.Integer(), nullable=True),
    sa.Column('page_start', sa.Integer(), nullable=True),
    sa.Column('page_end', sa.Integer(), nullable=True),
    sa.Column('editors', sa.String(length=255), nullable=True),
    sa.Column('edition', sa.String(length=50), nullable=True),
    sa.Column('chapter_title', sa.String(length=255), nullable=True),
    sa.Column('publication_place', sa.String(length=255), nullable=True),
    sa.Column('advisor', sa.String(length=255), nullable=True),
    sa.Column('doi', sa.String(length=255), nullable=True),
    sa.Column('isbn', sa.String(length=255), nullable=True),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('reviewer_flags', MsgpackExt(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_literature_id'), 'literature', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_literature_id'), table_name='literature')
    op.drop_table('literature')
    pass
