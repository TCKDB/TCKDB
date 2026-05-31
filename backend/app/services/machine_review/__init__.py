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
from app.services.machine_review.curator_task_lifecycle import (
    assign_curator_task,
    reopen_curator_task,
    resolve_curator_task,
    start_curator_task_review,
)
from app.services.machine_review.curator_tasks import (
    CuratorTaskBuildResult,
    build_curator_tasks_for_submission,
    compute_finding_fingerprint,
)
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.inspection import (
    MachineReviewInspectionView,
    SubmissionMachineReviewInspection,
    SubmissionRecordMachineReviewInspection,
    build_machine_review_inspection_view,
    build_submission_machine_review_inspection,
    get_machine_review_summaries_for_record,
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
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewProviderFindingV2,
    MachineReviewProviderResultV2,
    MachineReviewResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.services.machine_review.trust_adapter import (
    InternalTrustEnvelopeWithMachineReview,
    build_internal_machine_review_trust_fragment,
    build_private_trust_envelope_with_machine_review,
)

__all__ = [
    "MACHINE_REVIEW_V2_SCHEMA_VERSION",
    "AuditRecordLink",
    "CuratorPriority",
    "CuratorTaskBuildResult",
    "InternalTrustEnvelopeWithMachineReview",
    "MachineReviewAuditProjection",
    "MachineReviewCategory",
    "MachineReviewFinding",
    "MachineReviewInspectionView",
    "MachineReviewOutcome",
    "MachineReviewProviderFindingV2",
    "MachineReviewProviderResultV2",
    "MachineReviewRecordMapping",
    "MachineReviewRecordSummary",
    "MachineReviewResult",
    "MachineReviewSeverity",
    "MachineReviewStatus",
    "MappedRecord",
    "ParsedMachineReviewPayload",
    "RecordMachineReview",
    "SubmissionAuditEventLike",
    "SubmissionMachineReviewInspection",
    "SubmissionRecordMachineReviewInspection",
    "SubmissionRecordLinkLike",
    "SubmissionRecordLinkRef",
    "UnmappedFinding",
    "UnmappedReason",
    "assign_curator_task",
    "build_curator_tasks_for_submission",
    "build_internal_machine_review_trust_fragment",
    "build_machine_review_record_summary",
    "build_machine_review_inspection_view",
    "build_private_trust_envelope_with_machine_review",
    "build_submission_machine_review_inspection",
    "compute_finding_fingerprint",
    "derive_machine_review_status",
    "event_is_machine_review",
    "get_machine_review_summaries_for_record",
    "machine_review_result_from_audit_event",
    "map_findings_to_submission_records",
    "record_machine_reviews_from_audit_events",
    "record_machine_reviews_from_submission_audit_event",
    "reopen_curator_task",
    "resolve_curator_task",
    "select_latest_machine_review_for_record",
    "start_curator_task_review",
]
