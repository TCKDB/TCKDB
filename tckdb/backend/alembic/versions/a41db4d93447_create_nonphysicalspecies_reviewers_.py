"""Create nonphysicalspecies reviewers table

Revision ID: a41db4d93447
Revises: b8e5899e04b9
Create Date: 2024-10-20 10:26:03.331184

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a41db4d93447'
down_revision: Union[str, None] = 'b8e5899e04b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('np_species_reviewers',
    sa.Column('np_species_id', sa.Integer(), nullable=False),
    sa.Column('reviewer_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['np_species_id'], ['nonphysicalspecies.id'], ),
    sa.ForeignKeyConstraint(['reviewer_id'], ['person.id'], ),
    sa.PrimaryKeyConstraint('np_species_id', 'reviewer_id')
    )
    pass


def downgrade() -> None:
    op.drop_table('np_species_reviewers')
    pass
