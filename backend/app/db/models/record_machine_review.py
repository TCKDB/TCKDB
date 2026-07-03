"""Append-only persisted record-level machine-review projections.

``record_machine_review`` durably stores one row per machine-review **pass**
over one scientific record, so machine-review results become queryable and
classifiable (``not_run`` / ``current`` / ``stale`` / ``historical``) by the
pure currency classifier
(:func:`app.services.machine_review.currency.classify_machine_review_currency`),
per ``backend/docs/specs/record_machine_review_policy.md`` §8.

**Append-only.** A re-review inserts a *new* row; rows are never updated in
place. There is therefore intentionally **no** uniqueness constraint over
``(record_type, record_id)`` — multiple historical rows for the same record are
the expected shape, and "which is live" is derived at read time by the currency
classifier (latest by ``reviewed_at`` then ``source_audit_event_id`` then
``id``), never stored as a mutable flag (policy §2/§4).

**Private.** This table is not wired into any public scientific read; no
``trust.machine_review`` is emitted and the public ``TrustFragment`` is
untouched (policy §7 is future work). ``record_id`` is the raw internal id,
acceptable because the table is private — the same addressing as
:class:`~app.db.models.submission.SubmissionRecordLink` and
:class:`~app.db.models.machine_review_curator_task.MachineReviewCuratorTask`.

The naming distinguishes the persisted row (:class:`RecordMachineReviewRow`)
from the in-memory read-model projection
(:class:`app.services.machine_review.read_model.RecordMachineReview`); the table
is ``record_machine_review``.

The ``status`` column reuses the existing DB-layer ``machine_review_status``
enum (created by the curator-task revision); ``submission_record_type`` is
reused from the initial schema. ``curator_priority`` is stored as text rather
than a DB enum to avoid enum churn (validated at the application layer). DB
models must not import service-layer Pydantic schemas
(``.claude/rules/schema-rules.md``), so the enum columns use the
:mod:`app.db.models.common` mirrors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.models.common import MachineReviewStatus, SubmissionRecordType


class RecordMachineReviewRow(Base, TimestampMixin):
    """One persisted machine-review pass over one scientific record (append-only)."""

    __tablename__ = "record_machine_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- record addressing (raw internal id; private table) ------------------
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # --- the machine-review verdict snapshot for this pass -------------------
    status: Mapped[MachineReviewStatus] = mapped_column(
        SAEnum(MachineReviewStatus, name="machine_review_status"),
        nullable=False,
    )
    curator_priority: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    findings_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # --- currency key (policy §3.4/§3.5) -------------------------------------
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rubric_versions_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )

    # --- provenance back to what triggered this review -----------------------
    source_submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    source_audit_event_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "submission_audit_event.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
    )

    # --- timing ---------------------------------------------------------------
    # ``reviewed_at`` is when the review ran (the primary latest-selection key);
    # ``created_at`` (TimestampMixin) is when the row was appended.
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )

    # No ORM relationships are declared: the FK columns carry provenance, and
    # the read/write helpers only need scalar values. Keeping this row
    # relationship-free avoids back-population coupling and mirrors the
    # append-only, projection-only intent of the table.

    __table_args__ = (
        # Latest-selection access path (policy §4): newest review per record.
        Index(
            "ix_record_machine_review_record_reviewed_at",
            "record_type",
            "record_id",
            text("reviewed_at DESC"),
        ),
        # Tie-break access path: source_audit_event_id DESC within a record.
        Index(
            "ix_record_machine_review_record_source_audit_event",
            "record_type",
            "record_id",
            text("source_audit_event_id DESC"),
        ),
        Index("ix_record_machine_review_context_hash", "context_hash"),
        Index(
            "ix_record_machine_review_source_submission_id",
            "source_submission_id",
        ),
        Index(
            "ix_record_machine_review_source_audit_event_id",
            "source_audit_event_id",
        ),
        # context_hash is a SHA-256 hex digest — exactly 64 chars.
        CheckConstraint(
            "char_length(context_hash) = 64",
            name="context_hash_len",
        ),
        # findings_json is always a JSON array (default '[]'), never an object.
        CheckConstraint(
            "jsonb_typeof(findings_json) = 'array'",
            name="findings_json_is_array",
        ),
    )
