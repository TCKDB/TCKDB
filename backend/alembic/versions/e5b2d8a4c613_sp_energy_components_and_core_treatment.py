"""single-point energy components and level-of-theory core treatment (ADR 0021, P4)

Two additions, both for composite levels of theory (docs/adr/0021).

``calc_sp_energy_component``
    A child of a single-point calculation holding the parts of its electronic
    energy: the reference (SCF) energy, the correlation energy, a triples part,
    a correction. A CCSD(T)/CBS extrapolation extrapolates the correlation part
    and takes the reference part from the larger basis, so those parts have to
    be stored for a later phase to check an assembled total against them.
    Primary key ``(calculation_id, component)``: one value per component. The
    ``component`` enum is the existing ``energy_component_kind`` (created by
    ``d7a3f1b9c284``), reused as is. The value is whatever the depositor sent;
    TCKDB never stores a sum it formed itself.

    The table is an ownership child of ``calculation``, guarded like
    ``calc_sp_result``: once an accepted calculation exists, its energy parts
    can be neither added nor changed. TRUNCATE is refused on the same terms.

``level_of_theory.core_treatment``
    A nullable enum, ``frozen_core`` or ``all_electron``. It is part of the
    level's identity **only when stated**: the application hash adds the key
    when the column is set and adds nothing when it is NULL. So this revision
    re-keys no row, and no existing ``lot_hash`` changes. Existing rows are
    NULL. The column is listed in ``snapshot_defaults.UNCHANGED_DEFAULTS`` so a
    whole-row digest of a level does not go stale for a NULL.

What this revision writes
-------------------------
* DDL: one enum type, one column, one table, two triggers (``trg_as_child_calc_sp_energy_component``
  and ``trg_as_truncate_calc_sp_energy_component``).
* No data. No ``lot_hash`` is touched.

The frozen alias revisions (``d0a7c3b91e4f`` .. ``b9e4c2a7d153``) hash a level
from the columns that existed when they ran and know nothing of this column.
That is correct: they run below this revision, where every level has a NULL
core treatment, and ``downgrade()`` here refuses while any level states one, so
they can never see a stated value.

Downgrade
---------
Refuses while any ``level_of_theory.core_treatment`` is set or any
``calc_sp_energy_component`` row exists. Both would be silent loss, and each
refusal says how to proceed:

* Components are deposited results, and rows under an accepted calculation
  cannot be deleted while the freeze trigger stands. The operator drops
  ``trg_as_child_calc_sp_energy_component`` for the duration (or deletes the
  rows of unaccepted calculations), then downgrades.
* A stated core treatment is part of the level's hash. Setting it back to NULL
  leaves a ``lot_hash`` that includes it, so the row would be stale under the
  older formula. The operator re-keys or merges those levels (the merge script
  regroups by the recomputed hash) before, or in the same step as, clearing it.

With neither present, downgrade restores the prior schema exactly and upgrade
then downgrade then upgrade is a no-op on data.

Revision ID: e5b2d8a4c613
Revises: d7a3f1b9c284
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5b2d8a4c613"
down_revision: Union[str, Sequence[str], None] = "d7a3f1b9c284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "calc_sp_energy_component"

#: ``(table, record_type, column)`` -- the ownership foreign key, guarded by
#: ``tckdb_guard_accepted_child`` like ``calc_sp_result`` under
#: ``c6f2a9d4e7b1``. Read by ``tests/db/test_accepted_science_trigger_registry.py``.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, "calculation", "calculation_id"),)

#: No table here reaches its root through a parent row.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = ()

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = (_TABLE,)

_CORE_TREATMENT = postgresql.ENUM("frozen_core", "all_electron", name="core_treatment", create_type=False)
_COMPONENT = postgresql.ENUM(
    "total",
    "reference",
    "correlation",
    "triples",
    "dboc",
    "scalar_relativistic",
    name="energy_component_kind",
    create_type=False,
)


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``a1f6c3e9b527``'s naming, truncated to PostgreSQL's 63 bytes."""

    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the direct registry to one trigger per ``(table, record_type)``."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def upgrade() -> None:
    # -- 1. level_of_theory.core_treatment -----------------------------------
    _CORE_TREATMENT.create(op.get_bind(), checkfirst=True)
    op.add_column("level_of_theory", sa.Column("core_treatment", _CORE_TREATMENT, nullable=True))

    # -- 2. calc_sp_energy_component -----------------------------------------
    op.create_table(
        _TABLE,
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("component", _COMPONENT, nullable=False),
        sa.Column("value_hartree", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "value_hartree > '-Infinity'::float8 AND value_hartree < 'Infinity'::float8",
            name=op.f("ck_calc_sp_energy_component_value_hartree_finite"),
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name=op.f("fk_calc_sp_energy_component_calculation_id_calculation"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("calculation_id", "component", name=op.f("pk_calc_sp_energy_component")),
    )

    for table, record_type, columns in _child_groups():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_child", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child(
                '{record_type}', {arguments}
            )
            """
        )
    for table in _TRUNCATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_truncate", table)}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def _refuse_if_any(description: str, query: str, how_to_proceed: str) -> None:
    count = op.get_bind().execute(sa.text(query)).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} {description}. The previous schema has no "
            f"place for them, and deleting them to make the downgrade succeed would "
            f"be a silent loss. {how_to_proceed}"
        )


def downgrade() -> None:
    _refuse_if_any(
        "level_of_theory row(s) with a stated core_treatment",
        "SELECT count(*) FROM level_of_theory WHERE core_treatment IS NOT NULL",
        "A stated core treatment is part of the level's lot_hash, so setting it back to NULL "
        "leaves a stale hash: re-key those levels, or merge them into the level they duplicate "
        "(scripts/ops/merge_duplicate_levels_of_theory.py regroups by the recomputed hash), "
        "then clear the column and downgrade.",
    )
    _refuse_if_any(
        f"{_TABLE} row(s)",
        f"SELECT count(*) FROM {_TABLE}",
        "Rows under an accepted calculation cannot be deleted while the freeze trigger "
        "trg_as_child_calc_sp_energy_component stands: export them, drop that trigger "
        "for the duration of the delete (and recreate it if you stop short of the downgrade), "
        "then downgrade. Rows of unaccepted calculations can simply be deleted.",
    )

    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.drop_table(_TABLE)

    op.drop_column("level_of_theory", "core_treatment")
    _CORE_TREATMENT.drop(op.get_bind(), checkfirst=True)
