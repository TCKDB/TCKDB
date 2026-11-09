"""Say who made a thermo record's thermodynamic-target declaration.

One column on ``thermo`` and one enum type:

* ``thermodynamic_target_declared_by`` (enum ``thermo_declaration_source``:
  ``depositor`` | ``chemkin_format``), ``NOT NULL DEFAULT 'depositor'``.

Why a default, and why ``depositor``
------------------------------------
Before this revision the only way a thermo row got a
``thermodynamic_target_kind`` was a depositor declaring it on an upload. The
CHEMKIN importer declared nothing, and no other producer sets a target on its
own. So every existing row that has a target is, in fact, depositor-declared;
``depositor`` is the one honest value for it, not an assumption. A row with no
target (``thermodynamic_target_kind IS NULL``) has nothing for the column to
describe; the column carries the default there and every read surfaces it only
inside a target block, so it never appears for a row with no target.

Why NOT NULL with a server default, rather than nullable plus a backfill
-------------------------------------------------------------------------
``thermo`` is an accepted-science root (``c6f2a9d4e7b1``,
``trg_as_root_thermo``): an UPDATE of a row that has ever been approved is
refused without a declared repair. A nullable column plus
``UPDATE ... SET declared_by = 'depositor' WHERE kind IS NOT NULL`` would need a
repair declaration and a ledger for a change that changes no number and no
claim. ``ADD COLUMN ... NOT NULL DEFAULT <constant>`` is a metadata-only change
in PostgreSQL 11+: it rewrites nothing and fires no UPDATE trigger, so the guard
is not involved and approved rows are not touched.

No CHECK ties the column to ``thermodynamic_target_kind``: for the reason above
a row without a target holds the default, so "set iff a target" is not a rule
the table can state.

Downgrade drops the column and the enum. It forgets which target declarations
were made by the CHEMKIN format; it prints how many it is forgetting first.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7a3c1d9f482"
down_revision: Union[str, Sequence[str], None] = "d5f2b8c1a704"
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
    print(f"e7a3c1d9f482 downgrade forgets: {forgotten} CHEMKIN-format target declaration(s).")
    op.drop_column("thermo", "thermodynamic_target_declared_by")
    _SOURCE.drop(bind, checkfirst=True)
