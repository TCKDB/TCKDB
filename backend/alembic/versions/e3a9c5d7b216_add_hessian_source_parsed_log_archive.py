"""add ``parsed_log_archive`` to ``hessian_source``

Upgrade adds the enum value. Downgrade refuses while a ``calc_hessian`` row uses it and is otherwise a no-op
(PostgreSQL cannot drop an enum value).

Revision ID: e3a9c5d7b216
Revises: d5f2b8c1a704
Create Date: 2026-11-09 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e3a9c5d7b216"
down_revision: Union[str, Sequence[str], None] = "d5f2b8c1a704"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the enum value."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE hessian_source ADD VALUE IF NOT EXISTS 'parsed_log_archive'")


def downgrade() -> None:
    """Refuse while a row uses the value."""
    count = (
        op.get_bind()
        .exec_driver_sql("SELECT count(*) FROM calc_hessian WHERE source::text = 'parsed_log_archive'")
        .scalar()
    )
    if count:
        raise RuntimeError(
            f"{count} calc_hessian row(s) have source 'parsed_log_archive'; delete or re-source them before "
            "downgrading, because the older code does not know that source."
        )
