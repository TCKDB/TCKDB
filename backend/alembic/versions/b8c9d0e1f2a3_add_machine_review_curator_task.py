"""add machine_review_curator_task

Adds the persisted human triage queue over exact, mapped machine-review
findings, per ``backend/docs/specs/machine_review_curator_task_queue.md``.

This is an **additive** migration (phase-aware policy in
``.claude/rules/migration-rules.md``): a new revision on top of the deployed
chain, not an edit to ``d861dfd60891`` or any applied revision. The table is
brand-new and holds no production data on introduction, but it is NOT in the
network/PDep exception group — standard already-deployed rules apply once it
ships.

Creates:
  - enum ``machine_review_curator_task_state`` (human workflow axis)
  - enum ``machine_review_status``  (DB-layer mirror of the advisory status)
  - enum ``machine_review_severity`` (DB-layer mirror of the finding severity)
  - table ``machine_review_curator_task`` with its identity unique
    constraint, queue indexes, foreign keys, and resolution-consistency
    check.

The ``submission_record_type`` enum already exists from the initial schema
and is reused (``create_type=False``).

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WORKFLOW_STATE_VALUES = (
    "untriaged",
    "needs_curator_review",
    "in_curator_review",
    "resolved_no_action",
    "resolved_human_reviewed",
    "dismissed_machine_finding",
)
_TERMINAL_STATE_VALUES = (
    "resolved_no_action",
    "resolved_human_reviewed",
    "dismissed_machine_finding",
)
_MACHINE_REVIEW_STATUS_VALUES = (
    "not_run",
    "machine_screened_pass",
    "machine_screened_warning",
    "machine_screened_needs_attention",
    "machine_review_failed",
)
_MACHINE_REVIEW_SEVERITY_VALUES = ("info", "warning", "critical")

_TERMINAL_STATES_SQL = ", ".join(f"'{v}'" for v in _TERMINAL_STATE_VALUES)


def upgrade() -> None:
    """Upgrade schema."""

    bind = op.get_bind()

    # New enum types owned by this revision. ``submission_record_type`` is
    # reused from the initial schema and intentionally NOT created here.
    workflow_state_enum = postgresql.ENUM(
        *_WORKFLOW_STATE_VALUES,
        name="machine_review_curator_task_state",
        create_type=False,
    )
    workflow_state_enum.create(bind, checkfirst=True)

    machine_review_status_enum = postgresql.ENUM(
        *_MACHINE_REVIEW_STATUS_VALUES,
        name="machine_review_status",
        create_type=False,
    )
    machine_review_status_enum.create(bind, checkfirst=True)

    machine_review_severity_enum = postgresql.ENUM(
        *_MACHINE_REVIEW_SEVERITY_VALUES,
        name="machine_review_severity",
        create_type=False,
    )
    machine_review_severity_enum.create(bind, checkfirst=True)

    op.create_table(
        "machine_review_curator_task",
        sa.Column("id", sa.BigInteger(), nullable=False),
        # Identity / addressing.
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_fingerprint", sa.String(length=64), nullable=False),
        # Workflow state.
        sa.Column(
            "workflow_state",
            postgresql.ENUM(
                *_WORKFLOW_STATE_VALUES,
                name="machine_review_curator_task_state",
                create_type=False,
            ),
            server_default="untriaged",
            nullable=False,
        ),
        # Denormalised advisory snapshot.
        sa.Column(
            "machine_review_status",
            postgresql.ENUM(
                *_MACHINE_REVIEW_STATUS_VALUES,
                name="machine_review_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "highest_severity",
            postgresql.ENUM(
                *_MACHINE_REVIEW_SEVERITY_VALUES,
                name="machine_review_severity",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "findings_count",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        # Provenance.
        sa.Column("source_audit_event_id", sa.BigInteger(), nullable=True),
        # Assignment / lifecycle.
        sa.Column("assigned_to", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Resolution.
        sa.Column("resolved_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_machine_review_curator_task")
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_machine_review_curator_task_submission_id_submission"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["source_audit_event_id"],
            ["submission_audit_event.id"],
            name=op.f(
                "fk_machine_review_curator_task_source_audit_event_id_submission_audit_event"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["app_user.id"],
            name=op.f("fk_machine_review_curator_task_assigned_to_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["app_user.id"],
            name=op.f("fk_machine_review_curator_task_resolved_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.UniqueConstraint(
            "submission_id",
            "record_type",
            "record_id",
            "finding_fingerprint",
            name="uq_machine_review_curator_task_identity",
        ),
        sa.CheckConstraint(
            f"(workflow_state IN ({_TERMINAL_STATES_SQL})) "
            "= (resolved_at IS NOT NULL AND resolved_by IS NOT NULL "
            "AND resolution_note IS NOT NULL)",
            name=op.f("ck_machine_review_curator_task_resolution_consistency"),
        ),
        sa.CheckConstraint(
            "findings_count >= 1",
            name=op.f("ck_machine_review_curator_task_findings_count_positive"),
        ),
    )

    op.create_index(
        "ix_machine_review_curator_task_workflow_state",
        "machine_review_curator_task",
        ["workflow_state"],
    )
    op.create_index(
        "ix_machine_review_curator_task_state_severity",
        "machine_review_curator_task",
        ["workflow_state", "highest_severity"],
    )
    op.create_index(
        "ix_machine_review_curator_task_assigned_to",
        "machine_review_curator_task",
        ["assigned_to"],
    )
    op.create_index(
        "ix_machine_review_curator_task_record",
        "machine_review_curator_task",
        ["record_type", "record_id"],
    )
    op.create_index(
        "ix_machine_review_curator_task_submission_id",
        "machine_review_curator_task",
        ["submission_id"],
    )
    op.create_index(
        "ix_machine_review_curator_task_source_audit_event_id",
        "machine_review_curator_task",
        ["source_audit_event_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    bind = op.get_bind()

    op.drop_index(
        "ix_machine_review_curator_task_source_audit_event_id",
        table_name="machine_review_curator_task",
    )
    op.drop_index(
        "ix_machine_review_curator_task_submission_id",
        table_name="machine_review_curator_task",
    )
    op.drop_index(
        "ix_machine_review_curator_task_record",
        table_name="machine_review_curator_task",
    )
    op.drop_index(
        "ix_machine_review_curator_task_assigned_to",
        table_name="machine_review_curator_task",
    )
    op.drop_index(
        "ix_machine_review_curator_task_state_severity",
        table_name="machine_review_curator_task",
    )
    op.drop_index(
        "ix_machine_review_curator_task_workflow_state",
        table_name="machine_review_curator_task",
    )
    op.drop_table("machine_review_curator_task")

    # Drop only the enum types created by this revision. ``submission_record_type``
    # is owned by the initial schema and must be left intact.
    postgresql.ENUM(name="machine_review_severity").drop(bind, checkfirst=True)
    postgresql.ENUM(name="machine_review_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="machine_review_curator_task_state").drop(
        bind, checkfirst=True
    )
