"""enable rdkit extension

Revision ID: 60b67e360daf
Revises:
Create Date: 2026-02-24 20:11:19.973866

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "60b67e360daf"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS rdkit;")


def downgrade() -> None:
    """Downgrade schema."""
    pass
