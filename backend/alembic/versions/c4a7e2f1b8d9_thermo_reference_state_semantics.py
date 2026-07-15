"""thermo reference-state semantics

Adds reference-state and provenance columns to ``thermo`` so deposited
values are no longer under-specified (schema audit / 2026-07-02 P1 #15):

* ``reference_pressure_bar`` — standard-state pressure the H/S/NASA
  values are referenced to (fixed unit: bar; 1 bar IUPAC, 1 atm =
  1.01325 bar for legacy data). NULL = unspecified.
* ``phase`` — physical phase (new ``phase_kind`` enum: gas/liquid/
  solid/aqueous). NULL = unspecified.
* ``enthalpy_formation_0k_kj_mol`` + ``..._uncertainty_kj_mol`` —
  ΔfH°(0 K) and its uncertainty (with a ``>= 0`` CHECK mirroring the
  existing h298 uncertainty CHECK).
* ``statmech_id`` — FK to the ``statmech`` record a computed thermo was
  derived from (nullable; experimental/literature/GA thermo has none).

The ``thermo`` table has ever been a deployed table, so this is a new
additive revision rather than a baseline edit (per
``.claude/rules/migration-rules.md``). All new columns are nullable, so
no data backfill is required.

Revision ID: c4a7e2f1b8d9
Revises: b38b43af6460
Create Date: 2026-07-15 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a7e2f1b8d9"
down_revision: Union[str, Sequence[str], None] = "b38b43af6460"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


phase_kind = postgresql.ENUM(
    "gas",
    "liquid",
    "solid",
    "aqueous",
    name="phase_kind",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    phase_kind.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "thermo",
        sa.Column("enthalpy_formation_0k_kj_mol", sa.Double(), nullable=True),
    )
    op.add_column(
        "thermo",
        sa.Column(
            "enthalpy_formation_0k_uncertainty_kj_mol",
            sa.Double(),
            nullable=True,
        ),
    )
    op.add_column(
        "thermo",
        sa.Column("reference_pressure_bar", sa.Double(), nullable=True),
    )
    op.add_column(
        "thermo",
        sa.Column("phase", phase_kind, nullable=True),
    )
    op.add_column(
        "thermo",
        sa.Column("statmech_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        op.f("ix_thermo_statmech_id"),
        "thermo",
        ["statmech_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_thermo_statmech_id_statmech"),
        "thermo",
        "statmech",
        ["statmech_id"],
        ["id"],
        initially="IMMEDIATE",
        deferrable=True,
    )
    op.create_check_constraint(
        "enthalpy_formation_0k_uncertainty_ge_0",
        "thermo",
        "enthalpy_formation_0k_uncertainty_kj_mol IS NULL "
        "OR enthalpy_formation_0k_uncertainty_kj_mol >= 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Plain (convention-expanded) name on both create and drop sides, per
    # the d0f4b2c6e8a3 precedent, so the two can't drift on a future edit.
    op.drop_constraint(
        "enthalpy_formation_0k_uncertainty_ge_0",
        "thermo",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_thermo_statmech_id_statmech"),
        "thermo",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_thermo_statmech_id"), table_name="thermo")
    op.drop_column("thermo", "statmech_id")
    op.drop_column("thermo", "phase")
    op.drop_column("thermo", "reference_pressure_bar")
    op.drop_column("thermo", "enthalpy_formation_0k_uncertainty_kj_mol")
    op.drop_column("thermo", "enthalpy_formation_0k_kj_mol")

    phase_kind.drop(op.get_bind(), checkfirst=True)
