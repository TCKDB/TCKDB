"""widen the tau-basis vocabulary for an assumed default (ADR 0012 amendment)

``e2a7c9d4b615`` closed ``calc_freq_result.imaginary_mode_tau_basis`` to
the five rows of ADR 0012's original protocol table. Measured live on
2026-09-04: 0 of 132 frequency results carry ``freq.hessian_method``,
because Gaussian never names its analytic default in the output and the
parser records only explicit statements -- so ``protocol_not_recorded``
was, in practice, every Gaussian record ever written.

The archive owner's amendment (see the 2026-09-04 addendum to
``docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md``):
when the Hessian method is not recorded, TCKDB now assumes the producing
program's documented default for the level-of-theory's method family
(``app/services/hessian_method_inference.py``), records that it was
*assumed*, and gives the assumed method the same tau as its recorded
counterpart. This revision widens the CHECK to accept the three new
tokens that fact requires: ``assumed_analytic_default``,
``assumed_finite_difference_gradient``, ``assumed_finite_difference_energy``.

**Widening a CHECK never invalidates an existing row.** Every row that
satisfied the five-value constraint still satisfies the eight-value one,
so unlike ``e2a7c9d4b615`` there is nothing to scan for and refuse before
adding it -- the old constraint already proved every stored value is one
of the five, and the new one is a superset. ``upgrade()`` therefore just
drops the old CHECK and installs the wider one, validated in the same
statement.

**No backfill here.** This revision only widens what the column may
hold; nothing is written. ``backend/scripts/ops/backfill_assumed_tau.py``
is the write path, run by an operator against a real database.

**``downgrade()`` refuses rather than guesses**, for the same reason
``e2a7c9d4b615`` refused an unclassifiable value on the way in: a row
holding ``assumed_analytic_default`` says a real judgement was made and
stored, and narrowing the constraint back to five values while leaving
that row in place would either violate the narrower CHECK outright or
require silently deciding what the row should say instead -- which only
the writer that produced it can decide. So ``downgrade()`` first asks
whether any row holds one of the three new values; if so it raises,
naming the count and the values, and changes nothing. Only once no row
holds an assumed basis does it restore ``e2a7c9d4b615``'s five-value
constraint.

Revision ID: ae3d607cc9f8
Revises: 6141f2d98e78
Create Date: 2026-09-04
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "ae3d607cc9f8"
down_revision: Union[str, Sequence[str], None] = "6141f2d98e78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "calc_freq_result"
_COLUMN = "imaginary_mode_tau_basis"

#: Same short name as ``e2a7c9d4b615`` -- this revision widens the same
#: constraint in place rather than renaming it, so
#: ``ck_calc_freq_result_imaginary_mode_tau_basis_known`` keeps meaning
#: "the current vocabulary", not "the vocabulary as of one revision".
_CONSTRAINT = "imaginary_mode_tau_basis_known"
_CONSTRAINT_IN_DB = f"ck_{_TABLE}_{_CONSTRAINT}"

#: ADR 0012's original five rows, as ``e2a7c9d4b615`` spelled them out.
#: Repeated here (not imported) for the reason that revision gives: a
#: migration must keep meaning what it meant when it ran, immune to a
#: later edit of ``tckdb_schemas.stationary_point.TauBasis``.
_ORIGINAL_TAU_BASIS_VALUES: tuple[str, ...] = (
    "analytic_tight",
    "analytic_default",
    "finite_difference_gradient",
    "finite_difference_energy",
    "protocol_not_recorded",
)

#: The three tokens this revision adds. Mirrored in
#: ``tckdb_schemas.stationary_point.TauBasis`` and
#: ``app.db.models.common.IMAGINARY_MODE_TAU_BASIS_VALUES``; the three
#: are pinned together by
#: ``tests/db/test_imaginary_mode_tau_basis_constraint.py``.
_ASSUMED_TAU_BASIS_VALUES: tuple[str, ...] = (
    "assumed_analytic_default",
    "assumed_finite_difference_gradient",
    "assumed_finite_difference_energy",
)

_WIDENED_TAU_BASIS_VALUES: tuple[str, ...] = (
    _ORIGINAL_TAU_BASIS_VALUES + _ASSUMED_TAU_BASIS_VALUES
)


def _condition(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{_COLUMN} IS NULL OR {_COLUMN} IN ({quoted})"


def _refuse_rows_holding_assumed_values() -> None:
    """Stop the downgrade rather than guess what an assumed row meant.

    Named in full in the error, for the same reason ``e2a7c9d4b615``
    names an unclassifiable value: the next question is "which rows?"
    and a migration that answers it is a migration whose failure is
    actionable.
    """
    bind = op.get_bind()
    counts = bind.execute(
        sa.text(
            f"SELECT {_COLUMN}, count(*) FROM {_TABLE} "
            f"WHERE {_COLUMN} = ANY(:assumed) "
            f"GROUP BY {_COLUMN} ORDER BY {_COLUMN}"
        ),
        {"assumed": list(_ASSUMED_TAU_BASIS_VALUES)},
    ).all()
    if counts:
        detail = ", ".join(f"{basis!r}: {n}" for basis, n in counts)
        raise RuntimeError(
            f"{_TABLE}.{_COLUMN} holds rows recorded under an assumed basis "
            f"this downgrade would make illegal: {detail}. Narrowing the "
            "constraint back to ADR 0012's original five values would "
            "either violate it outright or require deciding what those "
            "rows should say instead, which only the writer that produced "
            "them can decide -- this migration will not guess. "
            f"{_CONSTRAINT_IN_DB} was left as the eight-value constraint "
            "and nothing was changed. Re-run the downgrade once those rows "
            "have been repaired or removed (recorded, not assumed, or "
            "deleted)."
        )


def upgrade() -> None:
    """Widen the CHECK to accept the three ``assumed_*`` tau bases."""
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, _condition(_WIDENED_TAU_BASIS_VALUES)
    )


def downgrade() -> None:
    """Restore ``e2a7c9d4b615``'s five-value constraint.

    Refuses first if any row holds a value the narrower constraint would
    reject -- see :func:`_refuse_rows_holding_assumed_values`.
    """
    _refuse_rows_holding_assumed_values()
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, _condition(_ORIGINAL_TAU_BASIS_VALUES)
    )
