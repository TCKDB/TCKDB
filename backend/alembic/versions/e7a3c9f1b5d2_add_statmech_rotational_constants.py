"""statmech: rotational constants (A/B/C, cm^-1)

Schema audit R11. Persists the numerical principal rotational constants
that enter the rotational partition function. The Arkane pdep parser
already extracts them (``rotational_constants_cm_inv``) but the builder
dropped them for lack of a column — data-on-the-floor.

Adds three nullable ``Double`` columns to ``statmech`` (unit cm^-1),
stored in source-provided order (conventionally descending A >= B >= C),
each guarded by a ``> 0`` CHECK. Additive on the deployed ``statmech``
table; existing rows stay valid and no backfill is required.

Revision ID: e7a3c9f1b5d2
Revises: b3e7d1f9a2c4
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7a3c9f1b5d2'
down_revision: Union[str, Sequence[str], None] = 'b3e7d1f9a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'statmech',
        sa.Column('rotational_constant_a_cm1', sa.Double(), nullable=True),
    )
    op.add_column(
        'statmech',
        sa.Column('rotational_constant_b_cm1', sa.Double(), nullable=True),
    )
    op.add_column(
        'statmech',
        sa.Column('rotational_constant_c_cm1', sa.Double(), nullable=True),
    )
    op.create_check_constraint(
        'rotational_constant_a_cm1_positive',
        'statmech',
        'rotational_constant_a_cm1 IS NULL OR rotational_constant_a_cm1 > 0',
    )
    op.create_check_constraint(
        'rotational_constant_b_cm1_positive',
        'statmech',
        'rotational_constant_b_cm1 IS NULL OR rotational_constant_b_cm1 > 0',
    )
    op.create_check_constraint(
        'rotational_constant_c_cm1_positive',
        'statmech',
        'rotational_constant_c_cm1 IS NULL OR rotational_constant_c_cm1 > 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'rotational_constant_c_cm1_positive', 'statmech', type_='check'
    )
    op.drop_constraint(
        'rotational_constant_b_cm1_positive', 'statmech', type_='check'
    )
    op.drop_constraint(
        'rotational_constant_a_cm1_positive', 'statmech', type_='check'
    )
    op.drop_column('statmech', 'rotational_constant_c_cm1')
    op.drop_column('statmech', 'rotational_constant_b_cm1')
    op.drop_column('statmech', 'rotational_constant_a_cm1')
