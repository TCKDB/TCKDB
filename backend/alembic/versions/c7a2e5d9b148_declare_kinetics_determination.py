"""Let a kinetics record declare its determination, applicability and protocol.

Additive, with no data step. One new identity table and four nullable columns:

``kinetics_determination`` (public ref prefix ``kdet``)
    One complete determination of a rate, which several kinetics records can be
    representations of. Its identity is content -- the reaction entry, the direction, the
    declared target (the whole reaction, or one channel named by a transition-state entry or
    a network channel), the source attribution (literature, workflow-tool release) and a
    source-scoped key -- and ``identity_hash`` is the unique digest of that content, so the
    same content resolves to one row. **Immutable from creation:** the trigger
    ``trg_kinetics_determination_immutable`` refuses every UPDATE, including while the row
    is shared by several records.

``kinetics`` gains, all nullable:

* ``determination_id`` (FK, indexed) and ``representation_role`` (enum
  ``kinetics_representation_role``: ``complete`` | ``additive_component``), set together or
  not at all (``ck_kinetics_determination_iff_role``).
* ``applicability_declaration`` and ``protocol_declaration`` (JSONB), versioned objects whose
  shape is owned by ``tckdb_schemas.kinetics_declarations``; the database checks only that
  each is an object carrying a numeric ``version`` (the ``coalesce`` matters: an object with
  no ``version`` key would otherwise make the predicate NULL, and a CHECK passes on NULL).

What the database does not check
--------------------------------
That a record's determination is of the record's own reaction entry and direction, that a
target transition state belongs to that reaction, and that a protocol's supporting
calculations belong to its participants are cross-table facts a CHECK cannot state. The
services that write the columns enforce them and refuse with a code
(``app.services.kinetics_declaration_resolution``); a trigger on ``reaction_entry``,
``transition_state_entry`` or ``calculation`` -- deployed identity tables -- for a rule the
write path already owns would be the wrong place.

What this revision deliberately does not do
-------------------------------------------
* **No backfill.** Existing rows keep NULL in every new column. A determination, a role, an
  applicability claim and a protocol are attributed claims and are never inferred from a
  model kind, a direction, a pressure context or a source-calculation link; "not stated" is
  the honest reading of every record deposited before this revision.
* **No change to the accepted-science guards.** ``trg_as_root_kinetics`` (from
  ``c6f2a9d4e7b1``) refuses any UPDATE of an accepted kinetics row, whichever column it
  touches, so the four new columns are frozen with the rest of the row the moment it is
  accepted; ``ADD COLUMN`` of a nullable column with no default fires no UPDATE trigger, so
  the upgrade does not touch an approved row. A determination is immutable on its own.
* **No new accepted-science repair declaration.** None of the new columns is in any declared
  repair's column set.

Downgrade drops the trigger, the checks, the foreign key, the index and the columns, then the
table and the two new enums. It forgets every determination and declaration made after the
upgrade, and prints how many it is forgetting first.

Revision ID: c7a2e5d9b148
Revises: d7a1c4e9b258
Create Date: 2026-10-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7a2e5d9b148"
down_revision: Union[str, Sequence[str], None] = "d7a1c4e9b258"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TARGET_KIND = postgresql.ENUM(
    "whole_reaction",
    "resolved_channel",
    name="kinetics_determination_target_kind",
    create_type=False,
)
_ROLE = postgresql.ENUM(
    "complete",
    "additive_component",
    name="kinetics_representation_role",
    create_type=False,
)
_DIRECTION = postgresql.ENUM(
    "forward", "reverse", "net", name="kinetics_direction", create_type=False
)

_FUNCTION = "tckdb_kinetics_determination_immutable"
_TRIGGER = "trg_kinetics_determination_immutable"
_FK = "fk_kinetics_determination_id_kinetics_determination"
_INDEX = "ix_kinetics_determination_id"
_CHECK_ROLE = "ck_kinetics_determination_iff_role"
_CHECK_APPLICABILITY = "ck_kinetics_applicability_declaration_versioned_object"
_CHECK_PROTOCOL = "ck_kinetics_protocol_declaration_versioned_object"

_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'kinetics_determination_is_immutable: determination % cannot be changed. A '
        'determination is identity: it is shared by every record that states it, so a '
        'change would silently re-describe all of them.', OLD.id
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$
"""


