"""Add kinetics.t0_k, the reference temperature of the Arrhenius expression (#620).

The scalar rate on a ``kinetics`` row is ``k = A * (T / T0)**n * exp(-Ea / RT)``.
The table stored ``a``, ``n`` and ``ea_kj_mol`` and no T0, so a producer that
fitted with T0 = 298 K had to fold ``A / T0**n`` into ``a`` before depositing,
and the T0 it fitted with was gone. ``t0_k`` carries it.

What this revision writes
-------------------------
* ``kinetics.t0_k`` (DOUBLE PRECISION, NOT NULL, ``server_default '1'``) and the
  CHECK ``ck_kinetics_t0_k_finite_positive`` (``0 < t0_k <= 10000``, which also excludes NaN and infinity).
* No data step is needed. Every row stored so far was deposited without a T0,
  and what it meant was ``A * T**n``, which is T0 = 1 K: the server default is
  the backfill. ``ADD COLUMN ... NOT NULL DEFAULT <constant>`` is a
  metadata-only change on PostgreSQL 11+, and it fires no UPDATE trigger, so
  the accepted-science immutability guards on approved kinetics rows are not
  involved. Once a row exists, the same guards freeze ``t0_k`` with the rest of
  the row, which is correct: changing T0 changes what ``a`` means.
* The existing CHECKs on ``kinetics`` are untouched. The constraint is added
  after the column, so a value violating it cannot exist at any point.

Downgrade drops the constraint and the column. A row deposited with
T0 != 1 K loses that T0 on downgrade and its ``a`` would then be read at 1 K,
which is not the rate that was deposited; downgrade only on a database that
holds no such row, or rescale ``a`` to ``a / T0**n`` first.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f1c8a4d7b263"
down_revision: Union[str, Sequence[str], None] = "f2c8a5d1e9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "kinetics",
        sa.Column("t0_k", sa.Double(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        op.f("ck_kinetics_t0_k_finite_positive"),
        "kinetics",
        "t0_k > 0 AND t0_k <= 10000",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_kinetics_t0_k_finite_positive"), "kinetics", type_="check"
    )
    op.drop_column("kinetics", "t0_k")
