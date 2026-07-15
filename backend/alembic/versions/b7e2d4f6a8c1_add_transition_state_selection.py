"""add transition_state_selection

Adds the ``transition_state_selection`` curation-overlay table (the
transition-state analog of ``conformer_selection``) so a curator/workflow can
record which transition-state candidate is the representative / curator pick /
lowest-barrier pick for downstream use. Selection is a human/workflow choice,
so — unlike conformer selection — there is deliberately no assignment-scheme
dimension. See schema-audit ref R6.

Revision ID: b7e2d4f6a8c1
Revises: f4c8b2a6e9d1
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7e2d4f6a8c1'
down_revision: Union[str, Sequence[str], None] = 'f4c8b2a6e9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False: the type is created/dropped explicitly below so that
# create_table() does not also try to auto-create it (which would raise
# DuplicateObject).
transition_state_selection_kind = postgresql.ENUM(
    'display_default',
    'curator_pick',
    'lowest_barrier',
    'benchmark_reference',
    'preferred_for_kinetics',
    'representative_geometry',
    name='transition_state_selection_kind',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    transition_state_selection_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'transition_state_selection',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('transition_state_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'selection_kind',
            transition_state_selection_kind,
            nullable=False,
        ),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ['created_by'], ['app_user.id'],
            name=op.f('fk_transition_state_selection_created_by_app_user'),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ['transition_state_id'], ['transition_state.id'],
            name=op.f(
                'fk_transition_state_selection_transition_state_id_'
                'transition_state'
            ),
            initially='IMMEDIATE', deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            'id', name=op.f('pk_transition_state_selection')
        ),
        sa.UniqueConstraint(
            'transition_state_id',
            'selection_kind',
            name='uq_transition_state_selection_transition_state_id',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('transition_state_selection')
    transition_state_selection_kind.drop(op.get_bind(), checkfirst=True)
