"""add ``parsed_log_archive`` to ``hessian_source``

A Gaussian 16 frequency log of a larger molecule (14 atoms and up in the E2 study: benzaldehyde, n-butane,
nitrobenzene, n-pentane) prints no ``Force constants in Cartesian coordinates`` block, but the archive block at the
end of the log carries the same lower triangle, in hartree/bohr^2, printed to eight decimals. A Hessian read from
there is recorded as ``parsed_log_archive`` rather than ``parsed_log`` because the two print formats differ and the
spectrum recovered from a stored Hessian is judged against the precision it was printed at.

Schema: one value is added to the ``hessian_source`` enum. No table changes and no backfill; existing rows keep
their source.

Downgrade: PostgreSQL cannot drop one enum value without rebuilding the type (the asymmetry ``5eaf03c94f9b``
documents for ``artifact_kind``), so the value stays in the type. ``downgrade()`` refuses while a
``calc_hessian`` row uses it, because the older code would not know how to read that source; on a database
without such rows it is a no-op. ``ADD VALUE IF NOT EXISTS`` makes upgrading again a no-op.

The value is added inside ``autocommit_block()`` (PostgreSQL does not let a value be used in the transaction that
added it; nothing here uses it).

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
    """Add the value to the enum."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE hessian_source ADD VALUE IF NOT EXISTS 'parsed_log_archive'")


def downgrade() -> None:
    """Refuse while a row uses the value; otherwise nothing to undo (an enum value cannot be dropped)."""
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
