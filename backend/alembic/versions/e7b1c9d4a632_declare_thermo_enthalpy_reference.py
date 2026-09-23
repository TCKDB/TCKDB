"""Declare the thermo enthalpy reference without assigning legacy science.

Enforced by a trigger, not a CHECK: legacy h298 rows remain null and stay
writable. The guard only fires when an INSERT or UPDATE actually touches
``h298_kj_mol`` or ``enthalpy_reference_kind`` (compared with ``IS DISTINCT
FROM`` against the pre-update row), so an update to an unrelated column on a
legacy undeclared row is unaffected -- including the public-ref backfill
(``app/services/public_refs.py``) and any other write that does not touch
either column. A CHECK ``... NOT VALID`` was tried first: Postgres still
validates a NOT VALID CHECK on every subsequent UPDATE, including one that
touches neither column, so it refused writes to rows it was meant to spare.
No validation or data step may infer a reference on legacy rows, including
immutable accepted science. Child-only enthalpy and declarations without
content are workflow rules, not this one.
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
        """
        CREATE FUNCTION public.tckdb_guard_thermo_enthalpy_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.h298_kj_mol IS NOT DISTINCT FROM OLD.h298_kj_mol
               AND NEW.enthalpy_reference_kind IS NOT DISTINCT FROM OLD.enthalpy_reference_kind
            THEN
                RETURN NEW;
            END IF;
            IF NEW.h298_kj_mol IS NOT NULL AND NEW.enthalpy_reference_kind IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'thermo.h298_kj_mol requires enthalpy_reference_kind to be declared';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guard_thermo_enthalpy_reference
        BEFORE INSERT OR UPDATE ON public.thermo
        FOR EACH ROW
        EXECUTE FUNCTION public.tckdb_guard_thermo_enthalpy_reference()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_guard_thermo_enthalpy_reference ON public.thermo")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_guard_thermo_enthalpy_reference() CASCADE")
    op.drop_column("thermo", "enthalpy_reference_kind")
    _reference.drop(op.get_bind(), checkfirst=True)
