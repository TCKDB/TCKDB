"""state the energy convention of an ensemble member's relative energy

``statmech_model_member.relative_energy_kj_mol`` said nothing about what was folded into it (electronic only, zero-point
corrected, ...), so two ensembles built from the same members could differ by that alone and a reader could not tell
which one they had. This revision adds ``relative_energy_convention``, in the existing ``energy_correction_convention``
vocabulary that network state energies use (no new enum), and two CHECKs:

* the convention is present if and only if the energy is;
* ``other`` is not allowed. The network energy tables pair ``other`` with a ``convention_note``; a member has no note
  column, and a free-text note is not a convention, so ``other`` is refused here rather than given a column.

Nothing is inferred and nothing is backfilled: a stored member that already carries an energy has no convention to
give it, so the upgrade refuses to run (nothing committed) rather than guess. No row is updated, so the table's
immutability trigger is not involved.

Revision ID: f3a8c5d1e947
Revises: a4c8e1f7b392
Create Date: 2026-10-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3a8c5d1e947"
down_revision: Union[str, Sequence[str], None] = "a4c8e1f7b392"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "statmech_model_member"
_COLUMN = "relative_energy_convention"
_WITH_ENERGY = "ck_statmech_model_member_relative_energy_convention_with_energy"
_NOT_OTHER = "ck_statmech_model_member_relative_energy_convention_not_other"


def upgrade() -> None:
    stored = op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {_TABLE} WHERE relative_energy_kj_mol IS NOT NULL"))
    if stored:
        refs = op.get_bind().scalars(
            sa.text(
                "SELECT DISTINCT s.public_ref FROM statmech_model_member m "
                "JOIN statmech_model_declaration d ON d.id = m.declaration_id "
                "JOIN statmech s ON s.id = d.statmech_id "
                "WHERE m.relative_energy_kj_mol IS NOT NULL ORDER BY s.public_ref LIMIT 20"
            )
        ).all()
        raise RuntimeError(
            f"{stored} stored ensemble member(s) carry a relative energy with no stated convention. None is assumed "
            "and nothing is backfilled: supersede those statmech records with a declaration that states the "
            "convention, or remove them, then upgrade again. Declaring statmech records (first 20): "
            + ", ".join(refs)
        )
    convention = postgresql.ENUM(name="energy_correction_convention", create_type=False)
    op.add_column(_TABLE, sa.Column(_COLUMN, convention, nullable=True))
    op.create_check_constraint(
        op.f(_WITH_ENERGY), _TABLE, "(relative_energy_kj_mol IS NULL) = (relative_energy_convention IS NULL)"
    )
    op.create_check_constraint(op.f(_NOT_OTHER), _TABLE, "relative_energy_convention IS DISTINCT FROM 'other'")


def downgrade() -> None:
    op.drop_constraint(op.f(_NOT_OTHER), _TABLE, type_="check")
    op.drop_constraint(op.f(_WITH_ENERGY), _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
