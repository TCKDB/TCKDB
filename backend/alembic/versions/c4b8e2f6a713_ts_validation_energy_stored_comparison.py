"""record what an energy-ordering energy was held against (issue #638)

An ``energy_ordering`` evidence row states one energy per participant and cites
the calculation each was taken from. Until now the stated number was compared
with the other stated numbers and never with what TCKDB stores for the cited
calculation, so a record could pass while the stored energies put the saddle
point below a well. The upload now holds each stated energy against the stored
one (electronic from the cited ``sp`` or ``opt``; E0 from the paired electronic
energy plus the cited ``freq``'s zero-point energy, scaled by the producer's
stated ``zpe_scale_factor`` when there is one) and refuses a contradiction.
This revision adds the place the *other* outcomes, and the stated factor, are
recorded.

Schema
------
Three nullable columns on ``transition_state_validation_energy``:

* ``stored_energy_comparison`` -- ``agrees`` or ``not_compared``. A disagreement
  is never stored; it refuses the deposit.
* ``not_compared_reason`` -- why a comparison could not be made. Required exactly
  when the status is ``not_compared`` and NULL otherwise
  (``ck_..._not_compared_reason_shape``), and drawn from a fixed set of tokens
  (``ck_..._not_compared_reason_token``): ``stored_energy_not_stated``,
  ``zpe_not_stated``, ``no_electronic_energy_to_pair``, ``geometry_not_paired``,
  ``zpe_scaling_unstated``.
* ``zpe_scale_factor`` -- ``e0`` only: the factor the producer multiplied the
  stored (unscaled) zero-point energy by in forming the stated E0. NULL is "none
  stated", never 1.0 (``ck_..._zpe_scale_factor_e0``).

No backfill
-----------
Rows deposited before this revision keep all three columns NULL, which reads as
"the comparison did not exist", never as a pass. Nothing here can be recomputed
honestly after the fact without re-reading the cited calculation as it was when
the record was accepted, and the rows are frozen under accepted entries.
``ALTER TABLE`` does not fire the accepted-science row guard, so the registry is
untouched.

Downgrade
---------
Drops the constraints and the three columns. It refuses, with the count, if any
row recorded a comparison outcome or a factor: dropping the columns would discard
the only statement that a stated energy was checked (or could not be), or how its
zero-point energy was scaled.

Revision ID: c4b8e2f6a713
Revises: a9c3e7b1d5f2
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c4b8e2f6a713"
down_revision: Union[str, Sequence[str], None] = "a9c3e7b1d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "transition_state_validation_energy"
_STATUS_CHECK = "ck_transition_state_validation_energy_stored_energy_comparison"
_REASON_CHECK = "ck_transition_state_validation_energy_not_compared_reason_shape"
_TOKEN_CHECK = "ck_transition_state_validation_energy_not_compared_reason_token"
_SCALE_CHECK = "ck_transition_state_validation_energy_zpe_scale_factor_e0"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("stored_energy_comparison", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("not_compared_reason", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("zpe_scale_factor", sa.Float(), nullable=True))
    op.execute(
        f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_STATUS_CHECK} "
        "CHECK (stored_energy_comparison IS NULL OR stored_energy_comparison IN ('agrees', 'not_compared'))"
    )
    op.execute(
        f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_REASON_CHECK} "
        "CHECK ((stored_energy_comparison IS NOT DISTINCT FROM 'not_compared') = (not_compared_reason IS NOT NULL))"
    )
    op.execute(
        f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_TOKEN_CHECK} "
        "CHECK (not_compared_reason IS NULL OR not_compared_reason IN ("
        "'stored_energy_not_stated', 'zpe_not_stated', 'no_electronic_energy_to_pair', "
        "'geometry_not_paired', 'zpe_scaling_unstated'))"
    )
    op.execute(
        f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT {_SCALE_CHECK} "
        "CHECK (zpe_scale_factor IS NULL OR (energy_kind = 'e0' AND zpe_scale_factor > 0 "
        "AND zpe_scale_factor < 'Infinity'::float8))"
    )


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text(
            f"SELECT count(*) FROM {_TABLE} "
            "WHERE stored_energy_comparison IS NOT NULL OR zpe_scale_factor IS NOT NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} {_TABLE} row(s) record the outcome of comparing a stated energy "
            "with the stored one, or the zero-point scale factor of a stated E0. Dropping the columns "
            "would discard the only statement that it was checked, or could not be. Nothing is deleted; "
            "resolve those rows first."
        )
    for name in (_SCALE_CHECK, _TOKEN_CHECK, _REASON_CHECK, _STATUS_CHECK):
        op.execute(f"ALTER TABLE public.{_TABLE} DROP CONSTRAINT {name}")
    op.drop_column(_TABLE, "zpe_scale_factor")
    op.drop_column(_TABLE, "not_compared_reason")
    op.drop_column(_TABLE, "stored_energy_comparison")
