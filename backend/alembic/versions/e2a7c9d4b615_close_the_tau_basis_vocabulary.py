"""close the tau-basis vocabulary at the write side

``c2f7a4e8d1b6`` added ``calc_freq_result.imaginary_mode_tau_basis`` as
plain ``TEXT``. It carries which row of ADR 0012's protocol table set the
tolerance a record's imaginary modes were judged at, and the only thing
that has ever written it is ``TauBasis(...).value`` -- so every stored
value is one of five tokens by construction and by nothing else. Nothing
in the schema said so, which means a typo in a future write path, a bulk
loader, or a restore from a hand-edited dump reaches a reader as a
tolerance basis that no protocol table row corresponds to. A reader
cannot tell that from a basis a newer TCKDB knows about and this one does
not, and the two want opposite responses.

This revision closes the vocabulary at the write side only. The read side
deliberately stays open: ``imaginary_mode_tau_basis`` remains ``str`` on
the wire, never the enum, so a value this build does not recognise is
*displayed* rather than made to refuse the whole record. ADR 0012's
argument for accepting a flagged record instead of refusing it is the
same argument, one level down.

**A CHECK, not a native enum.** Three reasons, in order of weight. The
ORM column stays ``TEXT`` (above), and a native enum type under a
``TEXT`` mapping is a mismatch waiting to surprise a writer. A CHECK,
unlike a foreign key, still holds under
``session_replication_role = replica`` -- which is what bulk loaders and
restore paths run under, and those are exactly the writers this exists
for (``tests/db/test_element_symbol_canonicality.py`` measures that
split). And extending the vocabulary later is a constraint swap in one
revision with a working ``downgrade()``, where ``ALTER TYPE ... ADD
VALUE`` cannot be undone.

**No backfill, and a refusal instead of one.** ``upgrade()`` first asks
the database what it actually holds. Every value it finds must be one of
the five, or NULL; anything else and the migration raises with the
offending values named, rather than deleting them, coercing them to
``protocol_not_recorded`` (which would be a claim about how a record was
judged, and a false one), or dropping the constraint. There is no honest
repair available at migration time for a tolerance basis nobody
recognises: only the writer that produced it knows what it meant.

Measured before choosing the vocabulary, and this is what the measurement
was: the column has existed only since ``c2f7a4e8d1b6``, which added it
nullable and backfilled nothing; ``app/services/calculation_resolution.py``
is its sole writer and writes ``tau.basis.value``; and ``TauBasis``'s five
members have never been renamed or removed since they were introduced in
the same commit. So the expected finding on every database is "five
tokens or NULL" -- and the guard is there because "expected" is not
"verified on a machine this migration has not seen".

Revision ID: e2a7c9d4b615
Revises: d4e9b1c7a253
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2a7c9d4b615"
down_revision: Union[str, Sequence[str], None] = "d4e9b1c7a253"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "calc_freq_result"
_COLUMN = "imaginary_mode_tau_basis"

#: The **short** name, as the model spells it. ``alembic/env.py`` hands
#: Alembic ``Base.metadata``, whose ``NAMING_CONVENTION`` expands this to
#: ``ck_calc_freq_result_imaginary_mode_tau_basis_known``. Passing the
#: already-expanded name here instead expands it a second time and
#: PostgreSQL truncates the result to 63 characters with a hash suffix --
#: which is what ``c2f7a4e8d1b6`` did, leaving
#: ``ck_calc_freq_mode_ck_calc_freq_mode_imaginary_dispositi_ddc2`` on
#: ``calc_freq_mode``. That constraint enforces the right rule under the
#: wrong name; this one is spelled so the database agrees with the model.
_CONSTRAINT = "imaginary_mode_tau_basis_known"

#: What the database ends up calling it, for the error messages and the
#: tests that read ``pg_constraint``.
_CONSTRAINT_IN_DB = f"ck_{_TABLE}_{_CONSTRAINT}"

#: ``tckdb_schemas.stationary_point.TauBasis``, spelled out rather than
#: imported: a migration must keep meaning what it meant when it ran, and
#: an import would let a later edit to the enum silently change what this
#: revision did. Mirrored in
#: ``app.db.models.common.IMAGINARY_MODE_TAU_BASIS_VALUES``; the two are
#: pinned together by
#: ``tests/db/test_imaginary_mode_tau_basis_constraint.py``.
_TAU_BASIS_VALUES: tuple[str, ...] = (
    "analytic_tight",
    "analytic_default",
    "finite_difference_gradient",
    "finite_difference_energy",
    "protocol_not_recorded",
)


def _condition() -> str:
    values = ", ".join(f"'{value}'" for value in _TAU_BASIS_VALUES)
    return f"{_COLUMN} IS NULL OR {_COLUMN} IN ({values})"


def _refuse_unclassifiable_values() -> None:
    """Stop rather than guess if the column holds something unexpected.

    Named in full in the error, because the next question anyone asks is
    "which values?" and a migration that answers it is a migration whose
    failure is actionable.
    """
    bind = op.get_bind()
    unknown = bind.execute(
        sa.text(
            f"SELECT DISTINCT {_COLUMN} FROM {_TABLE} "
            f"WHERE {_COLUMN} IS NOT NULL "
            f"AND {_COLUMN} <> ALL(:known) "
            f"ORDER BY {_COLUMN}"
        ),
        {"known": list(_TAU_BASIS_VALUES)},
    ).scalars().all()
    if unknown:
        raise RuntimeError(
            f"{_TABLE}.{_COLUMN} holds {len(unknown)} value(s) that are not "
            f"rows of ADR 0012's protocol table: {unknown!r}. This migration "
            "will not guess what they meant: coercing them to "
            "'protocol_not_recorded' would assert that those records were "
            "judged at the conservative tolerance, which is a claim about "
            "their science that only whatever wrote them can make. Repair "
            "the rows (or add the value to _TAU_BASIS_VALUES in this "
            "revision, if it is a legitimate basis this branch predates) and "
            "re-run."
        )


def upgrade() -> None:
    """Constrain ``imaginary_mode_tau_basis`` to ADR 0012's five bases."""
    _refuse_unclassifiable_values()
    op.create_check_constraint(_CONSTRAINT, _TABLE, _condition())


def downgrade() -> None:
    """Reopen the column to free text, as ``c2f7a4e8d1b6`` left it."""
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