def upgrade() -> None:
    bind = op.get_bind()
    _TARGET_KIND.create(bind, checkfirst=True)
    _ROLE.create(bind, checkfirst=True)

    op.create_table(
        "kinetics_determination",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("reaction_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", _DIRECTION, nullable=False),
        sa.Column("target_kind", _TARGET_KIND, nullable=False),
        sa.Column("target_transition_state_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("target_network_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("literature_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_tool_release_id", sa.BigInteger(), nullable=True),
        sa.Column("determination_key", sa.Text(), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(target_kind = 'whole_reaction' AND target_transition_state_entry_id IS NULL "
            "AND target_network_channel_id IS NULL) OR (target_kind = 'resolved_channel' "
            "AND num_nonnulls(target_transition_state_entry_id, target_network_channel_id) = 1)",
            name=op.f("ck_kinetics_determination_target_matches_kind"),
        ),
        sa.CheckConstraint(
            "identity_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_kinetics_determination_identity_hash_sha256_hex"),
        ),
        sa.CheckConstraint(
            "length(btrim(determination_key)) > 0 AND length(determination_key) <= 128",
            name=op.f("ck_kinetics_determination_key_bounded"),
        ),
        sa.CheckConstraint(
            "literature_id IS NOT NULL OR workflow_tool_release_id IS NOT NULL",
            name=op.f("ck_kinetics_determination_source_required"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_kinetics_determination_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name=op.f("fk_kinetics_determination_literature_id_literature"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["reaction_entry_id"],
            ["reaction_entry.id"],
            name=op.f("fk_kinetics_determination_reaction_entry_id_reaction_entry"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["target_network_channel_id"],
            ["network_channel.id"],
            name="fk_kinetics_determination_target_network_channel",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["target_transition_state_entry_id"],
            ["transition_state_entry.id"],
            name="fk_kinetics_determination_target_ts_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_tool_release_id"],
            ["workflow_tool_release.id"],
            name="fk_kinetics_determination_workflow_tool_release",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kinetics_determination")),
        sa.UniqueConstraint("identity_hash", name=op.f("uq_kinetics_determination_identity_hash")),
    )
    op.create_index(
        op.f("ix_kinetics_determination_public_ref"),
        "kinetics_determination",
        ["public_ref"],
        unique=True,
    )
    op.create_index(
        op.f("ix_kinetics_determination_reaction_entry_id"),
        "kinetics_determination",
        ["reaction_entry_id"],
        unique=False,
    )
    op.execute(_FUNCTION_SQL)
    op.execute(
        f"CREATE TRIGGER {_TRIGGER} BEFORE UPDATE ON public.kinetics_determination "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_FUNCTION}()"
    )

    op.add_column("kinetics", sa.Column("determination_id", sa.BigInteger(), nullable=True))
    op.add_column("kinetics", sa.Column("representation_role", _ROLE, nullable=True))
    op.add_column(
        "kinetics",
        sa.Column(
            "applicability_declaration",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "kinetics",
        sa.Column(
            "protocol_declaration",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(op.f(_INDEX), "kinetics", ["determination_id"], unique=False)
    op.create_foreign_key(
        op.f(_FK),
        "kinetics",
        "kinetics_determination",
        ["determination_id"],
        ["id"],
        initially="IMMEDIATE",
        deferrable=True,
    )
    op.create_check_constraint(
        op.f(_CHECK_ROLE),
        "kinetics",
        "(determination_id IS NULL) = (representation_role IS NULL)",
    )
    op.create_check_constraint(
        op.f(_CHECK_APPLICABILITY),
        "kinetics",
        "applicability_declaration IS NULL OR ("
        "jsonb_typeof(applicability_declaration) = 'object' "
        "AND coalesce(jsonb_typeof(applicability_declaration -> 'version'), '') = 'number')",
    )
    op.create_check_constraint(
        op.f(_CHECK_PROTOCOL),
        "kinetics",
        "protocol_declaration IS NULL OR ("
        "jsonb_typeof(protocol_declaration) = 'object' "
        "AND coalesce(jsonb_typeof(protocol_declaration -> 'version'), '') = 'number')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    determinations = bind.execute(sa.text("SELECT count(*) FROM kinetics_determination")).scalar_one()
    declared = bind.execute(
        sa.text(
            "SELECT count(*) FROM kinetics WHERE determination_id IS NOT NULL "
            "OR applicability_declaration IS NOT NULL OR protocol_declaration IS NOT NULL"
        )
    ).scalar_one()
    print(
        f"c7a2e5d9b148 downgrade forgets: {determinations} kinetics determination(s) and the "
        f"declarations on {declared} kinetics record(s)."
    )

    op.drop_constraint(op.f(_CHECK_PROTOCOL), "kinetics", type_="check")
    op.drop_constraint(op.f(_CHECK_APPLICABILITY), "kinetics", type_="check")
    op.drop_constraint(op.f(_CHECK_ROLE), "kinetics", type_="check")
    op.drop_constraint(op.f(_FK), "kinetics", type_="foreignkey")
    op.drop_index(op.f(_INDEX), table_name="kinetics")
    op.drop_column("kinetics", "protocol_declaration")
    op.drop_column("kinetics", "applicability_declaration")
    op.drop_column("kinetics", "representation_role")
    op.drop_column("kinetics", "determination_id")

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON public.kinetics_determination")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}()")
    op.drop_index(op.f("ix_kinetics_determination_reaction_entry_id"), table_name="kinetics_determination")
    op.drop_index(op.f("ix_kinetics_determination_public_ref"), table_name="kinetics_determination")
    op.drop_table("kinetics_determination")
    _ROLE.drop(bind, checkfirst=True)
    _TARGET_KIND.drop(bind, checkfirst=True)
