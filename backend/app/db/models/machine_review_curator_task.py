"""Machine-review curator task queue ORM model.

``machine_review_curator_task`` persists **human triage state** over exact,
mapped, record-level machine-review findings. It is the implementation of the
"curator task table" design in
``backend/docs/specs/machine_review_curator_task_queue.md``.

This table owns exactly one of four orthogonal review axes (spec §2):

* machine-review finding  — advisory signal (what the screener said)
* ``MachineReviewCuratorTaskState`` — *this table*: has a human handled it?
* ``RecordReviewStatus``   — authoritative human review outcome
* ``SubmissionStatus``     — submission lifecycle / moderation

A curator task is a to-do item about a finding, never the finding's verdict.
Per the non-interference policy (spec §9) a task create/assign/resolve writes
**only** to this table; it must never mutate ``submission.status``,
``record_review``, certification, evidence, scientific records, or any public
``trust.*`` fragment.

Notes on design choices baked into the columns:

* ``record_type`` + ``record_id`` use the same generic record-addressing as
  :class:`~app.db.models.submission.SubmissionRecordLink` and
  :class:`~app.db.models.record_review.RecordReview`. ``record_id`` is the raw
  internal id; acceptable because this table is admin/curator-private and is
  never serialised onto a public response (spec §3).
* ``machine_review_status`` / ``highest_severity`` / ``findings_count`` are a
  **denormalised advisory snapshot** for cheap queue ranking/filtering — never
  authoritative (spec §3).
* The enum columns use DB-layer mirrors in :mod:`app.db.models.common`; DB
  models must not import service-layer Pydantic schemas
  (``.claude/rules/schema-rules.md``).
* No automatic task creation, assignment, or resolution behaviour exists yet —
  this is model + migration only (spec §6/§14).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.models.common import (
    MachineReviewCuratorTaskState,
    MachineReviewSeverity,
    MachineReviewStatus,
    SubmissionRecordType,
)

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser
    from app.db.models.submission import Submission, SubmissionAuditEvent


# SQL fragment listing the terminal (resolved/dismissed) workflow states. Kept
# as a module constant so the model CHECK constraint and any future service
# code stay aligned with :meth:`MachineReviewCuratorTaskState.terminal_states`.
_TERMINAL_STATES_SQL = ", ".join(
    f"'{state.value}'" for state in MachineReviewCuratorTaskState.terminal_states()
)


class MachineReviewCuratorTask(Base, TimestampMixin):
    """One human to-do about one finding on one record in one submission."""

    __tablename__ = "machine_review_curator_task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- identity / addressing (spec §4) -------------------------------------
    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- workflow state (spec §5) --------------------------------------------
    workflow_state: Mapped[MachineReviewCuratorTaskState] = mapped_column(
        SAEnum(MachineReviewCuratorTaskState, name="machine_review_curator_task_state"),
        nullable=False,
        default=MachineReviewCuratorTaskState.untriaged,
        server_default=MachineReviewCuratorTaskState.untriaged.value,
    )

    # --- denormalised advisory snapshot (display/ranking only; spec §3) ------
    machine_review_status: Mapped[MachineReviewStatus] = mapped_column(
        SAEnum(MachineReviewStatus, name="machine_review_status"),
        nullable=False,
    )
    highest_severity: Mapped[MachineReviewSeverity] = mapped_column(
        SAEnum(MachineReviewSeverity, name="machine_review_severity"),
        nullable=False,
    )
    findings_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    # --- provenance back to the advisory signal (spec §3) --------------------
    source_audit_event_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "submission_audit_event.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
    )

    # --- assignment / lifecycle ----------------------------------------------
    assigned_to: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    # ``created_at`` comes from TimestampMixin; ``updated_at`` is added here so
    # the queue can order by last-touched without a separate audit table.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- resolution (all three set together for terminal states; spec §7) ----
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    resolved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- relationships --------------------------------------------------------
    submission: Mapped["Submission"] = relationship(
        foreign_keys=[submission_id],
    )
    source_audit_event: Mapped[Optional["SubmissionAuditEvent"]] = relationship(
        foreign_keys=[source_audit_event_id],
    )
    assignee: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[assigned_to],
    )
    resolver: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys=[resolved_by],
    )

    __table_args__ = (
        # Identity / dedupe: one task per finding on a record in a submission.
        UniqueConstraint(
            "submission_id",
            "record_type",
            "record_id",
            "finding_fingerprint",
            name="uq_machine_review_curator_task_identity",
        ),
        # Queue access patterns (spec §10).
        Index(
            "ix_machine_review_curator_task_workflow_state",
            "workflow_state",
        ),
        Index(
            "ix_machine_review_curator_task_state_severity",
            "workflow_state",
            "highest_severity",
        ),
        Index(
            "ix_machine_review_curator_task_assigned_to",
            "assigned_to",
        ),
        Index(
            "ix_machine_review_curator_task_record",
            "record_type",
            "record_id",
        ),
        Index(
            "ix_machine_review_curator_task_submission_id",
            "submission_id",
        ),
        Index(
            "ix_machine_review_curator_task_source_audit_event_id",
            "source_audit_event_id",
        ),
        # Resolution consistency (spec §7): terminal states require all three
        # resolution fields set; open states require all three NULL. Boolean
        # equivalence enforces both directions in one constraint.
        CheckConstraint(
            f"(workflow_state IN ({_TERMINAL_STATES_SQL})) "
            "= (resolved_at IS NOT NULL AND resolved_by IS NOT NULL "
            "AND resolution_note IS NOT NULL)",
            name="resolution_consistency",
        ),
        # A non-negative aggregate count; a task always covers at least one
        # finding.
        CheckConstraint(
            "findings_count >= 1",
            name="findings_count_positive",
        ),
    )
