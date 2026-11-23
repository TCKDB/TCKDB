"""Machine-review job queue, job progress log and admin switches.

Adds ``machine_review_job``, ``machine_review_job_event`` and
``machine_review_settings`` with their enum types. No seed row: an absent
settings row means both switches are on.

``downgrade()`` drops the three tables and the five enum types.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d7f1a6c3e9"
down_revision: Union[str, Sequence[str], None] = "c8e2a4f6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *labels: str) -> postgresql.ENUM:
    return postgresql.ENUM(*labels, name=name, create_type=False)


_PROVIDER_KEY = _enum("machine_review_provider_key", "fake", "local", "cloud", "claude_code", "codex")
_EXECUTOR = _enum("machine_review_executor", "in_process", "remote_runner")
_STATUS = _enum("machine_review_job_status", "queued", "running", "succeeded", "failed", "cancelled")
_FAILURE = _enum(
    "machine_review_job_failure",
    "provider_unavailable",
    "provider_timeout",
    "budget_exhausted",
    "invalid_output",
    "quota_exhausted",
    "auth_failed",
    "worker_lost",
    "config_error",
    "internal_error",
    "machine_review_disabled",
)
_EVENT_KIND = _enum(
    "machine_review_job_event_kind",
    "claimed",
    "context_built",
    "model_turn",
    "tool_call",
    "tool_result",
    "retry",
    "final_output",
    "parse_failed",
    "finished",
)
_ENUMS = (_PROVIDER_KEY, _EXECUTOR, _STATUS, _FAILURE, _EVENT_KIND)


def _fk(column: str, target: str, name: str, **kw) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=name, deferrable=True, initially="IMMEDIATE", **kw
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=False)

    op.create_table(
        "machine_review_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("provider_key", _PROVIDER_KEY, nullable=False),
        sa.Column("executor", _EXECUTOR, nullable=False),
        sa.Column("status", _STATUS, server_default="queued", nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failure_code", _FAILURE, nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("step_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("context_hash", sa.Text(), nullable=True),
        sa.Column("prompt_name", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("budget_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("runner_name", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_review_job"),
        _fk("submission_id", "submission.id", "fk_machine_review_job_submission_id_submission"),
        _fk("requested_by", "app_user.id", "fk_machine_review_job_requested_by_app_user"),
        _fk("audit_event_id", "submission_audit_event.id", "fk_machine_review_job_audit_event_id_submission_audit_event"),
        sa.CheckConstraint(
            "(status = 'succeeded' AND audit_event_id IS NOT NULL) "
            "OR (status IN ('queued', 'running', 'cancelled') AND audit_event_id IS NULL) "
            "OR status = 'failed'",
            name="audit_event_state",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL) "
            "OR (status = 'cancelled' AND (failure_code IS NULL OR failure_code = 'machine_review_disabled')) "
            "OR (status IN ('queued', 'running', 'succeeded') AND failure_code IS NULL)",
            name="failure_code_state",
        ),
        sa.CheckConstraint("char_length(failure_detail) <= 2000", name="failure_detail_length"),
    )
    op.create_index(
        "ux_machine_review_job_open_submission",
        "machine_review_job",
        ["submission_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_machine_review_job_status_created_at", "machine_review_job", ["status", "created_at"])
    op.create_index(
        "ix_machine_review_job_submission_id_created_at", "machine_review_job", ["submission_id", "created_at"]
    )

    op.create_table(
        "machine_review_job_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", _EVENT_KIND, nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("status_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_review_job_event"),
        _fk("job_id", "machine_review_job.id", "fk_machine_review_job_event_job_id_machine_review_job", ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "seq", name="uq_machine_review_job_event_job_id_seq"),
    )

    op.create_table(
        "machine_review_settings",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("machine_review_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("curator_requests_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_review_settings"),
        _fk("updated_by", "app_user.id", "fk_machine_review_settings_updated_by_app_user"),
        sa.CheckConstraint("id = 1", name="singleton"),
    )


def downgrade() -> None:
    op.drop_table("machine_review_settings")
    op.drop_table("machine_review_job_event")
    op.drop_table("machine_review_job")
    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=False)
