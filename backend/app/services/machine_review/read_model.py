"""Internal read-model projection of the latest machine-review state per record.

This module answers a single private question for one scientific record:

    Given zero or more machine-review passes whose findings were already
    mapped to this record, what is the *latest* machine-review summary a
    future public ``trust.machine_review`` fragment could render?

It is a **read-only projection**. It performs no database access, no
persistence, no provider calls, and no public read-API integration. It does
not — and structurally cannot — mutate ``review_status``,
``benchmark_reference``, ``is_certified``, evidence completeness, the
deterministic check sets, ``hard_fail_reason``, ``trust_status``, any
scientific record, or ``submission.status``. The output
(:class:`MachineReviewRecordSummary`) is ``extra="forbid"`` and contains no
field that could carry a mutation instruction (spec §8/§10).

Why a wrapper type
------------------

The pure mapper (:mod:`app.services.machine_review.mapping`) produces a
:class:`~app.services.machine_review.mapping.MappedRecord` per record for a
*single* review pass: findings plus a derived status, but no temporal or
provenance metadata. Selecting the *latest* review means comparing across
passes, which needs ``reviewed_at`` and the producing ``model``/``provider``.
:class:`RecordMachineReview` is that lightweight wrapper — it mirrors the
future ``record_machine_review`` row (spec §6 Option B) without introducing
any persistence or public schema. Construct one per ``(review pass, record)``;
:meth:`RecordMachineReview.from_mapped_record` bridges directly from mapper
output.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.machine_review.mapping import MappedRecord
from app.services.machine_review.schemas import (
    CuratorPriority,
    MachineReviewFinding,
    MachineReviewSeverity,
    MachineReviewStatus,
)

# Total order over finding severities, lowest-to-highest. Used only to pick the
# single highest severity present on the selected review's findings.
_SEVERITY_RANK: dict[MachineReviewSeverity, int] = {
    MachineReviewSeverity.info: 0,
    MachineReviewSeverity.warning: 1,
    MachineReviewSeverity.critical: 2,
}


@dataclass(frozen=True)
class RecordMachineReview:
    """One machine-review pass's result, scoped to a single scientific record.

    Mirrors a future ``record_machine_review`` row (spec §6 Option B) without
    any persistence: it bundles the findings that one review pass mapped to
    this record with that pass's status and provenance metadata (which
    ``model``/``provider`` produced it, when it ran, which submission triggered
    it). The ``findings`` here are already record-scoped — submission-scoped
    and sibling-record findings were filtered out by the mapper — so the read
    model never re-derives mapping scope.

    ``status`` is carried explicitly rather than always derived from
    ``findings`` because the non-finding outcomes have no findings to derive
    from: a ``machine_review_failed`` or ``not_run`` pass legitimately has an
    empty ``findings`` tuple (see
    :func:`~app.services.machine_review.derivation.derive_machine_review_status`).
    """

    record_type: str
    record_ref: str
    status: MachineReviewStatus
    findings: tuple[MachineReviewFinding, ...] = ()
    curator_priority: CuratorPriority | None = None
    model: str | None = None
    provider: str | None = None
    reviewed_at: datetime | None = None
    submission_id: int | None = None
    # The source audit event this pass was projected from. It is the primary
    # latest-selection tie-break (a higher id is a strictly later event) and the
    # per-record provenance: the history of which events contributed to a record
    # is the set of ``audit_event_id`` across its reviews. ``None`` for
    # hand-built test wrappers and any pass with no backing event.
    audit_event_id: int | None = None
    # Internal id carried through as passthrough metadata for a future
    # persistence layer; governed by the internal-id policy, never surfaced.
    record_id: int | None = None

    @classmethod
    def from_mapped_record(
        cls,
        mapped: MappedRecord,
        *,
        reviewed_at: datetime | None = None,
        model: str | None = None,
        provider: str | None = None,
        submission_id: int | None = None,
        curator_priority: CuratorPriority | None = None,
        audit_event_id: int | None = None,
    ) -> "RecordMachineReview":
        """Wrap a mapper :class:`MappedRecord` with one pass's review metadata.

        The mapper already enforced record scope and derived the per-record
        status; this only attaches the temporal/provenance fields the read
        model needs to choose between passes. It copies the mapped findings and
        derived status verbatim — it does not re-map or re-derive.
        """
        return cls(
            record_type=mapped.record_type,
            record_ref=mapped.record_ref,
            status=mapped.derived_status,
            findings=mapped.findings,
            curator_priority=curator_priority,
            model=model,
            provider=provider,
            reviewed_at=reviewed_at,
            submission_id=submission_id,
            audit_event_id=audit_event_id,
            record_id=mapped.record_id,
        )


class MachineReviewRecordSummary(BaseModel):
    """Internal summary of the latest machine-review state for one record.

    This is the shape a future public ``trust.machine_review`` fragment (spec
    §4/§10) could render, prepared here but **not** exposed: nothing imports
    this into a public response schema, and it is ``extra="forbid"`` so it can
    never carry a provider-supplied mutation payload. It is a projection of the
    single *selected latest* review, never an aggregate across passes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MachineReviewStatus
    curator_priority: CuratorPriority | None = None
    findings_count: int = Field(default=0, ge=0)
    highest_severity: MachineReviewSeverity | None = None
    model: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
    reviewed_at: datetime | None = None
    submission_id: int | None = None


