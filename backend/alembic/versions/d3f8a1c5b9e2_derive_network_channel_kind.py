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
   one -- including any ``stabilization`` row, which maps to ``association`` or whatever its states
   say;
3. rebuilds the ``network_channel_kind`` PostgreSQL enum without ``stabilization`` (Postgres cannot
   drop an enum value, so the type is recreated and the column re-cast).

Step 2 is a declared accepted-science repair on the single column ``kind`` (``e2c9a4f7b163``), so
an instance where a network has been approved gets a recorded repair rather than a failed
migration. Only ``kind`` can change under that declaration.

``downgrade()`` restores the enum with ``stabilization`` as a member. It does not rewrite data:
every derived kind is still a member of the old type, and which rows were relabelled is not
something the old schema can ask to remember.

Revision ID: d3f8a1c5b9e2
Revises: c7e2a9f4d138
"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "d3f8a1c5b9e2"
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
    "d3f8a1c5b9e2: network_channel.kind is derived from the kinds of the channel's source and sink "
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
            SELECT c.channel_key, src.kind::text AS source_kind, snk.kind::text AS sink_kind
              FROM network_channel c
              JOIN network_state src ON src.id = c.source_state_id
              JOIN network_state snk ON snk.id = c.sink_state_id
             WHERE src.kind::text NOT IN ('well', 'bimolecular', 'termolecular')
                OR snk.kind::text NOT IN ('well', 'bimolecular', 'termolecular')
             ORDER BY c.id
            """
        )
    ).all()
    if unclassifiable:
        listing = "; ".join(f"{r.channel_key} ({r.source_kind} -> {r.sink_kind})" for r in unclassifiable)
        raise RuntimeError(
            f"{len(unclassifiable)} network_channel row(s) join a state whose kind is not well, "
            f"bimolecular or termolecular, so their channel kind cannot be derived: {listing}. "
            "Fix or remove them, then re-run."
        )

    bind.execute(
        text(
            """
            INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason)
            VALUES ('network_channel', ARRAY['kind'], CAST(:revision AS text), CAST(:reason AS text))
            """
        ),
        {"revision": revision, "reason": _REASON},
    )
    # Every derived value is already a member of the old type, so recompute first; the re-cast in
    # _rebuild_enum then cannot meet 'stabilization'.
    bind.execute(
        text(
            f"""
            UPDATE network_channel AS c
               SET kind = ({_DERIVED})::network_channel_kind
              FROM network_state AS src, network_state AS snk
             WHERE src.id = c.source_state_id
               AND snk.id = c.sink_state_id
               AND c.kind::text IS DISTINCT FROM ({_DERIVED})
            """
        )
    )
    _rebuild_enum(_NEW_VALUES)


def downgrade() -> None:
    _rebuild_enum(_OLD_VALUES)
