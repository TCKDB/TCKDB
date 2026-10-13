"""add the exponential_sqrt extrapolation formula

ORCA's automatic SCF basis-set extrapolation, ``E_SCF(X) = E_CBS + A exp(-alpha sqrt(X))``, is none of
the four formulas a composite scheme term could name. This adds it as a fifth member of
``composite_extrapolation_formula``. The term's existing ``exponent`` column carries alpha, so alpha is
part of the scheme's identity exactly as the exponent of ``inverse_power`` is: no column, constraint or
table changes, and no data step.

Revision ID: a7d2c9e5b413
Revises: a7c3e9d1f5b2
Create Date: 2026-10-11
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d2c9e5b413"
down_revision: Union[str, Sequence[str], None] = "a7c3e9d1f5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPE = "composite_extrapolation_formula"
_VALUE = "exponential_sqrt"
_REMAINING = ("inverse_power", "inverse_power_shifted_half", "karton_martin_scf", "exponential_three_point")


def upgrade() -> None:
    """Add the member. The new value is not used inside this transaction."""
    op.execute(f"ALTER TYPE {_TYPE} ADD VALUE IF NOT EXISTS '{_VALUE}'")


def downgrade() -> None:
    """Remove the member, refusing (and deleting nothing) while a scheme term uses it."""
    op.execute(
        f"""
        DO $$
        DECLARE n bigint;
        BEGIN
            SELECT count(*) INTO n FROM composite_scheme_term WHERE formula = '{_VALUE}';
            IF n > 0 THEN
                RAISE EXCEPTION
                    'Cannot downgrade: % composite_scheme_term row(s) use formula {_VALUE}', n;
            END IF;
        END;
        $$
        """
    )
    op.execute(f"ALTER TYPE {_TYPE} RENAME TO {_TYPE}_with_{_VALUE}")
    members = ", ".join(f"'{m}'" for m in _REMAINING)
    op.execute(f"CREATE TYPE {_TYPE} AS ENUM ({members})")
    op.execute(
        f"ALTER TABLE composite_scheme_term ALTER COLUMN formula TYPE {_TYPE} USING formula::text::{_TYPE}"
    )
    op.execute(f"DROP TYPE {_TYPE}_with_{_VALUE}")