def _highest_severity(
    findings: Sequence[MachineReviewFinding],
) -> MachineReviewSeverity | None:
    """Return the single highest severity present, or ``None`` if no findings."""
    if not findings:
        return None
    return max((f.severity for f in findings), key=lambda s: _SEVERITY_RANK[s])


def _reviewed_at_rank(review: RecordMachineReview) -> tuple[int, ...]:
    """Primary latest-selection key: greater = more recent.

    A review with no ``reviewed_at`` sorts strictly *older* than any review
    that has one, and ``datetime`` is never compared against ``None`` (the
    leading ``0``/``1`` flag decides those cases first). When two reviews both
    carry a ``reviewed_at``, their timestamps are compared directly.
    """
    if review.reviewed_at is None:
        return (0,)
    return (1, review.reviewed_at.timestamp())


def _tie_break_key(review: RecordMachineReview, index: int) -> tuple[int, int, int]:
    """Deterministic tie-break when two reviews share the same ``reviewed_at``.

    Returns a sortable tuple where *greater = preferred* (selected as latest),
    combined after :func:`_reviewed_at_rank`. Policy, in order:

    1. higher ``audit_event_id`` — the source audit events are created
       monotonically, so a higher id is the strictly later event; this is the
       spec's primary tie-break. A missing ``audit_event_id`` sorts lowest.
    2. higher ``submission_id`` — a coarser monotonic fallback for hand-built
       reviews with no backing event id; a missing ``submission_id`` sorts
       lowest.
    3. later input position — last-resort, keeps the order total.

    This is a total order, so equal timestamps can never produce a
    non-deterministic selection.
    """
    audit_rank = review.audit_event_id if review.audit_event_id is not None else -1
    submission_rank = review.submission_id if review.submission_id is not None else -1
    return (audit_rank, submission_rank, index)


def select_latest_machine_review_for_record(
    *,
    record_type: str,
    record_ref: str,
    reviews: Sequence[RecordMachineReview],
) -> RecordMachineReview | None:
    """Select the single latest review addressing exactly this record.

    Filters ``reviews`` to those whose ``(record_type, record_ref)`` matches
    the target — so sibling-record reviews can never be selected — then picks
    the most recent by ``reviewed_at`` with :func:`_tie_break_key` resolving
    ties deterministically. Returns ``None`` when no review addresses the
    record.
    """
    matching = [
        (index, review)
        for index, review in enumerate(reviews)
        if review.record_type == record_type and review.record_ref == record_ref
    ]
    if not matching:
        return None

    return max(
        matching,
        key=lambda pair: (_reviewed_at_rank(pair[1]), _tie_break_key(pair[1], pair[0])),
    )[1]


def build_machine_review_record_summary(
    *,
    record_type: str,
    record_ref: str,
    reviews: Sequence[RecordMachineReview],
) -> MachineReviewRecordSummary:
    """Build the latest-machine-review summary for one record.

    Selection policy (deterministic):

    1. No review addresses the record -> ``status=not_run`` summary.
    2. Otherwise the newest review by ``reviewed_at`` is selected
       (:func:`select_latest_machine_review_for_record`), ties broken
       deterministically.
    3. The selected review's ``status``/``curator_priority``/provenance become
       the summary's.
    4. ``findings_count`` and ``highest_severity`` are computed from **only**
       the selected review's findings — which are already record-scoped, so
       submission-scoped and sibling-record findings are structurally excluded.
    """
    latest = select_latest_machine_review_for_record(
        record_type=record_type, record_ref=record_ref, reviews=reviews
    )

    if latest is None:
        # Rule 1: nothing has reviewed this record. ``not_run`` is the absence
        # of a review, distinct from ``machine_review_failed`` (a review ran
        # and could not complete).
        return MachineReviewRecordSummary(status=MachineReviewStatus.not_run)

    return MachineReviewRecordSummary(
        status=latest.status,
        curator_priority=latest.curator_priority,
        findings_count=len(latest.findings),
        highest_severity=_highest_severity(latest.findings),
        model=latest.model,
        provider=latest.provider,
        reviewed_at=latest.reviewed_at,
        submission_id=latest.submission_id,
    )
