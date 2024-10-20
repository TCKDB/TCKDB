"""Species NULL Approved

Revision ID: ba97ec975f85
Revises: 2c5fceb709a1
Create Date: 2024-10-18 18:38:08.028714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba97ec975f85'
down_revision: Union[str, None] = '2c5fceb709a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('species', 'approved',
               existing_type=sa.BOOLEAN(),
               nullable=True)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('species', 'approved',
               existing_type=sa.BOOLEAN(),
               nullable=False)
    # ### end Alembic commands ###
