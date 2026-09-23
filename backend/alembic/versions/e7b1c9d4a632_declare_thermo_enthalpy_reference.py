"""Declare the thermo enthalpy reference without assigning legacy science.

The CHECK is intentionally NOT VALID: legacy h298 rows remain null. It
still enforces new inserts and updates. No validation or data step may
infer a reference on those rows, including immutable accepted science.
Child-only enthalpy and declarations without content are workflow rules.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e7b1c9d4a632"
down_revision = "d2f4a7c1b8e6"
branch_labels = None
depends_on = None

_reference = postgresql.ENUM(
    "formation_298k", name="enthalpy_reference_kind", create_type=False
)


def upgrade() -> None:
    _reference.create(op.get_bind(), checkfirst=True)
    op.add_column("thermo", sa.Column("enthalpy_reference_kind", _reference, nullable=True))
    op.execute(
        "ALTER TABLE thermo ADD CONSTRAINT ck_thermo_h298_requires_enthalpy_reference "
        "CHECK (h298_kj_mol IS NULL OR enthalpy_reference_kind IS NOT NULL) NOT VALID"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_thermo_h298_requires_enthalpy_reference"), "thermo", type_="check")
    op.drop_column("thermo", "enthalpy_reference_kind")
    _reference.drop(op.get_bind(), checkfirst=True)
