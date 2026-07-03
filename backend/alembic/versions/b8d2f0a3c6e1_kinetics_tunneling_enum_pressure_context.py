"""kinetics: tunneling enum + pressure context (k-infinity designation)

Part A of DR-0032. Converts the free-text ``kinetics.tunneling_model``
column to the ``tunneling_model`` enum (mapping known tokens, folding any
other non-null value to ``other``), and adds a nullable
``pressure_context`` enum + ``pressure_bar`` so a rate coefficient can be
marked as the high-pressure limit (k∞) or an apparent rate at a specific
pressure. Additive on a deployed table; existing rows stay valid.

Revision ID: b8d2f0a3c6e1
Revises: a7c1e9d2f4b8
Create Date: 2026-07-02 08:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8d2f0a3c6e1'
down_revision: Union[str, Sequence[str], None] = 'a7c1e9d2f4b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


tunneling_model = postgresql.ENUM(
    'none', 'wigner', 'eckart', 'sct', 'other',
    name='tunneling_model',
    create_type=False,
)
pressure_context = postgresql.ENUM(
    'high_p_limit', 'apparent_at_pressure', 'pressure_dependent',
    name='pressure_context',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    tunneling_model.create(bind, checkfirst=True)
    pressure_context.create(bind, checkfirst=True)

    # Convert the free-text tunneling_model column to the enum, mapping
    # recognized tokens (case-insensitively) and folding any other
    # non-null value to 'other'. NULL stays NULL.
    op.execute(
        """
        ALTER TABLE kinetics
        ALTER COLUMN tunneling_model TYPE tunneling_model
        USING (
            CASE
                WHEN tunneling_model IS NULL THEN NULL
                WHEN lower(tunneling_model) IN ('none', 'wigner', 'eckart', 'sct')
                    THEN lower(tunneling_model)::tunneling_model
                ELSE 'other'::tunneling_model
            END
        )
        """
    )

    op.add_column(
        'kinetics',
        sa.Column('pressure_context', pressure_context, nullable=True),
    )
    op.add_column(
        'kinetics',
        sa.Column('pressure_bar', sa.Double(), nullable=True),
    )
    op.create_check_constraint(
        'pressure_bar_gt_0', 'kinetics', 'pressure_bar IS NULL OR pressure_bar > 0'
    )
    op.create_check_constraint(
        'apparent_pressure_requires_pressure_bar',
        'kinetics',
        "pressure_context <> 'apparent_at_pressure' OR pressure_bar IS NOT NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'apparent_pressure_requires_pressure_bar', 'kinetics', type_='check'
    )
    op.drop_constraint('pressure_bar_gt_0', 'kinetics', type_='check')
    op.drop_column('kinetics', 'pressure_bar')
    op.drop_column('kinetics', 'pressure_context')

    # Revert tunneling_model to free text.
    op.execute(
        "ALTER TABLE kinetics "
        "ALTER COLUMN tunneling_model TYPE text "
        "USING tunneling_model::text"
    )

    bind = op.get_bind()
    pressure_context.drop(bind, checkfirst=True)
    tunneling_model.drop(bind, checkfirst=True)
