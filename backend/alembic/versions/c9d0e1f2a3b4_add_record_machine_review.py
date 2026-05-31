"""add record_machine_review

Adds the append-only ``record_machine_review`` table that durably stores
per-record machine-review projections, per
``backend/docs/specs/record_machine_review_policy.md`` §8 (persistence step).

This is an **additive** migration (phase-aware policy in
``.claude/rules/migration-rules.md``): a new revision on top of the deployed
chain, not an edit to ``d861dfd60891`` or any applied revision. The table is
brand-new and holds no production data on introduction.

Append-only by design: no uniqueness constraint over ``(record_type,
record_id)`` — multiple historical rows per record are expected, and "which is
live" is derived at read time by the currency classifier, never stored.

Reused (NOT created here, ``create_type=False``):
  - enum ``submission_record_type``   (initial schema)
  - enum ``machine_review_status``    (curator-task revision ``b8c9d0e1f2a3``)

This revision creates no enum types, so ``downgrade()`` drops only the table
and its indexes; the shared enums are left intact for their owning revisions.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MACHINE_REVIEW_STATUS_VALUES = (
    "not_run",
    "machine_screened_pass",
    "machine_screened_warning",
    "machine_screened_needs_attention",
    "machine_review_failed",
)


def upgrade() -> None:
    """Upgrade schema: create the append-only record_machine_review table."""

    op.create_table(
        "record_machine_review",
        sa.Column("id", sa.BigInteger(), nullable=False),
        # Record addressing (raw internal id; private table).
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        # Verdict snapshot for this pass.
        sa.Column(
            "status",
            postgresql.ENUM(
                *_MACHINE_REVIEW_STATUS_VALUES,
                name="machine_review_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("curator_priority", sa.String(length=16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "findings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        # Currency key (policy §3.4/§3.5).
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("context_schema_version", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "rubric_versions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        # Provenance.
        sa.Column("source_submission_id", sa.BigInteger(), nullable=True),
        sa.Column("source_audit_event_id", sa.BigInteger(), nullable=True),
        # Timing.
        sa.Column("reviewed_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_record_machine_review")),
        sa.ForeignKeyConstraint(
            ["source_submission_id"],
            ["submission.id"],
            name=op.f("fk_record_machine_review_source_submission_id_submission"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["source_audit_event_id"],
            ["submission_audit_event.id"],
            name=op.f(
                "fk_record_machine_review_source_audit_event_id_submission_audit_event"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "char_length(context_hash) = 64",
            name=op.f("ck_record_machine_review_context_hash_len"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(findings_json) = 'array'",
            name=op.f("ck_record_machine_review_findings_json_is_array"),
        ),
    )

    # Latest-selection access path (policy §4): newest review per record.
    op.create_index(
        "ix_record_machine_review_record_reviewed_at",
        "record_machine_review",
        ["record_type", "record_id", sa.text("reviewed_at DESC")],
    )
    op.create_index(
        "ix_record_machine_review_record_source_audit_event",
        "record_machine_review",
        ["record_type", "record_id", sa.text("source_audit_event_id DESC")],
    )
    op.create_index(
        "ix_record_machine_review_context_hash",
        "record_machine_review",
        ["context_hash"],
    )
    op.create_index(
        "ix_record_machine_review_source_submission_id",
        "record_machine_review",
        ["source_submission_id"],
    )
    op.create_index(
        "ix_record_machine_review_source_audit_event_id",
        "record_machine_review",
        ["source_audit_event_id"],
    )


def downgrade() -> None:
    """Downgrade schema: drop the table and its indexes.

    The ``submission_record_type`` and ``machine_review_status`` enums are NOT
    dropped: they are owned by earlier revisions and reused here.
    """

    op.drop_index(
        "ix_record_machine_review_source_audit_event_id",
        table_name="record_machine_review",
    )
    op.drop_index(
        "ix_record_machine_review_source_submission_id",
        table_name="record_machine_review",
    )
    op.drop_index(
        "ix_record_machine_review_context_hash",
        table_name="record_machine_review",
    )
    op.drop_index(
        "ix_record_machine_review_record_source_audit_event",
        table_name="record_machine_review",
    )
    op.drop_index(
        "ix_record_machine_review_record_reviewed_at",
        table_name="record_machine_review",
    )
    op.drop_table("record_machine_review")
