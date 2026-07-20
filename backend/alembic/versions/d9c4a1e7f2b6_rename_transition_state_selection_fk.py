"""rename transition_state_selection FK to an explicit short name

The ``transition_state_selection.transition_state_id`` foreign key was created
without an explicit name, so it inherited the repo ``NAMING_CONVENTION``
(``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``). For this
column that renders to a 66-character identifier
(``fk_transition_state_selection_transition_state_id_transition_state``),
which exceeds PostgreSQL's 63-character identifier limit. SQLAlchemy silently
truncates it (deterministic hash suffix) to the name actually stored in every
deployed DB:

    fk_transition_state_selection_transition_state_id_trans_c45a

That works today only because the model and the baseline migration truncate
identically, but it is fragile and opaque. The model now declares an explicit
short name (``fk_ts_selection_transition_state``); this revision renames the
existing constraint in place so the deployed DB matches the model.

``transition_state_selection`` is already deployed (revision
``b7e2d4f6a8c1``), so per ``.claude/rules/migration-rules.md`` this is a new
revision rather than an edit to the original. ``RENAME CONSTRAINT`` is a
metadata-only operation (no table rewrite, the FK and its enforcement are
preserved); we deliberately do NOT drop/recreate the FK.

Revision ID: d9c4a1e7f2b6
Revises: a5c8e2f4b6d1
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9c4a1e7f2b6"
down_revision: Union[str, Sequence[str], None] = "a5c8e2f4b6d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The truncated name SQLAlchemy emits from the naming convention (deployed).
_OLD_NAME = "fk_transition_state_selection_transition_state_id_trans_c45a"
# The explicit short name now declared on the model.
_NEW_NAME = "fk_ts_selection_transition_state"
_TABLE = "transition_state_selection"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f'ALTER TABLE {_TABLE} '
        f'RENAME CONSTRAINT "{_OLD_NAME}" TO "{_NEW_NAME}"'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        f'ALTER TABLE {_TABLE} '
        f'RENAME CONSTRAINT "{_NEW_NAME}" TO "{_OLD_NAME}"'
    )
