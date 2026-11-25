"""Machine-review lessons: curator-written notes the reviewer may search once verified.

Adds ``machine_review_lesson`` with the ``machine_review_category``,
``machine_review_lesson_status`` and ``machine_review_lesson_signal`` enum types.

``downgrade()`` drops the table and the three enum types.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7a3c9d1b5f2"
down_revision: Union[str, Sequence[str], None] = "b4d7f1a6c3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *labels: str) -> postgresql.ENUM:
    return postgresql.ENUM(*labels, name=name, create_type=False)


_CATEGORY = _enum(
    "machine_review_category",
    "provenance",
    "units",
    "geometry",
    "kinetics",
    "thermo",
    "statmech",
    "transport",
    "transition_state_validation",
    "calculation_parameters",
    "consistency",
    "schema_gap",
)
_STATUS = _enum("machine_review_lesson_status", "proposed", "verified", "retired")
_SIGNAL = _enum(
    "machine_review_lesson_signal",
    "curator_dismissed_finding",
    "curator_flagged_missed_issue",
    "curator_confirmed_finding",
)
_RECORD_TYPE = postgresql.ENUM(name="submission_record_type", create_type=False)
_ENUMS = (_CATEGORY, _STATUS, _SIGNAL)


def _fk(column: str, target: str, name: str, **kw) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], name=name, deferrable=True, initially="IMMEDIATE", **kw)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=False)

    op.create_table(
        "machine_review_lesson",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("category", _CATEGORY, nullable=True),
        sa.Column("record_type", _RECORD_TYPE, nullable=True),
        sa.Column("scope_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signal", _SIGNAL, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", _STATUS, server_default="proposed", nullable=False),
        sa.Column("source_job_id", sa.BigInteger(), nullable=True),
        sa.Column("source_curator_task_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("verified_by", sa.BigInteger(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("retired_by", sa.BigInteger(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_review_lesson"),
        _fk("source_job_id", "machine_review_job.id", "fk_mr_lesson_source_job", ondelete="SET NULL"),
        _fk(
            "source_curator_task_id",
            "machine_review_curator_task.id",
            "fk_mr_lesson_source_task",
            ondelete="SET NULL",
        ),
        _fk("created_by", "app_user.id", "fk_mr_lesson_created_by"),
        _fk("verified_by", "app_user.id", "fk_mr_lesson_verified_by"),
        _fk("retired_by", "app_user.id", "fk_mr_lesson_retired_by"),
        sa.CheckConstraint("char_length(btrim(text, E' \\t\\r\\n')) BETWEEN 1 AND 500", name="text_length"),
        sa.CheckConstraint(
            "(status = 'proposed' AND verified_by IS NULL AND verified_at IS NULL "
            "AND retired_by IS NULL AND retired_at IS NULL) "
            "OR (status = 'verified' AND verified_by IS NOT NULL AND verified_at IS NOT NULL "
            "AND retired_by IS NULL AND retired_at IS NULL) "
            "OR (status = 'retired' AND retired_by IS NOT NULL AND retired_at IS NOT NULL)",
            name="status_stamps",
        ),
    )
    op.create_index("ix_machine_review_lesson_status_verified_at", "machine_review_lesson", ["status", "verified_at"])
    op.create_index("ix_machine_review_lesson_status_created_at", "machine_review_lesson", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("machine_review_lesson")
    bind = op.get_bind()
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=False)
