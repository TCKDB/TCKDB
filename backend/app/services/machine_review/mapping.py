"""Pure submission -> record mapping policy for machine-review findings.

This module encodes the **safety policy** from
``backend/docs/specs/provisional_machine_review.md`` §6/§13 that gates the
future record-level ``machine_review`` layer: a submission-scoped advisory
result must **never** automatically become a record-level result, and a
record-addressed finding must map only to the *exact* linked record it
names — never to every record that happens to share the submission.

The function is deliberately **pure**: it takes the already-validated
:class:`MachineReviewFinding` set plus a lightweight description of the
submission's record links, and returns a structured mapping. It performs no
database access, no persistence, and no public exposure — this slice only
proves that a safe mapping policy exists (spec §6 Option C "Future").

Addressing model
----------------

Findings are addressed by the public ``(record_type, record_ref)`` pair
that :class:`MachineReviewFinding` already carries — raw internal
``record_id`` is governed by the internal-id policy and is deliberately not
the matching key here. The internal-only variant (matching on
``(record_type, record_id)`` once that addressing is allowed) is the same
algorithm with the key field swapped; the structures carry ``record_id``
through as passthrough metadata so a future persistence layer does not have
to re-resolve it.

Policy (spec §6 / §13, and the slice's required decisions):

1. A finding with no ``record_type`` is submission-scoped only -> unmapped.
2. A finding with ``record_type`` but no ``record_ref`` is not safely
   mappable -> unmapped (warning).
3. A finding maps only to the exact linked record it names.
4. A finding never maps to a record merely because it shares the submission.
5. Unknown ``record_type`` -> unmapped (warning); never raises.
6. Unknown/unlinked ``record_ref`` -> unmapped (warning); never raises.
7. Findings for records not linked to the submission do not map.
8. Multiple findings may map to the same record.
9. One submission may produce mapped and unmapped findings simultaneously.
10. A mapped record's status is derived **only** from its own findings.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from app.db.models.common import SubmissionRecordType
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.schemas import (
    MachineReviewFinding,
    MachineReviewStatus,
)

# The controlled record-type vocabulary a finding is allowed to address.
# A finding citing anything outside this set is treated as unknown (rule 5).
_KNOWN_RECORD_TYPES: frozenset[str] = frozenset(t.value for t in SubmissionRecordType)


@runtime_checkable
class SubmissionRecordLinkLike(Protocol):
    """Structural type for one submission -> record link.

    The ORM ``SubmissionRecordLink`` addresses records by ``record_id``; the
    public mapping path needs the resolved ``record_ref``. Callers therefore
    resolve refs *before* invoking the mapper (keeping this function pure and
    DB-free) and pass objects exposing both. ``record_id`` is optional
    passthrough so the internal-id variant and the future persistence layer
    do not have to re-resolve it.
    """

    @property
    def record_type(self) -> str: ...

    @property
    def record_ref(self) -> str | None: ...

    @property
    def record_id(self) -> int | None: ...


@dataclass(frozen=True)
class SubmissionRecordLinkRef:
    """Concrete, lightweight :class:`SubmissionRecordLinkLike` for callers/tests.

    A submission's linked record, addressed by its public
    ``(record_type, record_ref)`` with the internal ``record_id`` carried
    through as passthrough metadata.
    """

    record_type: str
    record_ref: str | None = None
    record_id: int | None = None


class UnmappedReason(str, Enum):
    """Why a finding was *not* mapped to a record.

    Every value is a safety outcome, not an error: the mapper never raises on
    a malformed/over-broad finding, it routes it here so the caller can decide
    what to surface to an admin (spec: advisory, non-blocking).
    """

    submission_scoped = "submission_scoped"
    """No ``record_type`` -> the finding is about the submission as a whole
    (rule 1). This is expected, not a defect; it carries no warning."""

    missing_record_ref = "missing_record_ref"
    """``record_type`` present but no ``record_ref`` -> not safely mappable
    (rule 2). Warns."""

    unknown_record_type = "unknown_record_type"
    """``record_type`` is outside the controlled vocabulary (rule 5). Warns."""

    unlinked_record = "unlinked_record"
    """A valid, addressed record that is not linked to this submission
    (rule 7) -> must not map. Warns. This is the anti-fan-out guard."""


@dataclass(frozen=True)
class UnmappedFinding:
    """A finding that did not map, with the policy reason it did not."""

    finding: MachineReviewFinding
    reason: UnmappedReason


@dataclass(frozen=True)
class MappedRecord:
    """All findings mapped to one linked record, plus its derived status.

    ``derived_status`` is computed by the shared
    :func:`derive_machine_review_status` over **only** this record's findings
    (rule 10) — never submission-scoped findings, never a sibling record's.
    """

    record_type: str
    record_ref: str
    record_id: Optional[int]
    findings: tuple[MachineReviewFinding, ...]
    derived_status: MachineReviewStatus


@dataclass(frozen=True)
class MachineReviewRecordMapping:
    """Result of mapping a submission's findings onto its linked records."""

    mapped_by_record: dict[tuple[str, str], MappedRecord] = field(default_factory=dict)
    unmapped_findings: tuple[UnmappedFinding, ...] = ()
    mapping_warnings: tuple[str, ...] = ()


