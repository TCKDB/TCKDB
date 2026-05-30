"""Provisional machine-review contract package.

Internal types/contracts only for the future machine-review layer
(``backend/docs/specs/provisional_machine_review.md``). This package
intentionally contains **no** provider implementation, no persistence, no
RAG, no public read-API integration, and no submission-workflow wiring. It
exists so the future direction is type-safe and testable, and so the
machine-review axis is impossible to confuse with human review or the
submission precheck at the type level.
"""

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
from app.services.machine_review.schemas import (
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)

__all__ = [
    "CuratorPriority",
    "MachineReviewCategory",
    "MachineReviewFinding",
    "MachineReviewOutcome",
    "MachineReviewRecordMapping",
    "MachineReviewResult",
    "MachineReviewSeverity",
    "MachineReviewStatus",
    "MappedRecord",
    "SubmissionRecordLinkLike",
    "SubmissionRecordLinkRef",
    "UnmappedFinding",
    "UnmappedReason",
    "derive_machine_review_status",
    "map_findings_to_submission_records",
]
