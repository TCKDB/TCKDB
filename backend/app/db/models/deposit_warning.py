"""Append-only deposit-time warnings, stored against the record they are about.

A deposit's warn-tier findings (``UploadWarning``) used to reach the depositor
once, in the upload response, and then survive only inside the upload job's
result. This table keeps each one beside the record it concerns, so a reader of
that record can see that it tripped an expectation when it was deposited.

**One row per (submission, record, content hash).** Each submission writes its own
rows; a re-deposit does not touch an earlier submission's. The unique key drops only a
truly identical warning (same code, field, message and context) within one submission and record.

**Marker rows.** A row with ``code IS NULL`` says "this record was checked by this
submission at ``check_version``", whether or not anything fired. It is what lets a
read say ``recorded`` with no items instead of leaving "nothing fired" and "nothing
was ever checked" looking alike. A record deposited before this table existed has no
marker and reads ``not_recorded``.

**Anchor.** ``record_type``/``record_id`` name the result or provenance record the
deposit created (calculation, transition-state entry, kinetics, thermo, statmech,
transport, network solve, conformer observation). A warning whose subject is a
shared, deduplicated identity row is never attached to that row; it attaches to the
result this deposit created, or, where there is none, to the submission alone
(``record_type`` and ``record_id`` both NULL).

**Append-only, unconditionally.** A trigger refuses UPDATE, DELETE and TRUNCATE
(``trg_deposit_warning_append_only``, ``trg_deposit_warning_no_truncate``). INSERT is
allowed even under an approved record, because a later artifact upload can add a
warning to an existing calculation. This is the regime ``record_reproducibility_assessment``
lives under, not the accepted-science child guard.

Warnings never change a value, order a list or gate selection.
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
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import SubmissionRecordType


class DepositWarning(Base):
    """One deposit-time warning (or one "checked" marker) for one record of one submission."""

    __tablename__ = "deposit_warning"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: The submission that produced the row. NULL only for a warning an artifact upload earned when the
    #: calculation had no live submission to join; such a row must name a record.
    submission_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", name="fk_deposit_warning_submission", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    record_type: Mapped[Optional[SubmissionRecordType]] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type", create_type=False), nullable=True
    )
    record_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    #: NULL marks "checked"; every other column but ``check_version`` is then NULL too.
    code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The ``scientific_check_register`` entry number, when the code has one.
    register_entry: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    #: The payload position the producer named (a depositor-facing path).
    field: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The values that triggered the warning, as the producer stated them.
    context: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    #: SHA-256 of code, field, message and canonical context: only truly identical warnings dedupe.
    content_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The version of the warning set that judged the deposit.
    check_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(record_type IS NULL) = (record_id IS NULL)",
            name="record_both_or_neither",
        ),
        CheckConstraint(
            "(code IS NULL AND register_entry IS NULL AND field IS NULL AND message IS NULL AND context IS NULL "
            "AND content_hash IS NULL) "
            "OR (code IS NOT NULL AND field IS NOT NULL AND message IS NOT NULL AND content_hash IS NOT NULL AND content_hash ~ '^[0-9a-f]{64}$')",
            name="marker_or_complete",
        ),
        CheckConstraint(
            "submission_id IS NOT NULL OR (record_type IS NOT NULL AND code IS NOT NULL)",
            name="submission_or_record_warning",
        ),
        CheckConstraint(
            "context IS NULL OR jsonb_typeof(context) = 'object'",
            name="context_is_object",
        ),
        UniqueConstraint(
            "submission_id",
            "record_type",
            "record_id",
            "content_hash",
            name="uq_deposit_warning_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_deposit_warning_record", "record_type", "record_id"),
    )
