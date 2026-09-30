"""Correction-scheme data revision, atom-param sign, and stored energy level (#619)

Three additive changes from one adapter audit.

1. ``energy_correction_scheme.data_revision`` (nullable text) and a split
   identity.

   Atom-energy and BAC tables live in RMG-database, but the only build a
   producer records is the RMG-Py commit (``workflow_tool_release``). So
   every RMG-Py commit made a new scheme row, and a database-only change to
   one parameter under the same identity was refused as a value conflict.
   The owner's decision (2026-09-30): an optional data revision joins the
   identity when present, and the tool build stays provenance.

   The one unique index becomes two partial unique indexes:

   * ``uq_energy_correction_scheme_identity`` -- same name, same six
     columns, now ``WHERE data_revision IS NULL``. Every existing row has a
     NULL revision, so every existing row keeps exactly the identity it
     had, and no public ref changes.
   * ``uq_energy_correction_scheme_identity_revised`` --
     ``(kind, name, level_of_theory_id, source_literature_id,
     software_release_id, data_revision)`` ``WHERE data_revision IS NOT
     NULL``. The tool build is not in it: two builds that read the same
     revision's tables are one scheme.

   No backfill. A revision is not recoverable from a stored row: the build
   recorded on it (an RMG-Py commit) does not determine which database
   commit its tables came from. NULL therefore means "not stated" and
   stays NULL.

2. ``energy_correction_scheme.atom_params_applied_as`` (nullable enum
   ``atom_param_application``: ``subtracted`` | ``added``). Says how the
   scheme's atom parameters enter the corrected energy. NULL is "not
   stated"; it is not inferred from ``kind``. No backfill, for the same
   reason: an existing row does not say.

3. ``thermo.energy_level_of_theory_id`` and
   ``statmech.energy_level_of_theory_id`` (nullable FK to
   ``level_of_theory``, indexed). The declared ``energy_level_of_theory`` of
   an upload was checked against the linked calculations and then thrown
   away. It is now stored as declared.

   No backfill. The value a depositor declared is not recoverable: the
   level the read layer derives from the linked calculations is often the
   same, but a record with a declaration and one without look identical in
   the stored rows, and writing the derived level into rows that never
   declared one would record a claim nobody made. NULL means "nothing
   declared".

Accepted-science immutability
-----------------------------
``thermo`` and ``statmech`` are accepted-science tables whose row triggers
refuse an UPDATE of approved science. Adding a nullable column runs no
trigger, and this revision issues no UPDATE or INSERT against either
table, so nothing is refused and nothing needs a declared repair.

Merged levels of theory
-----------------------
``merge_duplicate_levels_of_theory.py`` reads foreign keys into
``level_of_theory`` at run time, so the two new columns are found without a
change to it. It repoints only ``calculation.lot_id`` and the merge table;
a thermo or statmech row that cites a duplicate therefore blocks that
group, the same rule it applies to a scheme. New uploads cannot create that
situation: ``resolve_level_of_theory_ref`` follows a merge, so a declared
level resolves to the holder. The read layer follows a merge too.

Downgrade
---------
Drops the new columns, the enum and the revised index, and recreates the
single six-column index. That index cannot hold two revised rows that
differ only in ``data_revision``, so the downgrade refuses, naming the
count, when such rows exist, rather than deleting them. Dropping the
columns forgets every stored revision, sign and declared energy level;
the downgrade prints how many of each it is about to forget.

Revision ID: f2c8a5d1e9b7
Revises: d0a7c3b91e4f
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2c8a5d1e9b7"
down_revision: Union[str, Sequence[str], None] = "d0a7c3b91e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEME = "energy_correction_scheme"
_LEGACY_INDEX = "uq_energy_correction_scheme_identity"
_REVISED_INDEX = "uq_energy_correction_scheme_identity_revised"

_LEGACY_COLUMNS = [
    "kind",
    "name",
    "level_of_theory_id",
    "source_literature_id",
    "software_release_id",
    "workflow_tool_release_id",
]
_REVISED_COLUMNS = [
    "kind",
    "name",
    "level_of_theory_id",
    "source_literature_id",
    "software_release_id",
    "data_revision",
]

_ENUM_NAME = "atom_param_application"

# Two revised rows that the single six-column index would call duplicates.
_DOWNGRADE_COLLISIONS = sa.text(
    f"""
    SELECT count(*) FROM (
        SELECT 1
        FROM {_SCHEME}
        GROUP BY kind, name, level_of_theory_id, source_literature_id,
                 software_release_id, workflow_tool_release_id
        HAVING count(*) > 1
    ) AS groups
    """
)


def upgrade() -> None:
    atom_param_application = postgresql.ENUM(
        "subtracted", "added", name=_ENUM_NAME, create_type=False
    )
    postgresql.ENUM("subtracted", "added", name=_ENUM_NAME).create(
        op.get_bind(), checkfirst=True
    )

    op.add_column(_SCHEME, sa.Column("data_revision", sa.Text(), nullable=True))
    op.add_column(
        _SCHEME,
        sa.Column("atom_params_applied_as", atom_param_application, nullable=True),
    )

    # Every existing row has data_revision NULL, so the narrowed legacy
    # index holds exactly the rows, and the keys, the old one held.
    op.drop_index(
        _LEGACY_INDEX,
        table_name=_SCHEME,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        _LEGACY_INDEX,
        _SCHEME,
        _LEGACY_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NULL"),
    )
    op.create_index(
        _REVISED_INDEX,
        _SCHEME,
        _REVISED_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("data_revision IS NOT NULL"),
    )

    for table in ("thermo", "statmech"):
        op.add_column(
            table,
            sa.Column("energy_level_of_theory_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_energy_level_of_theory_id_level_of_theory"),
            table,
            "level_of_theory",
            ["energy_level_of_theory_id"],
            ["id"],
            deferrable=True,
            initially="IMMEDIATE",
        )
        op.create_index(
            op.f(f"ix_{table}_energy_level_of_theory_id"),
            table,
            ["energy_level_of_theory_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    collisions = bind.execute(_DOWNGRADE_COLLISIONS).scalar_one()
    if collisions:
        raise RuntimeError(
            f"Cannot downgrade: {collisions} group(s) of energy_correction_"
            "scheme rows differ only in data_revision (or in nothing the "
            "older single identity index looks at). The older index would "
            "call them duplicates. Nothing has been changed. Resolve them "
            "by hand, deciding which scheme each referencing "
            "applied_energy_correction row should keep, then retry."
        )

    revised = bind.execute(
        sa.text(f"SELECT count(*) FROM {_SCHEME} WHERE data_revision IS NOT NULL")
    ).scalar_one()
    signed = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {_SCHEME} WHERE atom_params_applied_as IS NOT NULL"
        )
    ).scalar_one()
    declared = {
        table: bind.execute(
            sa.text(
                f"SELECT count(*) FROM {table} "
                "WHERE energy_level_of_theory_id IS NOT NULL"
            )
        ).scalar_one()
        for table in ("thermo", "statmech")
    }
    print(
        "f2c8a5d1e9b7 downgrade forgets: "
        f"{revised} scheme data revision(s), {signed} atom-parameter sign(s), "
        f"{declared['thermo']} thermo and {declared['statmech']} statmech "
        "declared energy level(s)."
    )

    for table in ("thermo", "statmech"):
        op.drop_index(op.f(f"ix_{table}_energy_level_of_theory_id"), table_name=table)
        op.drop_constraint(
            op.f(f"fk_{table}_energy_level_of_theory_id_level_of_theory"),
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "energy_level_of_theory_id")

    op.drop_index(_REVISED_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.drop_index(_LEGACY_INDEX, table_name=_SCHEME, postgresql_nulls_not_distinct=True)
    op.create_index(
        _LEGACY_INDEX,
        _SCHEME,
        _LEGACY_COLUMNS,
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.drop_column(_SCHEME, "atom_params_applied_as")
    op.drop_column(_SCHEME, "data_revision")
    postgresql.ENUM(name=_ENUM_NAME).drop(bind, checkfirst=True)
