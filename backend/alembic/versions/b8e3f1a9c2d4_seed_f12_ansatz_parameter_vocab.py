"""seed f12.ansatz calculation-parameter vocab key

The Molpro parser (``app.services.molpro_parameter_parser``) extracts the F12
explicit-correlation ansatz (e.g. ``3C(FIX)``) observed in every real
CCSD(T)-F12 / cc-pVTZ-F12 job fixture. It is a genuinely new canonical
parameter with no existing home in ``calculation_parameter_vocab`` — the
``canonical_key`` column on ``calculation_parameter`` is FK-constrained against
this table, so the key must exist before parser rows can reference it.

This revision seeds the single ``f12.ansatz`` vocab row (observed-only; the
value list is not enumerated because the key is a free-form string). No other
Molpro-emitted canonical keys are new: ``scf.max_cycles`` (Molpro ``maxit``) and
``memory.raw`` (Molpro ``memory,...,m`` in mega-words) already exist.

Upgrade inserts one row; downgrade removes it. No data backfill is needed
because no ``calculation_parameter`` rows reference the key on any deployed DB
yet.

Revision ID: b8e3f1a9c2d4
Revises: c6f2a9d4e7b1
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e3f1a9c2d4"
down_revision: Union[str, Sequence[str], None] = "c6f2a9d4e7b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CANONICAL_KEY = "f12.ansatz"


def upgrade() -> None:
    """Seed the ``f12.ansatz`` canonical-parameter vocab key."""
    vocab = sa.table(
        "calculation_parameter_vocab",
        sa.column("canonical_key", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("expected_value_type", sa.Text()),
        sa.column("affects_scientific_result", sa.Boolean()),
        sa.column("affects_numerics", sa.Boolean()),
        sa.column("affects_resources", sa.Boolean()),
        sa.column("note", sa.Text()),
    )
    op.bulk_insert(
        vocab,
        [
            {
                "canonical_key": _CANONICAL_KEY,
                "description": (
                    "F12 explicit-correlation ansatz (e.g. 3C(FIX)) used by "
                    "explicitly-correlated methods such as CCSD(T)-F12."
                ),
                "expected_value_type": "string",
                "affects_scientific_result": True,
                "affects_numerics": True,
                "affects_resources": False,
                "note": (
                    "Observed in Molpro CCSD(T)-F12 / cc-pVTZ-F12 outputs. "
                    "Distinct F12 ansatze (e.g. 3C(FIX) vs 3*A) change the "
                    "explicitly-correlated energy."
                ),
            }
        ],
    )


def downgrade() -> None:
    """Remove the ``f12.ansatz`` canonical-parameter vocab key."""
    op.execute(
        sa.text(
            "DELETE FROM calculation_parameter_vocab "
            "WHERE canonical_key = :k"
        ).bindparams(k=_CANONICAL_KEY)
    )
