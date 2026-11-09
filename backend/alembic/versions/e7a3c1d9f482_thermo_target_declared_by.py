"""Add thermo.thermodynamic_target_declared_by (enum thermo_declaration_source).

NOT NULL DEFAULT 'depositor': existing targets read depositor, and a row with no target holds the
default and is never surfaced. A constant default is metadata-only, so no UPDATE runs and
accepted-science rows are untouched. Downgrade drops the column and the enum, prints how many
CHEMKIN-format declarations it forgets, and re-upgrading attributes those rows to the depositor.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7a3c1d9f482"
down_revision: Union[str, Sequence[str], None] = "e3a9c5d7b216"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE = postgresql.ENUM(
    "depositor",
    "chemkin_format",
    name="thermo_declaration_source",
    create_type=False,
)


def upgrade() -> None:
    _SOURCE.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "thermo",
        sa.Column(
            "thermodynamic_target_declared_by",
            _SOURCE,
            nullable=False,
            server_default="depositor",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    forgotten = bind.execute(
        sa.text("SELECT count(*) FROM thermo WHERE thermodynamic_target_declared_by <> 'depositor'")
    ).scalar_one()
    print(
        f"e7a3c1d9f482 downgrade forgets: {forgotten} CHEMKIN-format target declaration(s); "
        "re-upgrading attributes these rows to the depositor."
    )
    op.drop_column("thermo", "thermodynamic_target_declared_by")
    _SOURCE.drop(bind, checkfirst=True)
