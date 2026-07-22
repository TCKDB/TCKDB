"""Add an explicit kinetics degeneracy convention.

Adds a non-null enum column and backfills existing kinetics rows as
``unknown`` through the constant server default. It also adds the database
constraint that ``degeneracy`` is either null or finite and strictly positive,
after a preflight rejects incompatible legacy rows with remediation guidance.

This revision is amended in place because it is still unmerged and undeployed
and already owns the pending degeneracy-convention contract. No deployed
revision is changed. A local database that already applied this in-flight
revision must be recreated before testing the amended revision.

The schema change is reversible, but downgrade permanently discards any
convention values stored in the column. On large databases, adding or dropping
the column and validating the check constraint require an ``ACCESS EXCLUSIVE``
table lock; the constant-default add is catalog-fast on supported PostgreSQL
versions, but the preflight scan, constraint validation, lock acquisition, and
older-server behavior can increase deployment runtime.

Revision ID: f2a4c6e8b0d1
Revises: e9a3c5f7b1d2
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2a4c6e8b0d1"
down_revision: Union[str, Sequence[str], None] = "e9a3c5f7b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


kinetics_degeneracy_convention = postgresql.ENUM(
    "already_applied",
    "not_applied",
    "unknown",
    name="kinetics_degeneracy_convention",
    create_type=False,
)

_DEGENERACY_CONSTRAINT_NAME = "ck_kinetics_degeneracy_finite_positive"
_VALID_DEGENERACY_SQL = (
    "degeneracy IS NULL OR "
    "(degeneracy > 0 AND degeneracy < 'Infinity'::double precision)"
)


def _preflight_legacy_degeneracy(bind) -> None:
    """Reject legacy values that the finite-positive constraint cannot accept."""
    invalid_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM kinetics "
            f"WHERE NOT ({_VALID_DEGENERACY_SQL})"
        )
    )
    if invalid_count:
        raise RuntimeError(
            f"Cannot add {_DEGENERACY_CONSTRAINT_NAME}: found {invalid_count} "
            "kinetics row(s) whose degeneracy is not NULL or a finite value "
            "greater than zero. Set each invalid degeneracy to NULL or a finite "
            "positive value, then rerun `alembic upgrade head`."
        )


def upgrade() -> None:
    """Add the convention and enforce null-or-finite-positive degeneracy."""
    bind = op.get_bind()
    _preflight_legacy_degeneracy(bind)
    kinetics_degeneracy_convention.create(bind, checkfirst=True)
    op.add_column(
        "kinetics",
        sa.Column(
            "degeneracy_convention",
            kinetics_degeneracy_convention,
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_check_constraint(
        op.f(_DEGENERACY_CONSTRAINT_NAME),
        "kinetics",
        _VALID_DEGENERACY_SQL,
    )


def downgrade() -> None:
    """Remove the degeneracy constraint, convention column, and enum type."""
    op.drop_constraint(
        op.f(_DEGENERACY_CONSTRAINT_NAME),
        "kinetics",
        type_="check",
    )
    op.drop_column("kinetics", "degeneracy_convention")
    bind = op.get_bind()
    kinetics_degeneracy_convention.drop(bind, checkfirst=True)
