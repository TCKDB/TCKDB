"""Derive network_channel.kind from its states, and drop the unused 'stabilization'.

A channel's kind is a function of the two states it joins (``tckdb_schemas.network_channel_kind``):

* well -> well: ``isomerization``
* bimolecular/termolecular -> well: ``association``
* well -> bimolecular/termolecular: ``dissociation``
* bimolecular/termolecular -> bimolecular/termolecular: ``exchange``

This revision

1. refuses to run if any channel joins a state whose kind it cannot classify (the list is in the
   error), so nothing is ever labelled by guess;
2. recomputes ``network_channel.kind`` for every channel whose stored kind differs from the derived
   one -- including any ``stabilization`` row, which maps to whatever its states say -- and prints each
   change as (network ref / channel key, old kind, new kind), or ``0 changed``;
3. rebuilds the ``network_channel_kind`` PostgreSQL enum without ``stabilization`` (Postgres cannot
   drop an enum value, so the type is recreated and the column re-cast).

Step 2 is a declared accepted-science repair on the single column ``kind`` (``e2c9a4f7b163``), so
an instance where a network has been approved gets a recorded repair rather than a failed
migration. Only ``kind`` can change under that declaration.

``downgrade()`` restores the enum with ``stabilization`` as a member. It does not rewrite data:
every derived kind is still a member of the old type, and which rows were relabelled is not
something the old schema can ask to remember.

Revision ID: a7c4e1f9b268
Revises: c7e2a9f4d138
"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "a7c4e1f9b268"
down_revision: Union[str, Sequence[str], None] = "c7e2a9f4d138"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUES = ("isomerization", "association", "dissociation", "exchange")
_OLD_VALUES = ("isomerization", "association", "dissociation", "stabilization", "exchange")

_DERIVED = """
    CASE
        WHEN src.kind = 'well' AND snk.kind = 'well' THEN 'isomerization'
        WHEN src.kind = 'well' THEN 'dissociation'
        WHEN snk.kind = 'well' THEN 'association'
        ELSE 'exchange'
    END
"""

_REASON = (
    "a7c4e1f9b268: network_channel.kind is derived from the kinds of the channel's source and sink "
    "states. Recompute kind for every channel whose stored kind differs; no other column is touched."
)


def _rebuild_enum(values: tuple[str, ...]) -> None:
    bind = op.get_bind()
    quoted = ", ".join(f"'{v}'" for v in values)
    bind.execute(text("ALTER TYPE network_channel_kind RENAME TO network_channel_kind_old"))
    bind.execute(text(f"CREATE TYPE network_channel_kind AS ENUM ({quoted})"))
    bind.execute(
        text(
            "ALTER TABLE network_channel ALTER COLUMN kind TYPE network_channel_kind "
            "USING kind::text::network_channel_kind"
        )
    )
    bind.execute(text("DROP TYPE network_channel_kind_old"))


def upgrade() -> None:
    bind = op.get_bind()

    unclassifiable = bind.execute(
        text(
            """
            SELECT c.channel_key, n.public_ref AS network_ref,
                   src.kind::text AS source_kind, snk.kind::text AS sink_kind
              FROM network_channel c
              JOIN network n ON n.id = c.network_id
              JOIN network_state src ON src.id = c.source_state_id
              JOIN network_state snk ON snk.id = c.sink_state_id
             WHERE src.kind::text NOT IN ('well', 'bimolecular', 'termolecular')
                OR snk.kind::text NOT IN ('well', 'bimolecular', 'termolecular')
             ORDER BY c.id
            """
        )
    ).all()
    if unclassifiable:
        listing = "; ".join(f"{r.network_ref}/{r.channel_key} ({r.source_kind} -> {r.sink_kind})" for r in unclassifiable)
        raise RuntimeError(
            f"{len(unclassifiable)} network_channel row(s) join a state whose kind is not well, "
            f"bimolecular or termolecular, so their channel kind cannot be derived: {listing}. "
            "Fix or remove them, then re-run."
        )

    changes = bind.execute(
        text(
            f"""
            SELECT c.id AS channel_id, c.network_id, n.public_ref AS network_ref, c.channel_key,
                   c.kind::text AS old_kind, ({_DERIVED}) AS new_kind
              FROM network_channel AS c
              JOIN network AS n ON n.id = c.network_id
              JOIN network_state AS src ON src.id = c.source_state_id
              JOIN network_state AS snk ON snk.id = c.sink_state_id
             WHERE c.kind::text IS DISTINCT FROM ({_DERIVED})
             ORDER BY c.id
            """
        )
    ).all()
    total = bind.execute(text("SELECT count(*) FROM network_channel")).scalar_one()

    # ``network_channel`` is frozen once its network is accepted (``c6f2a9d4e7b1``: ``network_channel`` is a
    # direct child of root ``network``), so the recompute runs under a declared repair of the one column
    # ``kind`` (``e2c9a4f7b163``). The guard is never disabled.
    bind.execute(
        text(
            """
            INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason)
            VALUES ('network_channel', ARRAY['kind'], CAST(:revision AS text), CAST(:reason AS text))
            """
        ),
        {"revision": revision, "reason": _REASON},
    )
    for change in changes:
        # Never overwrite silently: every changed channel is named (network ref / channel key, old, new).
        print(
            f"[{revision}] network_channel.kind changed: {change.network_ref}/{change.channel_key}: "
            f"{change.old_kind} -> {change.new_kind}"
        )
        bind.execute(
            text(
                "UPDATE network_channel SET kind = CAST(:new AS network_channel_kind) WHERE id = :id"
            ),
            {"new": change.new_kind, "id": change.channel_id},
        )
        # The guard records a row under an accepted network; this records the rest, so the ledger covers
        # every row written, old -> new.
        bind.execute(
            text(
                """
                INSERT INTO accepted_science_repair_change (
                    repair_id, record_type, record_id, target_schema, target_table,
                    row_identity, changed_columns, before_json, after_json
                )
                SELECT declaration.id, 'network', :network_id, 'public', 'network_channel',
                       jsonb_build_object('id', CAST(:id AS bigint)), ARRAY['kind'],
                       jsonb_build_object('kind', CAST(:old AS text)),
                       jsonb_build_object('kind', CAST(:new AS text))
                  FROM accepted_science_repair AS declaration
                 WHERE declaration.target_table = 'network_channel'
                   AND declaration.xact_id = (pg_current_xact_id())::text::bigint
                   AND NOT tckdb_record_is_accepted(
                           CAST('network' AS submission_record_type), CAST(:network_id AS bigint)
                       )
                """
            ),
            {
                "id": change.channel_id,
                "network_id": change.network_id,
                "old": change.old_kind,
                "new": change.new_kind,
            },
        )
    if not changes:
        print("0 changed")
    stabilization = sum(1 for change in changes if change.old_kind == "stabilization")
    print(
        f"[{revision}] network channel kind: {len(changes)} changed "
        f"({stabilization} stabilization), {total - len(changes)} unchanged, {total} channels"
    )
    _rebuild_enum(_NEW_VALUES)


def downgrade() -> None:
    _rebuild_enum(_OLD_VALUES)
