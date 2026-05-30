"""Private/admin inspection service for machine-review projections.

This module answers one internal/admin question:

    Given a record identity, what machine-review summaries can be derived from
    the existing submission audit events that reference it?

It is a **read-only inspection/debugging aid** (spec
``backend/docs/specs/provisional_machine_review.md`` §5/§6) for maintainers to
verify machine-review behavior *before* deciding whether to expose
``trust.machine_review`` publicly. It deliberately does **not** imply machine
review is part of public trust yet: nothing here is wired into a public
scientific read, no ``include=machine_review`` flag exists, and the public
:class:`~app.services.trust.models.TrustFragment` is untouched.

It reuses the existing private stack end-to-end and adds no new policy::

    submission audit events
      -> audit_adapter (parse + safe submission->record mapping)
      -> RecordMachineReview
      -> read_model latest-selection
      -> MachineReviewRecordSummary

The service is **pure**: it reads only the audit-event-like objects and record
links supplied to it. It performs no persistence, runs no provider, and mutates
nothing — not the scientific records, the deterministic evidence/trust layer,
the human-review layer, nor ``submission.status``.

Anti-fan-out
------------

A submission-level result is never promoted to the requested record. Only a
finding that names the *exact* requested record (mapped by the audit adapter)
contributes to ``latest_summary``; submission-scoped and unlinked findings are
preserved only as diagnostics. Record links are scoped per submission so a
finding from one submission cannot map to a record linked only via a different
submission.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.machine_review.audit_adapter import (
    AuditRecordLink,
    SubmissionAuditEventLike,
    event_is_machine_review,
    record_machine_reviews_from_submission_audit_event,
)
from app.services.machine_review.mapping import UnmappedFinding
from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
    build_machine_review_record_summary,
)


@dataclass(frozen=True)
class MachineReviewInspectionView:
    """Private/admin inspection view for one record's machine-review projection.

    A frozen, read-only snapshot (matching the projection/container convention
    of :class:`~app.services.machine_review.audit_adapter.MachineReviewAuditProjection`
    and the mapping dataclasses): it holds the latest summary plus every record
    review and diagnostic that contributed, so a maintainer can audit *why* a
    record projects the status it does. It carries no field that could mutate
    any scientific, evidence, trust, or moderation state, and being frozen it
    cannot be mutated after construction.

    * ``latest_summary`` — the read-model latest-selection result for exactly
      this record; ``status=not_run`` when no mapped review exists.
    * ``all_record_reviews`` — every review that mapped to *this* record across
      the supplied events (sibling-record reviews are excluded).
    * ``unmapped_findings`` / ``mapping_warnings`` / ``parse_warnings`` —
      diagnostics preserved verbatim from the audit adapter, including
      submission-scoped findings that intentionally did not become a record
      summary.
    * ``source_submission_ids`` — distinct submissions whose audit events
      produced a mapped review for this record (sorted).
    """

    record_type: str
    latest_summary: MachineReviewRecordSummary
    record_ref: str | None = None
    record_id: int | None = None
    all_record_reviews: tuple[RecordMachineReview, ...] = ()
    unmapped_findings: tuple[UnmappedFinding, ...] = ()
    mapping_warnings: tuple[str, ...] = ()
    parse_warnings: tuple[str, ...] = ()
    source_submission_ids: tuple[int, ...] = field(default_factory=tuple)


def _resolve_target_ref(record_ref: str | None, record_id: int | None) -> str:
    """Resolve the matching key the audit-adapter stack addresses records by.

    The stack keys record reviews by the stringified internal ``record_id``
    (the public ref is unavailable at the audit layer). A caller may pass that
    ref directly, or an ``record_id`` to be stringified. At least one is
    required.
    """
    if record_ref is not None:
        return record_ref
    if record_id is not None:
        return str(record_id)
    raise ValueError("Either record_ref or record_id must be supplied.")


def _links_for_submission(
    submission_id: int | None,
    submission_record_links: Sequence[AuditRecordLink],
) -> list[AuditRecordLink]:
    """Scope record links to one submission.

    A link is in scope for the event when it carries no ``submission_id``
    (treated as shared — convenient for lightweight callers/tests) or its
    ``submission_id`` matches the event's. This keeps a finding from one
    submission from mapping to a record linked only via another submission.
    """
    scoped: list[AuditRecordLink] = []
    for link in submission_record_links:
        link_submission_id = getattr(link, "submission_id", None)
        if link_submission_id is None or link_submission_id == submission_id:
            scoped.append(link)
    return scoped


def build_machine_review_inspection_view(
    *,
    record_type: Any,
    record_ref: str | None = None,
    record_id: int | None = None,
    submission_record_links: Sequence[AuditRecordLink] = (),
    submission_audit_events: Sequence[SubmissionAuditEventLike] = (),
) -> MachineReviewInspectionView:
    """Derive the machine-review inspection view for one record.

    Steps (each reuses the existing stack; no new policy is introduced):

    1. Read only the supplied audit events; ignore non-machine-review ones.
    2. Project each machine-review event via the audit adapter, with links
       scoped to that event's submission.
    3. Keep only reviews that mapped to the *exact* requested record.
    4. Build ``latest_summary`` via the read-model helper (``not_run`` when
       none map).
    5. Preserve unmapped findings, mapping warnings, and parse warnings as
       diagnostics.

    Mutates nothing and performs no persistence.
    """
    target_ref = _resolve_target_ref(record_ref, record_id)
    record_type_value = getattr(record_type, "value", record_type)

    all_reviews: list[RecordMachineReview] = []
    unmapped: list[UnmappedFinding] = []
    mapping_warnings: list[str] = []
    parse_warnings: list[str] = []

    for event in submission_audit_events:
        # Step 1: an event that is not an LLM-authored machine-review event
        # contributes nothing (and is not even a diagnostic — it is unrelated).
        if not event_is_machine_review(event):
            continue

        scoped_links = _links_for_submission(
            getattr(event, "submission_id", None), submission_record_links
        )
        projection = record_machine_reviews_from_submission_audit_event(
            event=event, submission_record_links=scoped_links
        )
        all_reviews.extend(projection.record_reviews)
        unmapped.extend(projection.unmapped_findings)
        mapping_warnings.extend(projection.mapping_warnings)
        parse_warnings.extend(projection.parse_warnings)

    # Step 3: exact-record matching only — sibling records never leak in.
    matched_reviews = tuple(
        review
        for review in all_reviews
        if review.record_type == record_type_value
        and review.record_ref == target_ref
    )

    # Step 4: latest selection is the read model's job (tie-break included).
    latest_summary = build_machine_review_record_summary(
        record_type=record_type_value,
        record_ref=target_ref,
        reviews=matched_reviews,
    )

    source_submission_ids = tuple(
        sorted(
            {
                review.submission_id
                for review in matched_reviews
                if review.submission_id is not None
            }
        )
    )

    return MachineReviewInspectionView(
        record_type=record_type_value,
        record_ref=target_ref,
        record_id=record_id,
        latest_summary=latest_summary,
        all_record_reviews=matched_reviews,
        unmapped_findings=tuple(unmapped),
        mapping_warnings=tuple(mapping_warnings),
        parse_warnings=tuple(parse_warnings),
        source_submission_ids=source_submission_ids,
    )


def get_machine_review_summaries_for_record(
    *,
    record_type: Any,
    record_ref: str | None = None,
    record_id: int | None = None,
    submission_record_links: Sequence[AuditRecordLink] = (),
    submission_audit_events: Sequence[SubmissionAuditEventLike] = (),
) -> MachineReviewRecordSummary:
    """Convenience: just the latest machine-review summary for one record.

    Thin wrapper over :func:`build_machine_review_inspection_view` returning
    only ``latest_summary`` — ``status=not_run`` when no mapped review exists.
    """
    return build_machine_review_inspection_view(
        record_type=record_type,
        record_ref=record_ref,
        record_id=record_id,
        submission_record_links=submission_record_links,
        submission_audit_events=submission_audit_events,
    ).latest_summary
