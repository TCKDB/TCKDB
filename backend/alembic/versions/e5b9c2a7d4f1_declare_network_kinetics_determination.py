"""Let a network solve declare its target, protocol and validation, and a fit its determination.

Additive, with no data step. One new identity table, one new enum and eight nullable columns:

``network_kinetics_determination`` (public ref prefix ``nkdet``)
    One complete determination of one channel's coefficient within one solve, which several
    fits can be representations of. Its identity is content -- the solve, the channel, a
    solve-scoped key and the declared observable -- and ``identity_hash`` is the unique digest
    of that content. **Immutable from creation:** ``trg_network_kinetics_determination_immutable``
    refuses every UPDATE, including while the row is shared by several fits. It is an
    ownership child of ``network_solve`` (guarded on ``solve_id`` by ``tckdb_guard_accepted_child``,
    with the TRUNCATE refusal), so a determination cannot be added under an accepted solve.

``network_solve`` gains, all nullable JSONB: ``target_declaration``, ``protocol_declaration``,
``validation_declaration``. Versioned objects whose shape is owned by
``tckdb_schemas.network_declarations``; the database checks only that each is an object carrying a
numeric ``version`` (the ``coalesce`` matters: an object with no ``version`` key would otherwise
make the predicate NULL, and a CHECK passes on NULL).

``network_kinetics`` gains, all nullable: ``determination_id`` (FK, indexed),
``representation_role`` (enum ``network_representation_role``: ``complete`` | ``additive_component``
| ``overlapping_contribution``) and ``representation_declaration`` (JSONB, a versioned object with a
string ``key``). The three are set together or not at all
(``ck_network_kinetics_determination_iff_role``, ``..._iff_representation``), and the declared key is
unique within a determination (partial unique expression index
``uq_network_kinetics_representation_key``).

What the database does not check
--------------------------------
That a determination's channel belongs to its solve's network, that a target declaration names the
network's own states and channels, and that a product set names this solve's determinations are
cross-table facts a CHECK cannot state. The write path enforces them and refuses with a code
(``app.services.network_declaration_resolution``).

What this revision deliberately does not do
-------------------------------------------
* **No backfill.** Existing rows keep NULL in every new column. A determination, a role and a
  declaration are attributed claims, never inferred from a model kind, a channel or a source
  calculation; "not stated" is the honest reading of every network deposited before this revision.
* **No change to the accepted-science root guards.** ``trg_as_root_network_solve`` and the child
  guard on ``network_kinetics`` refuse any UPDATE of an accepted row whichever column it touches, so
  the new columns freeze with the rest of the row; ``ADD COLUMN`` of a nullable column with no
  default fires no UPDATE trigger, so the upgrade does not touch an approved row.

Downgrade drops the triggers, the checks, the foreign key, the indexes and the columns, then the
table and the enum. It forgets every determination and declaration made after the upgrade, and
prints how many it is forgetting first.

Revision ID: e5b9c2a7d4f1
Revises: c7a2e5d9b148
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5b9c2a7d4f1"
down_revision: Union[str, Sequence[str], None] = "c7a2e5d9b148"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "network_kinetics_determination"
_ROLE = postgresql.ENUM(
    "complete",
    "additive_component",
    "overlapping_contribution",
    name="network_representation_role",
    create_type=False,
)

_FUNCTION = "tckdb_network_kinetics_determination_immutable"
_IMMUTABLE_TRIGGER = "trg_network_kinetics_determination_immutable"


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``b6c1f4a8e703``'s naming, truncated to PostgreSQL's 63 bytes."""
    return f"trg_{prefix}_{table}"[:63]




def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the direct registry to one trigger per ``(table, record_type)``."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]
_FK = "fk_network_kinetics_determination_ref"
_INDEX = "ix_network_kinetics_determination_id"
_REPRESENTATION_INDEX = "uq_network_kinetics_representation_key"

#: ``(table, record_type, column)`` -- guarded by ``tckdb_guard_accepted_child`` against the accepted
#: root named directly on the row. Read by ``tests/db/test_accepted_science_trigger_registry.py``.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, "network_solve", "solve_id"),)

#: The table does not reach its root through a parent row.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = ()

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = (_TABLE,)

_SOLVE_CHECKS = {
    "target_declaration": "ck_network_solve_target_declaration_versioned_object",
    "protocol_declaration": "ck_network_solve_protocol_declaration_versioned_object",
    "validation_declaration": "ck_network_solve_validation_declaration_versioned_object",
}
_KINETICS_CHECKS = (
    "ck_network_kinetics_determination_iff_role",
    "ck_network_kinetics_determination_iff_representation",
    "ck_network_kinetics_representation_declaration_versioned_object",
)

