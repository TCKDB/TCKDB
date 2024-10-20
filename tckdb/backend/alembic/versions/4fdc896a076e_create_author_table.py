"""create autor table

Revision ID: 4fdc896a076e
Revises: 738e1beaa271
Create Date: 2024-10-20 08:21:26.972488

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4fdc896a076e'
down_revision: Union[str, None] = '738e1beaa271'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('author',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.String(), nullable=False),
    sa.Column('last_name', sa.String(), nullable=False),
    sa.Column('orcid', sa.String(length=19), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('first_name', 'last_name', name='_author_name_uc')
    )
    op.create_index('ix_author_full_name', 'author', ['first_name', 'last_name'], unique=True)
    op.create_index(op.f('ix_author_orcid'), 'author', ['orcid'], unique=True)
    op.create_index(op.f('ix_author_id'), 'author', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_author_id'), table_name='author')
    op.drop_index(op.f('ix_author_orcid'), table_name='author')
    op.drop_index('ix_author_full_name', table_name='author')
    op.drop_table('author')
    pass
