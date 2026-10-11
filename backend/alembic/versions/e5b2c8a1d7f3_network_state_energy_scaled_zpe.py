"""let a network state energy cite a frequency ZPE and a scale factor (issue #693)

#678 compared an ``electronic_plus_zpe`` state energy only when every source was a composite that
stores an E0. The usual source is an sp electronic energy plus a frequency calculation's
zero-point energy scaled by a stated factor, which the one source per participant could not hold.

Schema
------
* ``network_solve_state_energy_source.zpe_calculation_id`` -- nullable foreign key to
  ``calculation``: the ``freq`` whose zero-point energy the participant adds to the energy of
  ``calculation_id``. NULL for every existing row and for a participant that cites none (an atom, a
  composite that stores its E0, an ``electronic_only`` state).
* ``network_solve_state_energy.zpe_scale_factor`` -- nullable ``double precision``, positive and
  finite: the factor the producer scaled the ZPEs with. NULL is "not stated", never one.
* ``ck_network_solve_state_energy_sum_reason_token`` is replaced to admit three more reasons a
  comparison can fail to be made: ``zpe_not_stated`` (the cited freq stores no ZPE),
  ``geometry_not_paired`` (the sp and the freq are not at one geometry) and ``zpe_scaling_unstated``
  (no ``zpe_scale_factor``). Every previously valid token stays valid.

Accepted-science immutability
-----------------------------
``ALTER TABLE ... ADD COLUMN`` does not fire the row guard on either table, and neither table is new,
so no trigger or registry entry changes. ``zpe_calculation_id`` is provenance, not ownership.

No backfill
-----------
Existing rows keep both columns NULL and read as "no ZPE was cited, no factor was stated", which is
what they are. Nothing is recomputed: the rows are frozen under accepted solves.

Downgrade
---------
Refuses, with the counts, while any row cites a ZPE calculation, states a scale factor or records one
of the three new reasons: dropping them would discard the only statement of how a summed energy was
formed. It deletes nothing. Otherwise it restores the previous token check and drops the columns.

Revision ID: e5b2c8a1d7f3
Revises: a7d2c9e5b413
Create Date: 2026-10-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5b2c8a1d7f3"
down_revision: Union[str, Sequence[str], None] = "a7d2c9e5b413"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE = "network_solve_state_energy_source"
_ENERGY = "network_solve_state_energy"
_TOKEN_CHECK = "ck_network_solve_state_energy_sum_reason_token"
_SCALE_CHECK = "ck_network_solve_state_energy_zpe_scale_factor_positive"
_ZPE_FK = "fk_nsse_source_zpe_calculation_id_calculation"

_OLD_TOKENS = (
    "'no_source_stated', 'sources_incomplete', 'convention_not_summable', "
    "'energy_zero_not_comparable', 'stored_energy_not_stated', 'zpe_not_in_source', "
    "'no_second_state_on_the_same_zero', 'stated_precision_unknown'"
)
_NEW_REASONS = ("zpe_not_stated", "geometry_not_paired", "zpe_scaling_unstated")
_NEW_TOKENS = _OLD_TOKENS + ", " + ", ".join(f"'{reason}'" for reason in _NEW_REASONS)


def _replace_token_check(tokens: str) -> None:
    op.execute(f"ALTER TABLE public.{_ENERGY} DROP CONSTRAINT {_TOKEN_CHECK}")
    op.execute(
        f"ALTER TABLE public.{_ENERGY} ADD CONSTRAINT {_TOKEN_CHECK} "
        f"CHECK (source_sum_not_compared_reason IS NULL OR source_sum_not_compared_reason IN ({tokens}))"
    )


def upgrade() -> None:
    op.add_column(_SOURCE, sa.Column("zpe_calculation_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        _ZPE_FK,
        _SOURCE,
        "calculation",
        ["zpe_calculation_id"],
        ["id"],
        initially="IMMEDIATE",
        deferrable=True,
    )
    op.add_column(_ENERGY, sa.Column("zpe_scale_factor", sa.Double(), nullable=True))
    op.execute(
        f"ALTER TABLE public.{_ENERGY} ADD CONSTRAINT {_SCALE_CHECK} "
        "CHECK (zpe_scale_factor IS NULL OR (zpe_scale_factor > 0 AND zpe_scale_factor < 'Infinity'::float8))"
    )
    _replace_token_check(_NEW_TOKENS)


def downgrade() -> None:
    bind = op.get_bind()
    cited = bind.execute(
        sa.text(f"SELECT count(*) FROM {_SOURCE} WHERE zpe_calculation_id IS NOT NULL")
    ).scalar_one()
    reasons = ", ".join(f"'{reason}'" for reason in _NEW_REASONS)
    recorded = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {_ENERGY} "
            f"WHERE zpe_scale_factor IS NOT NULL OR source_sum_not_compared_reason IN ({reasons})"
        )
    ).scalar_one()
    if cited or recorded:
        raise RuntimeError(
            f"Cannot downgrade: {cited} {_SOURCE} row(s) cite a zero-point-energy calculation and "
            f"{recorded} {_ENERGY} row(s) state a ZPE scale factor or record a ZPE-related reason a "
            "comparison was not made. Dropping them would discard the only statement of how a summed "
            "energy was formed. Nothing is deleted; resolve those rows first. Rows under an accepted "
            "(frozen) solve cannot be deleted or edited, so for them this refusal is permanent."
        )
    _replace_token_check(_OLD_TOKENS)
    op.execute(f"ALTER TABLE public.{_ENERGY} DROP CONSTRAINT {_SCALE_CHECK}")
    op.drop_column(_ENERGY, "zpe_scale_factor")
    op.drop_constraint(_ZPE_FK, _SOURCE, type_="foreignkey")
    op.drop_column(_SOURCE, "zpe_calculation_id")
