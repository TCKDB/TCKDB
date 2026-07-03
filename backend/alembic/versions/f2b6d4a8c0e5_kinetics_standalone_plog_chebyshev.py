"""kinetics: standalone reaction-level PLOG + Chebyshev tables

DR-0032 Part C. Adds ``kinetics_plog`` (per-pressure modified-Arrhenius
entries) and ``kinetics_chebyshev`` (n_T × n_P coefficient surface + T/P
domain) at the reaction level, so a literature PLOG/Chebyshev fit can be
deposited without fabricating a master-equation network + solve. The
``plog``/``chebyshev`` ``kinetics_model_kind`` enum values were already
added in the falloff revision. Additive; existing rows unaffected.

Revision ID: f2b6d4a8c0e5
Revises: e1a5c3f7b9d4
Create Date: 2026-07-02 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f2b6d4a8c0e5'
down_revision: Union[str, Sequence[str], None] = 'e1a5c3f7b9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


arrhenius_a_units = postgresql.ENUM(name='arrhenius_a_units', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'kinetics_plog',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('kinetics_id', sa.BigInteger(), nullable=False),
        sa.Column('entry_index', sa.Integer(), nullable=False),
        sa.Column('pressure_bar', sa.Double(), nullable=False),
        sa.Column('a', sa.Double(), nullable=False),
        sa.Column('a_units', arrhenius_a_units, nullable=True),
        sa.Column('n', sa.Double(), nullable=True),
        sa.Column('ea_kj_mol', sa.Double(), nullable=True),
        sa.CheckConstraint(
            'entry_index >= 1', name=op.f('ck_kinetics_plog_plog_entry_index_ge_1')
        ),
        sa.CheckConstraint(
            'pressure_bar > 0', name=op.f('ck_kinetics_plog_plog_pressure_bar_gt_0')
        ),
        sa.ForeignKeyConstraint(
            ['kinetics_id'], ['kinetics.id'],
            name=op.f('fk_kinetics_plog_kinetics_id_kinetics'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_kinetics_plog')),
        sa.UniqueConstraint(
            'kinetics_id', 'entry_index', name='uq_kinetics_plog_entry'
        ),
    )

    op.create_table(
        'kinetics_chebyshev',
        sa.Column('kinetics_id', sa.BigInteger(), nullable=False),
        sa.Column('n_temperature', sa.SmallInteger(), nullable=False),
        sa.Column('n_pressure', sa.SmallInteger(), nullable=False),
        sa.Column('tmin_k', sa.Double(), nullable=True),
        sa.Column('tmax_k', sa.Double(), nullable=True),
        sa.Column('pmin_bar', sa.Double(), nullable=True),
        sa.Column('pmax_bar', sa.Double(), nullable=True),
        sa.Column('coefficients', postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            'n_temperature >= 1', name=op.f('ck_kinetics_chebyshev_cheb_n_temperature_ge_1')
        ),
        sa.CheckConstraint(
            'n_pressure >= 1', name=op.f('ck_kinetics_chebyshev_cheb_n_pressure_ge_1')
        ),
        sa.ForeignKeyConstraint(
            ['kinetics_id'], ['kinetics.id'],
            name=op.f('fk_kinetics_chebyshev_kinetics_id_kinetics'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint('kinetics_id', name=op.f('pk_kinetics_chebyshev')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('kinetics_chebyshev')
    op.drop_table('kinetics_plog')
