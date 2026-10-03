"""Let a thermo record declare its thermodynamic target and its protocol.

Three nullable columns on ``thermo``, nothing else, and no data step:

* ``thermodynamic_target_kind`` (enum ``thermo_target_kind``:
  ``equilibrium_ensemble`` | ``single_conformer``) -- what the record's values
  are claimed to describe.
* ``target_conformer_group_id`` (nullable FK to ``conformer_group``, indexed) --
  the one group a ``single_conformer`` target names.
* ``protocol_declaration`` (JSONB) -- a versioned, schema-validated declaration
  of how the values were produced (recipe, formation-reference construction,
  thermal approximation, departures from the standard recipe, supporting
  calculations). Its shape is owned by
  ``tckdb_schemas.thermo_declarations.StoredThermoProtocolDeclaration``; the
  database checks only that it is an object carrying a numeric ``version``.

Two CHECKs, both satisfied by every existing row (all three columns are NULL):

* ``ck_thermo_target_group_iff_single_conformer`` -- a group is named exactly
  when the kind is ``single_conformer``. Written with ``IS NOT DISTINCT FROM``
  so that a group on a row with no kind is refused too (a plain ``= `` would
  evaluate to NULL there, and a CHECK passes on NULL).
* ``ck_thermo_protocol_declaration_versioned_object`` -- an object with a numeric
  ``version``. The ``coalesce`` matters: an object with no ``version`` key makes
  ``jsonb_typeof(... -> 'version')`` NULL, the whole predicate NULL, and a CHECK
  passes on NULL.

That the named group belongs to the *same species entry* as the thermo row is a
cross-table fact a CHECK cannot state; the services that write the column
enforce it (``app.services.thermo_declaration_resolution``) and refuse with a
code. Making it a trigger or a composite foreign key would mean altering
``conformer_group``, a deployed identity table, for a rule the write path
already owns.

What this revision deliberately does not do
-------------------------------------------
* **No backfill.** Existing rows keep NULL. A target is an attributed claim and
  is never inferred from a statmech link or a conformer selection; "not stated"
  is the honest reading of every row deposited before this revision.
* **No change to the accepted-science guards.** ``trg_as_root_thermo`` (from
  ``c6f2a9d4e7b1``) refuses any UPDATE of an accepted thermo row, whichever
  column it touches, so the three new columns are frozen with the rest of the
  row the moment it is accepted. An approved declaration is corrected only by
  supersession, like any other scientific content. ``ADD COLUMN`` of a nullable
  column with no default fires no UPDATE trigger, so the upgrade itself does
  not touch an approved row.
* **No new accepted-science repair declaration.** None of the three columns is
  in any declared repair's column set, so no repair can change one.

Downgrade drops the CHECKs, the index, the foreign key and the columns, then the
enum. It forgets every declaration made after the upgrade; it prints how many it
is forgetting first.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3d8f1a6c924"
down_revision: Union[str, Sequence[str], None] = "c4b8e2f6a713"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TARGET_KIND = postgresql.ENUM(
    "equilibrium_ensemble",
    "single_conformer",
    name="thermo_target_kind",
    create_type=False,
)
_FK = "fk_thermo_target_conformer_group_id_conformer_group"
_INDEX = "ix_thermo_target_conformer_group_id"
_CHECK_GROUP = "ck_thermo_target_group_iff_single_conformer"
_CHECK_PROTOCOL = "ck_thermo_protocol_declaration_versioned_object"


def upgrade() -> None:
    _TARGET_KIND.create(op.get_bind(), checkfirst=True)
    op.add_column("thermo", sa.Column("thermodynamic_target_kind", _TARGET_KIND, nullable=True))
    op.add_column("thermo", sa.Column("target_conformer_group_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "thermo",
        sa.Column("protocol_declaration", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_foreign_key(
        op.f(_FK),
        "thermo",
        "conformer_group",
        ["target_conformer_group_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.create_index(op.f(_INDEX), "thermo", ["target_conformer_group_id"], unique=False)
    op.create_check_constraint(
        op.f(_CHECK_GROUP),
        "thermo",
        "(thermodynamic_target_kind IS NOT DISTINCT FROM 'single_conformer') "
        "= (target_conformer_group_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f(_CHECK_PROTOCOL),
        "thermo",
        "protocol_declaration IS NULL OR ("
        "jsonb_typeof(protocol_declaration) = 'object' "
        "AND coalesce(jsonb_typeof(protocol_declaration -> 'version'), '') = 'number')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    forgotten = bind.execute(
        sa.text(
            "SELECT count(*) FROM thermo WHERE thermodynamic_target_kind IS NOT NULL "
            "OR protocol_declaration IS NOT NULL"
        )
    ).scalar_one()
    print(f"b3d8f1a6c924 downgrade forgets: {forgotten} thermo target/protocol declaration(s).")

    op.drop_constraint(op.f(_CHECK_PROTOCOL), "thermo", type_="check")
    op.drop_constraint(op.f(_CHECK_GROUP), "thermo", type_="check")
    op.drop_index(op.f(_INDEX), table_name="thermo")
    op.drop_constraint(op.f(_FK), "thermo", type_="foreignkey")
    op.drop_column("thermo", "protocol_declaration")
    op.drop_column("thermo", "target_conformer_group_id")
    op.drop_column("thermo", "thermodynamic_target_kind")
    _TARGET_KIND.drop(bind, checkfirst=True)
