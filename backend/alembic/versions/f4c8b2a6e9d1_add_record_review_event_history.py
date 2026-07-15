"""add record_review_event history

Schema audit R7. Adds the append-only ``record_review_event`` table that
preserves who-changed-what-when for consumer-facing record review state.
``record_review`` still holds exactly one current-state row per
``(record_type, record_id)``; this table is its history log, mirroring
``submission_audit_event`` for submissions.

This is an **additive** migration (phase-aware policy in
``.claude/rules/migration-rules.md``): a new revision on top of the deployed
chain, not an edit to ``d861dfd60891`` or any applied revision. The table is
brand-new and holds no production data on introduction.

Created here:
  - enum ``record_review_event_kind`` (values: created, status_change)

Reused (NOT created here, ``create_type=False``):
  - enum ``record_review_status`` (initial schema)

``downgrade()`` drops the table then the newly-created enum type; the shared
``record_review_status`` enum is left intact for its owning revision.

Revision ID: f4c8b2a6e9d1
Revises: e7a3c9f1b5d2
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f4c8b2a6e9d1"
down_revision: Union[str, Sequence[str], None] = "e7a3c9f1b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EVENT_KIND_VALUES = ("created", "status_change")


def upgrade() -> None:
    """Upgrade schema: create the append-only record_review_event table."""

    event_kind_enum = postgresql.ENUM(
        *_EVENT_KIND_VALUES,
        name="record_review_event_kind",
        create_type=False,
    )
    event_kind_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "record_review_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("record_review_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_kind",
            postgresql.ENUM(
                *_EVENT_KIND_VALUES,
                name="record_review_event_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            postgresql.ENUM(name="record_review_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(name="record_review_status", create_type=False),
            nullable=True,
        ),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_record_review_event")),
        sa.ForeignKeyConstraint(
            ["record_review_id"],
            ["record_review.id"],
            name=op.f("fk_record_review_event_record_review_id_record_review"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_user.id"],
            name=op.f("fk_record_review_event_actor_user_id_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )

    op.create_index(
        "ix_record_review_event_record_review_id",
        "record_review_event",
        ["record_review_id"],
    )


def downgrade() -> None:
    """Downgrade schema: drop the table then the newly-created enum type.

    The ``record_review_status`` enum is NOT dropped: it is owned by the
    initial schema and only reused here.
    """

    op.drop_index(
        "ix_record_review_event_record_review_id",
        table_name="record_review_event",
    )
    op.drop_table("record_review_event")
    postgresql.ENUM(name="record_review_event_kind").drop(
        op.get_bind(), checkfirst=True
    )