_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'network_kinetics_determination_is_immutable: determination % cannot be changed. A '
        'determination is identity: it is shared by every fit that states it, so a '
        'change would silently re-describe all of them.', OLD.id
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$
"""


def upgrade() -> None:
    bind = op.get_bind()
    _ROLE.create(bind, checkfirst=True)

    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("solve_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("determination_key", sa.Text(), nullable=False),
        sa.Column("observable_declaration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "identity_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_network_kinetics_determination_identity_hash_sha256_hex"),
        ),
        sa.CheckConstraint(
            "length(btrim(determination_key)) > 0 AND length(determination_key) <= 128",
            name=op.f("ck_network_kinetics_determination_key_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(observable_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(observable_declaration -> 'version'), '') = 'number'",
            name=op.f("ck_network_kinetics_determination_observable_versioned_object"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["network_channel.id"],
            name=op.f("fk_network_kinetics_determination_channel_id_network_channel"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_network_kinetics_determination_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["solve_id"],
            ["network_solve.id"],
            name=op.f("fk_network_kinetics_determination_solve_id_network_solve"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.UniqueConstraint("identity_hash", name=op.f(f"uq_{_TABLE}_identity_hash")),
        sa.UniqueConstraint("solve_id", "determination_key", name="uq_network_kinetics_determination_key"),
    )
    op.create_index(op.f(f"ix_{_TABLE}_public_ref"), _TABLE, ["public_ref"], unique=True)
    op.create_index(op.f(f"ix_{_TABLE}_solve_id"), _TABLE, ["solve_id"], unique=False)
    op.execute(_FUNCTION_SQL)
    op.execute(
        f"CREATE TRIGGER {_IMMUTABLE_TRIGGER} BEFORE UPDATE ON public.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_FUNCTION}()"
    )
    for table, record_type, columns in _child_groups():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_child", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child('{record_type}', {arguments})
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

    for column in _SOLVE_CHECKS:
        op.add_column(
            "network_solve",
            sa.Column(column, postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        )
    for column, name in _SOLVE_CHECKS.items():
        op.create_check_constraint(
            op.f(name),
            "network_solve",
            f"{column} IS NULL OR (jsonb_typeof({column}) = 'object' "
            f"AND coalesce(jsonb_typeof({column} -> 'version'), '') = 'number')",
        )

    op.add_column("network_kinetics", sa.Column("determination_id", sa.BigInteger(), nullable=True))
    op.add_column("network_kinetics", sa.Column("representation_role", _ROLE, nullable=True))
    op.add_column(
        "network_kinetics",
        sa.Column(
            "representation_declaration",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(op.f(_INDEX), "network_kinetics", ["determination_id"], unique=False)
    op.create_foreign_key(
        op.f(_FK),
        "network_kinetics",
        _TABLE,
        ["determination_id"],
        ["id"],
        initially="IMMEDIATE",
        deferrable=True,
    )
    op.create_check_constraint(
        op.f(_KINETICS_CHECKS[0]), "network_kinetics", "(determination_id IS NULL) = (representation_role IS NULL)"
    )
    op.create_check_constraint(
        op.f(_KINETICS_CHECKS[1]),
        "network_kinetics",
        "(determination_id IS NULL) = (representation_declaration IS NULL)",
    )
    op.create_check_constraint(
        op.f(_KINETICS_CHECKS[2]),
        "network_kinetics",
        "representation_declaration IS NULL OR (jsonb_typeof(representation_declaration) = 'object' "
        "AND coalesce(jsonb_typeof(representation_declaration -> 'version'), '') = 'number' "
        "AND coalesce(jsonb_typeof(representation_declaration -> 'key'), '') = 'string')",
    )
    op.execute(
        f"CREATE UNIQUE INDEX {_REPRESENTATION_INDEX} ON public.network_kinetics "
        "(determination_id, (representation_declaration ->> 'key')) WHERE determination_id IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    determinations = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    solves = bind.execute(
        sa.text(
            "SELECT count(*) FROM network_solve WHERE target_declaration IS NOT NULL "
            "OR protocol_declaration IS NOT NULL OR validation_declaration IS NOT NULL"
        )
    ).scalar_one()
    fits = bind.execute(sa.text("SELECT count(*) FROM network_kinetics WHERE determination_id IS NOT NULL")).scalar_one()
    print(
        f"e5b9c2a7d4f1 downgrade forgets: {determinations} network kinetics determination(s), the "
        f"declarations on {solves} network solve(s) and the grouping of {fits} network kinetics fit(s)."
    )

    op.execute(f"DROP INDEX IF EXISTS public.{_REPRESENTATION_INDEX}")
    for name in reversed(_KINETICS_CHECKS):
        op.drop_constraint(op.f(name), "network_kinetics", type_="check")
    op.drop_constraint(op.f(_FK), "network_kinetics", type_="foreignkey")
    op.drop_index(op.f(_INDEX), table_name="network_kinetics")
    op.drop_column("network_kinetics", "representation_declaration")
    op.drop_column("network_kinetics", "representation_role")
    op.drop_column("network_kinetics", "determination_id")

    for column, name in reversed(list(_SOLVE_CHECKS.items())):
        op.drop_constraint(op.f(name), "network_solve", type_="check")
        op.drop_column("network_solve", column)

    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    op.execute(f"DROP TRIGGER IF EXISTS {_IMMUTABLE_TRIGGER} ON public.{_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}()")
    op.drop_index(op.f(f"ix_{_TABLE}_solve_id"), table_name=_TABLE)
    op.drop_index(op.f(f"ix_{_TABLE}_public_ref"), table_name=_TABLE)
    op.drop_table(_TABLE)
    _ROLE.drop(bind, checkfirst=True)
