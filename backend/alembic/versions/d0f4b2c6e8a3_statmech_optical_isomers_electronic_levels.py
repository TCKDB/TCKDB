"""statmech: optical isomers + electronic energy levels

DR-0033. Adds ``statmech.optical_isomers`` (entropy R*ln(n) contribution)
and the ``statmech_electronic_level`` table of ordered (energy, degeneracy)
pairs for the electronic partition function. Additive on the deployed
``statmech`` table; existing rows stay valid.

Revision ID: d0f4b2c6e8a3
Revises: c9e3a1b5d7f2
Create Date: 2026-07-02 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd0f4b2c6e8a3'
down_revision: Union[str, Sequence[str], None] = 'c9e3a1b5d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'statmech',
        sa.Column('optical_isomers', sa.SmallInteger(), nullable=True),
    )
    op.create_check_constraint(
        'optical_isomers_ge_1',
        'statmech',
        'optical_isomers IS NULL OR optical_isomers >= 1',
    )

    op.create_table(
        'statmech_electronic_level',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('statmech_id', sa.BigInteger(), nullable=False),
        sa.Column('level_index', sa.Integer(), nullable=False),
        sa.Column('energy_cm1', sa.Double(), nullable=False),
        sa.Column('degeneracy', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            'level_index >= 1',
            name=op.f('ck_statmech_electronic_level_level_index_ge_1'),
        ),
        sa.CheckConstraint(
            'energy_cm1 >= 0',
            name=op.f('ck_statmech_electronic_level_energy_cm1_ge_0'),
        ),
        sa.CheckConstraint(
            'degeneracy >= 1',
            name=op.f('ck_statmech_electronic_level_degeneracy_ge_1'),
        ),
        sa.ForeignKeyConstraint(
            ['statmech_id'], ['statmech.id'],
            name=op.f('fk_statmech_electronic_level_statmech_id_statmech'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_statmech_electronic_level')),
        sa.UniqueConstraint(
            'statmech_id', 'level_index', name='uq_statmech_electronic_level'
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('statmech_electronic_level')
    op.drop_constraint('optical_isomers_ge_1', 'statmech', type_='check')
    op.drop_column('statmech', 'optical_isomers')
