"""Provisional machine-review contract package.

Internal types/contracts only for the future machine-review layer
(``backend/docs/specs/provisional_machine_review.md``). This package
intentionally contains **no** provider implementation, no persistence, no
RAG, no public read-API integration, and no submission-workflow wiring. It
exists so the future direction is type-safe and testable, and so the
machine-review axis is impossible to confuse with human review or the
submission precheck at the type level.
"""

from app.services.machine_review.audit_adapter import (
    AuditRecordLink,
    MachineReviewAuditProjection,
    ParsedMachineReviewPayload,
    SubmissionAuditEventLike,
    event_is_machine_review,
    machine_review_result_from_audit_event,
    record_machine_reviews_from_audit_events,
    record_machine_reviews_from_submission_audit_event,
)
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.mapping import (
    MachineReviewRecordMapping,
    MappedRecord,
    SubmissionRecordLinkLike,
    SubmissionRecordLinkRef,
    UnmappedFinding,
    UnmappedReason,
    map_findings_to_submission_records,
)
from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
    build_machine_review_record_summary,
    select_latest_machine_review_for_record,
)
from app.services.machine_review.schemas import (
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)

__all__ = [
    "AuditRecordLink",
    "CuratorPriority",
    "MachineReviewAuditProjection",
    "MachineReviewCategory",
    "MachineReviewFinding",
    "MachineReviewOutcome",
    "MachineReviewRecordMapping",
    "MachineReviewRecordSummary",
    "MachineReviewResult",
    "MachineReviewSeverity",
    "MachineReviewStatus",
    "MappedRecord",
    "ParsedMachineReviewPayload",
    "RecordMachineReview",
    "SubmissionAuditEventLike",
    "SubmissionRecordLinkLike",
    "SubmissionRecordLinkRef",
    "UnmappedFinding",
    "UnmappedReason",
    "build_machine_review_record_summary",
    "derive_machine_review_status",
    "event_is_machine_review",
    "machine_review_result_from_audit_event",
    "map_findings_to_submission_records",
    "record_machine_reviews_from_audit_events",
    "record_machine_reviews_from_submission_audit_event",
    "select_latest_machine_review_for_record",
]
