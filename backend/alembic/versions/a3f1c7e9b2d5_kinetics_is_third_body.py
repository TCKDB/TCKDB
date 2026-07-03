"""kinetics: add is_third_body marker for simple third-body reactions

Adds a non-nullable boolean ``is_third_body`` (server_default ``false``) to
the deployed ``kinetics`` table. It marks a *simple* third-body reaction — a
generic ``+M`` collider with no falloff — whose main-line Arrhenius rate is
one concentration order higher than the reactant molecularity (the ``[M]``
term adds an order, so ``A + B + M`` main-line A-units are order-3). Falloff
reactions keep ``false``: their main line is the high-pressure limit k∞.

Additive column on an already-deployed table (see migration-rules.md
"already-deployed tables"): both ``upgrade()`` and ``downgrade()`` are
implemented and existing rows default to ``false``.

Revision ID: a3f1c7e9b2d5
Revises: f2b6d4a8c0e5
Create Date: 2026-07-02 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3f1c7e9b2d5'
down_revision: Union[str, Sequence[str], None] = 'f2b6d4a8c0e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'kinetics',
        sa.Column(
            'is_third_body',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('kinetics', 'is_third_body')