def _classify_finding(
    finding: MachineReviewFinding,
    linked_by_ref: dict[tuple[str, str], SubmissionRecordLinkLike],
) -> tuple[str, str] | UnmappedReason:
    """Decide a single finding's bucket.

    Returns the ``(record_type, record_ref)`` key it maps to, or an
    :class:`UnmappedReason`. Precedence is ordered most-fundamental first so
    the warning a finding earns names its *root* defect: a finding that is
    both an unknown type *and* unlinked is reported as the unknown type.
    """
    # Rule 1: no record_type -> the finding is about the submission as a whole.
    if finding.record_type is None:
        return UnmappedReason.submission_scoped

    # Rule 5: a record_type outside the controlled vocabulary cannot address
    # any real record. Reported before the missing-ref check: the type is the
    # more fundamental problem.
    if finding.record_type not in _KNOWN_RECORD_TYPES:
        return UnmappedReason.unknown_record_type

    # Rule 2/6: a typed finding with no ref names no specific record.
    if finding.record_ref is None:
        return UnmappedReason.missing_record_ref

    key = (finding.record_type, finding.record_ref)

    # Rule 3/4/7: map only to the exact linked record. A record that is not in
    # this submission's link set never matches — this is the anti-fan-out core.
    if key not in linked_by_ref:
        return UnmappedReason.unlinked_record

    return key


def map_findings_to_submission_records(
    *,
    findings: Sequence[MachineReviewFinding],
    submission_record_links: Sequence[SubmissionRecordLinkLike],
) -> MachineReviewRecordMapping:
    """Map record-addressed machine-review findings to linked submission records.

    Pure and DB-free. A finding maps to a record only when it explicitly names
    that record's exact ``(record_type, record_ref)`` *and* that record is
    linked to the submission; everything else is routed to ``unmapped_findings``
    with a reason (and a human-readable ``mapping_warnings`` entry for the
    defect cases). A submission-scoped finding (no ``record_type``) is never
    promoted to any record.

    The returned ``mapped_by_record`` is keyed by ``(record_type, record_ref)``;
    each :class:`MappedRecord` derives its status from only its own findings via
    the shared :func:`derive_machine_review_status`.
    """
    # Index links by their public key. Links missing a resolved ref cannot be
    # matched by ref-addressed findings; a same-key link seen twice (e.g. two
    # roles) collapses to one record — role is irrelevant to record identity.
    linked_by_ref: dict[tuple[str, str], SubmissionRecordLinkLike] = {}
    for link in submission_record_links:
        if link.record_ref is None:
            continue
        linked_by_ref.setdefault((link.record_type, link.record_ref), link)

    grouped: dict[tuple[str, str], list[MachineReviewFinding]] = {}
    unmapped: list[UnmappedFinding] = []
    warnings: list[str] = []

    for finding in findings:
        outcome = _classify_finding(finding, linked_by_ref)

        if isinstance(outcome, UnmappedReason):
            unmapped.append(UnmappedFinding(finding=finding, reason=outcome))
            if outcome is UnmappedReason.unknown_record_type:
                warnings.append(
                    f"Finding cites unknown record_type "
                    f"{finding.record_type!r}; not mapped."
                )
            elif outcome is UnmappedReason.missing_record_ref:
                warnings.append(
                    f"Finding for record_type {finding.record_type!r} has no "
                    f"record_ref; not safely mappable."
                )
            elif outcome is UnmappedReason.unlinked_record:
                warnings.append(
                    f"Finding addresses {finding.record_type!r}/"
                    f"{finding.record_ref!r}, which is not linked to this "
                    f"submission; not mapped."
                )
            # submission_scoped is expected, not a defect -> no warning.
            continue

        grouped.setdefault(outcome, []).append(finding)

    mapped_by_record: dict[tuple[str, str], MappedRecord] = {}
    for key, record_findings in grouped.items():
        record_type, record_ref = key
        link = linked_by_ref[key]
        findings_tuple = tuple(record_findings)
        mapped_by_record[key] = MappedRecord(
            record_type=record_type,
            record_ref=record_ref,
            record_id=link.record_id,
            findings=findings_tuple,
            # Rule 10: status from only this record's findings; the review
            # completed (findings exist), so the completed-outcome path applies.
            derived_status=derive_machine_review_status(
                findings_tuple, MachineReviewOutcome.completed
            ),
        )

    return MachineReviewRecordMapping(
        mapped_by_record=mapped_by_record,
        unmapped_findings=tuple(unmapped),
        mapping_warnings=tuple(warnings),
    )
