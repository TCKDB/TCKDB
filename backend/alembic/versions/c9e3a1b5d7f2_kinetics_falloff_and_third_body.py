"""kinetics: falloff/Troe parameters + third-body efficiencies

DR-0032 Part B. Extends the ``kinetics_model_kind`` enum with the
pressure-dependent falloff and standalone-fit forms and adds the
``kinetics_falloff`` (low-pressure Arrhenius + Troe/SRI broadening) and
``kinetics_third_body_efficiency`` (per-collider [M] scaling) tables.
Additive; existing rows unaffected. The plog/chebyshev enum values are
added here (cheap) but their tables land in Part C.

Revision ID: c9e3a1b5d7f2
Revises: b8d2f0a3c6e1
Create Date: 2026-07-02 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9e3a1b5d7f2'
down_revision: Union[str, Sequence[str], None] = 'b8d2f0a3c6e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Existing enum, reused for the low-pressure Arrhenius units column.
arrhenius_a_units = postgresql.ENUM(name='arrhenius_a_units', create_type=False)

_NEW_MODEL_KINDS = ('lindemann', 'troe', 'sri', 'plog', 'chebyshev')


def upgrade() -> None:
    """Upgrade schema."""
    for value in _NEW_MODEL_KINDS:
        op.execute(
            f"ALTER TYPE kinetics_model_kind ADD VALUE IF NOT EXISTS '{value}'"
        )

    op.create_table(
        'kinetics_falloff',
        sa.Column('kinetics_id', sa.BigInteger(), nullable=False),
        sa.Column('low_a', sa.Double(), nullable=False),
        sa.Column('low_a_units', arrhenius_a_units, nullable=True),
        sa.Column('low_n', sa.Double(), nullable=True),
        sa.Column('low_ea_kj_mol', sa.Double(), nullable=True),
        sa.Column('troe_alpha', sa.Double(), nullable=True),
        sa.Column('troe_t3', sa.Double(), nullable=True),
        sa.Column('troe_t1', sa.Double(), nullable=True),
        sa.Column('troe_t2', sa.Double(), nullable=True),
        sa.Column('sri_a', sa.Double(), nullable=True),
        sa.Column('sri_b', sa.Double(), nullable=True),
        sa.Column('sri_c', sa.Double(), nullable=True),
        sa.Column('sri_d', sa.Double(), nullable=True),
        sa.Column('sri_e', sa.Double(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['kinetics_id'], ['kinetics.id'],
            name=op.f('fk_kinetics_falloff_kinetics_id_kinetics'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint('kinetics_id', name=op.f('pk_kinetics_falloff')),
    )

    op.create_table(
        'kinetics_third_body_efficiency',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('kinetics_id', sa.BigInteger(), nullable=False),
        sa.Column('collider_species_id', sa.BigInteger(), nullable=False),
        sa.Column('efficiency', sa.Double(), nullable=False),
        sa.CheckConstraint(
            'efficiency >= 0',
            name=op.f('ck_kinetics_third_body_efficiency_efficiency_ge_0'),
        ),
        sa.ForeignKeyConstraint(
            ['collider_species_id'], ['species.id'],
            name=op.f('fk_kinetics_third_body_efficiency_collider_species_id_species'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ['kinetics_id'], ['kinetics.id'],
            name=op.f('fk_kinetics_third_body_efficiency_kinetics_id_kinetics'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            'id', name=op.f('pk_kinetics_third_body_efficiency')
        ),
        sa.UniqueConstraint(
            'kinetics_id', 'collider_species_id', name='uq_kinetics_collider'
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the two tables. The enum values added to ``kinetics_model_kind``
    are intentionally not removed (PostgreSQL cannot drop an enum value,
    and any row created with one must remain valid) — the standard, safe
    asymmetry for enum-extension migrations.
    """
    op.drop_table('kinetics_third_body_efficiency')
    op.drop_table('kinetics_falloff')
