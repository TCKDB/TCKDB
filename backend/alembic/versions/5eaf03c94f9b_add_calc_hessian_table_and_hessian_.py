"""add calc_hessian table and hessian artifact kind

Adds the append-only, one-row-per-calculation ``calc_hessian`` side table
(the Cartesian second-derivative matrix, bound to the geometry it was
computed at) and extends the ``artifact_kind`` enum with ``hessian`` so
raw ``.hess`` / ``.fchk`` sidecars can ride along as audit-trail
artifacts. See DR-0030.

Scope note: this revision deliberately touches ONLY the Hessian feature.
The ``molecular_property_observation`` type/timestamp drift and the
``ix_species_entry_mol_gist`` index that ``--autogenerate`` also surfaced
are pre-existing model/migration inconsistencies unrelated to this
change (tracked in plan.md discovered-issues); folding them in here would
mislabel their history.

Revision ID: 5eaf03c94f9b
Revises: e1f2a3b4c5d6
Create Date: 2026-07-02 05:19:15.472295

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5eaf03c94f9b'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False: the type is created/dropped explicitly below so that
# create_table() does not also try to auto-create it (which would raise
# DuplicateObject).
hessian_source = postgresql.ENUM(
    'parsed_fchk', 'parsed_hess', 'parsed_log', 'uploaded', 'derived',
    name='hessian_source',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    # Extend the existing artifact_kind enum. On PG 12+ ADD VALUE works
    # inside a transaction; the new value must not be *used* in this same
    # migration (it is not).
    op.execute("ALTER TYPE artifact_kind ADD VALUE IF NOT EXISTS 'hessian'")

    hessian_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'calc_hessian',
        sa.Column('calculation_id', sa.BigInteger(), nullable=False),
        sa.Column('geometry_id', sa.BigInteger(), nullable=False),
        sa.Column('natoms', sa.Integer(), nullable=False),
        sa.Column(
            'lower_triangle_hartree_bohr2',
            postgresql.ARRAY(sa.Float()),
            nullable=False,
        ),
        sa.Column(
            'source',
            hessian_source,
            nullable=False,
        ),
        sa.Column('parser_version', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            'cardinality(lower_triangle_hartree_bohr2) '
            '= (3 * natoms) * (3 * natoms + 1) / 2',
            name=op.f('ck_calc_hessian_hessian_lower_triangle_cardinality'),
        ),
        sa.CheckConstraint(
            'natoms >= 1',
            name=op.f('ck_calc_hessian_hessian_natoms_ge_1'),
        ),
        sa.ForeignKeyConstraint(
            ['calculation_id'], ['calculation.id'],
            name=op.f('fk_calc_hessian_calculation_id_calculation'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['app_user.id'],
            name=op.f('fk_calc_hessian_created_by_app_user'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ['geometry_id'], ['geometry.id'],
            name=op.f('fk_calc_hessian_geometry_id_geometry'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_hessian')),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and the ``hessian_source`` enum type. The ``hessian``
    value added to ``artifact_kind`` is intentionally NOT removed:
    PostgreSQL cannot drop an enum value, and any artifact row created
    with that kind must remain valid. This is a safe, standard asymmetry
    for enum-extension migrations.
    """
    op.drop_table('calc_hessian')
    hessian_source.drop(op.get_bind(), checkfirst=True)
