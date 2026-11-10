"""record where a literature title came from

Adds ``literature.title_source`` (enum ``literature_title_source``:
``resolved_metadata`` | ``depositor_supplied``), set at resolution time from
what actually happened: the title is ``resolved_metadata`` only when the DOI/ISBN
lookup returned one, otherwise it is the depositor's typed text.

Nullable on purpose and NOT backfilled: a row written before this revision
cannot be classified honestly (its title may be either), so NULL means "not
recorded". An existing row matched later by DOI/ISBN is adopted as it stands;
its title and source are never rewritten.

Revision ID: b4d8e1a7c953
Revises: f7a2c9e4b183
Create Date: 2026-11-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d8e1a7c953"
down_revision: Union[str, Sequence[str], None] = "f7a2c9e4b183"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_NAME = "literature_title_source"
_VALUES = ("resolved_metadata", "depositor_supplied")


def upgrade() -> None:
    enum = postgresql.ENUM(*_VALUES, name=_ENUM_NAME)
    enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "literature",
        sa.Column(
            "title_source",
            postgresql.ENUM(*_VALUES, name=_ENUM_NAME, create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("literature", "title_source")
    postgresql.ENUM(name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
