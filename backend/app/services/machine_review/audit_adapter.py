"""Private adapter: submission audit events -> record-scoped machine reviews.

This module is the bridge from **current persistence** —
``submission_audit_event.details_json`` written by the machine-review runner
(``event_kind=llm_precheck_recorded``, ``actor_kind=llm``) — to the
**future internal** machine-review projections
(:class:`~app.services.machine_review.read_model.RecordMachineReview`,
:class:`~app.services.machine_review.read_model.MachineReviewRecordSummary`).

It is **private plumbing only** (spec
``backend/docs/specs/provisional_machine_review.md`` §5/§6). It performs no
persistence, runs no provider, exposes nothing through any public scientific
read API, and never mutates submission status, scientific records, the
deterministic evidence/trust layer, or the human-review layer. Its output is a
read-only projection a future ``trust.machine_review`` fragment *could* render
— prepared here, deliberately not wired anywhere.

Three responsibilities, kept separate so each is independently testable:

1. **Parse** (:func:`machine_review_result_from_audit_event`): the
   untrusted ``details_json`` is validated against the provider contract
   (:class:`~app.services.machine_review.schemas.MachineReviewProviderResultV2`)
   and projected onto the machine-review contract
   (:class:`~app.services.machine_review.schemas.MachineReviewResult`). A
   payload without the contract's ``schema_version`` marker, or with another
   one, is refused: *no result + a parse warning*. Malformed or non-projectable
   payloads degrade the same way — they never raise.

2. **Map** (delegated to
   :func:`~app.services.machine_review.mapping.map_findings_to_submission_records`):
   record-addressed findings map only to the *exact* linked record they name.
   A submission-level result is **never** fanned out across every linked
   record (spec §6/§13). The mapper is the single source of truth for this.

3. **Project** (:func:`record_machine_reviews_from_submission_audit_event`):
   each mapped record becomes a
   :class:`~app.services.machine_review.read_model.RecordMachineReview` stamped
   with the event's ``created_at`` (``reviewed_at``), ``submission_id``, and the
   payload's ``model`` / ``provider``.

Internal-id addressing
----------------------

Findings address records by internal ``record_id`` (the public
``record_ref`` is not available at this layer), so this adapter uses the
**stringified internal ``record_id`` as the mapper's matching key** and carries
the real ``record_id`` int through as passthrough metadata. This is the
internal-id variant the mapping module's docstring anticipates ("the same
algorithm with the key field swapped"). The stringified id is never surfaced
publicly — this whole module is private — so the internal-id policy is not
violated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.services.machine_review.derivation import MachineReviewOutcome
from app.services.machine_review.mapping import (
    SubmissionRecordLinkRef,
    UnmappedFinding,
    map_findings_to_submission_records,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewFinding,
    MachineReviewProviderResultV2,
    MachineReviewResult,
    MachineReviewStatus,
)

# Only an LLM-authored precheck-recorded event carries a machine-review payload.
# Both conditions must hold: a non-LLM actor or any other event kind is not a
# machine-review event and is ignored without warning.
_MACHINE_REVIEW_EVENT_KIND = SubmissionAuditEventKind.llm_precheck_recorded
_MACHINE_REVIEW_ACTOR_KIND = SubmissionActorKind.llm

_KNOWN_RECORD_TYPES: frozenset[str] = frozenset(t.value for t in SubmissionRecordType)


@runtime_checkable
class SubmissionAuditEventLike(Protocol):
    """Structural type for one audit event the adapter can read.

    Satisfied by the ORM :class:`~app.db.models.submission.SubmissionAuditEvent`
    and by lightweight test stand-ins alike. The adapter reads these fields
    only; it never writes them, and accepts enum *or* string for the kind
    fields so callers are not forced to import the enums.
    """

    @property
    def event_kind(self) -> Any: ...

    @property
    def actor_kind(self) -> Any: ...

    @property
    def details_json(self) -> dict[str, Any] | None: ...

    @property
    def created_at(self) -> datetime | None: ...

    @property
    def submission_id(self) -> int | None: ...


@runtime_checkable
class AuditRecordLink(Protocol):
    """Structural type for one submission -> record link the adapter maps against.

    Deliberately requires only ``(record_type, record_id)`` so the ORM
    :class:`~app.db.models.submission.SubmissionRecordLink` (which carries no
    resolved ``record_ref``) satisfies it directly. The adapter resolves the
    matching key from ``record_id`` itself; no public ref is needed here.
    """

    @property
    def record_type(self) -> Any: ...

    @property
    def record_id(self) -> int: ...


@dataclass(frozen=True)
class ParsedMachineReviewPayload:
    """Outcome of validating/translating one ``details_json`` payload.

    ``result`` is the machine-review-contract projection of the payload, or
    ``None`` when the payload was missing, malformed, or not projectable onto
    the contract (in which case ``parse_warnings`` explains why). ``provider``
    is the optional provider name the precheck recorder attached to the event.
    """

    result: MachineReviewResult | None
    provider: str | None = None
    parse_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineReviewAuditProjection:
    """Private projection of one audit event into record-scoped machine reviews.

    ``record_reviews`` are the per-record machine-review passes safely derived
    from the event. ``unmapped_findings`` (with ``mapping_warnings``) and
    ``parse_warnings`` preserve everything that did *not* become a record review
    — submission-scoped findings, findings for unlinked records, and payloads
    that failed to parse — purely for admin diagnostics. The projection carries
    no field that could mutate any scientific, evidence, trust, or moderation
    state.
    """

    record_reviews: tuple[RecordMachineReview, ...] = ()
    unmapped_findings: tuple[UnmappedFinding, ...] = ()
    mapping_warnings: tuple[str, ...] = ()
    parse_warnings: tuple[str, ...] = field(default_factory=tuple)


def event_is_machine_review(event: SubmissionAuditEventLike) -> bool:
    """True iff the event is an LLM-authored machine-review (precheck) event.

    Compares by enum *value* so an ORM enum instance and a plain string both
    work. Any other event kind or actor is not a machine-review event.
    """
    event_kind = getattr(event, "event_kind", None)
    actor_kind = getattr(event, "actor_kind", None)
    event_kind_value = getattr(event_kind, "value", event_kind)
    actor_kind_value = getattr(actor_kind, "value", actor_kind)
    return (
        event_kind_value == _MACHINE_REVIEW_EVENT_KIND.value
        and actor_kind_value == _MACHINE_REVIEW_ACTOR_KIND.value
    )


def machine_review_result_from_audit_event(
    details_json: Any,
) -> ParsedMachineReviewPayload:
    """Validate and project one audit event's ``details_json`` payload.

    Treats ``details_json`` as **untrusted**. The payload must carry the
    provider contract's root ``schema_version`` marker
    (:data:`~app.services.machine_review.schemas.MACHINE_REVIEW_V2_SCHEMA_VERSION`);
    it is then validated against the provider contract and projected onto the
    internal contract (no label->status translation: the payload carries the
    machine-review ``status`` and ``curator_priority`` natively). The payload's
    ``provider`` field, when present, takes precedence over the sibling
    ``details_json["provider"]`` key.

    A payload with **no** marker, or any marker other than the one this release
    reads, is an unknown format: ``result=None`` plus a parse warning. Nothing is
    guessed about its shape.

    Any validation/projection failure degrades to ``result=None`` plus a parse
    warning; this function never raises on malformed input.
    """
    if not isinstance(details_json, dict):
        return ParsedMachineReviewPayload(
            result=None,
            parse_warnings=(
                "machine-review audit payload is missing or not an object.",
            ),
        )

    sibling_provider = details_json.get("provider")
    sibling_provider = sibling_provider if isinstance(sibling_provider, str) else None

    schema_version = details_json.get("schema_version")
    if schema_version != MACHINE_REVIEW_V2_SCHEMA_VERSION:
        return ParsedMachineReviewPayload(
            result=None,
            provider=sibling_provider,
            parse_warnings=(
                "machine-review audit payload has unknown schema_version "
                f"{schema_version!r}.",
            ),
        )

    try:
        payload = MachineReviewProviderResultV2.model_validate(details_json)
    except ValidationError:
        return ParsedMachineReviewPayload(
            result=None,
            provider=sibling_provider,
            parse_warnings=(
                "machine-review audit payload failed contract validation.",
            ),
        )

    # The payload carries provider as a first-class field; prefer it, else the sibling.
    provider = payload.provider if payload.provider is not None else sibling_provider

    try:
        result = MachineReviewResult(
            status=payload.status,
            curator_priority=payload.curator_priority,
            summary=payload.summary,
            findings=tuple(
                MachineReviewFinding(
                    severity=f.severity,
                    category=f.category,
                    record_type=f.record_type,
                    # A finding that cites only ``record_id`` gets the stringified
                    # internal id as its mapping key (module docstring).
                    record_ref=(
                        f.record_ref
                        if f.record_ref is not None or f.record_id is None
                        else str(f.record_id)
                    ),
                    message=f.message,
                    evidence_keys=f.evidence_keys,
                    recommended_action=f.recommended_action,
                )
                for f in payload.findings
            ),
            model=payload.model,
            used_rag=payload.used_rag,
        )
    except (ValidationError, ValueError):
        return ParsedMachineReviewPayload(
            result=None,
            provider=provider,
            parse_warnings=(
                "machine-review audit payload could not be projected onto "
                "the machine-review contract.",
            ),
        )

    return ParsedMachineReviewPayload(result=result, provider=provider)


def _outcome_from_status(status: MachineReviewStatus) -> MachineReviewOutcome:
    """Map a result-level machine-review status to a reviewer-completion outcome.

    The status the payload carries is the reviewer's own verdict for the *pass*. Only its
    two reviewer-completion values are event-level signals the mapper honors:

    * ``machine_review_failed`` -> ``failed`` (the reviewer could not complete);
    * ``not_run`` -> ``not_performed`` (the reviewer did not run);
    * every other (concern) status -> ``completed``: the per-record status is
      then derived from each record's own findings, never inherited from this
      submission-level verdict (anti-fan-out).
    """
    if status is MachineReviewStatus.machine_review_failed:
        return MachineReviewOutcome.failed
    if status is MachineReviewStatus.not_run:
        return MachineReviewOutcome.not_performed
    return MachineReviewOutcome.completed


def _link_refs_from_links(
    submission_record_links: Sequence[AuditRecordLink],
) -> list[SubmissionRecordLinkRef]:
    """Adapt raw record links to the mapper's ref-addressed link type.

    The matching key is the stringified internal ``record_id`` (see module
    docstring); the real ``record_id`` int is carried through as passthrough
    metadata. Links with no ``record_id`` cannot be addressed and are dropped.
    """
    refs: list[SubmissionRecordLinkRef] = []
    for link in submission_record_links:
        record_id = getattr(link, "record_id", None)
        if record_id is None:
            continue
        record_type = getattr(link, "record_type", None)
        record_type_value = getattr(record_type, "value", record_type)
        refs.append(
            SubmissionRecordLinkRef(
                record_type=record_type_value,
                record_ref=str(record_id),
                record_id=record_id,
            )
        )
    return refs


def record_machine_reviews_from_submission_audit_event(
    *,
    event: SubmissionAuditEventLike,
    submission_record_links: Sequence[AuditRecordLink],
) -> MachineReviewAuditProjection:
    """Project one submission audit event into record-scoped machine reviews.

    Pipeline (each step degrades safely, never raises):

    1. Non-machine-review events (wrong kind/actor) -> empty projection, no
       warnings.
    2. Malformed/non-projectable ``details_json`` -> empty projection with a
       parse warning.
    3. Record-addressed findings are mapped to *exactly* their linked record
       via :func:`map_findings_to_submission_records`; submission-level results
       never fan out across linked records.
    4. Each mapped record becomes a :class:`RecordMachineReview` stamped with
       the event's ``created_at`` (``reviewed_at``), ``submission_id``, and the
       payload's ``model`` / ``provider``.

    Findings that do not map (submission-scoped, missing ref, unknown type,
    unlinked record) are preserved in ``unmapped_findings`` /
    ``mapping_warnings`` for diagnostics. This function mutates nothing.
    """
    if not event_is_machine_review(event):
        return MachineReviewAuditProjection()

    parsed = machine_review_result_from_audit_event(
        getattr(event, "details_json", None)
    )
    if parsed.result is None:
        return MachineReviewAuditProjection(parse_warnings=parsed.parse_warnings)

    mapping = map_findings_to_submission_records(
        findings=parsed.result.findings,
        submission_record_links=_link_refs_from_links(submission_record_links),
        # The payload's own status is the reviewer-completion signal for this
        # pass; failed/not_run dominate per-record status, concern statuses defer
        # to each record's findings (anti-fan-out). See ``_outcome_from_status``.
        outcome=_outcome_from_status(parsed.result.status),
    )

    reviewed_at = getattr(event, "created_at", None)
    submission_id = getattr(event, "submission_id", None)
    audit_event_id = getattr(event, "id", None)
    record_reviews = tuple(
        RecordMachineReview.from_mapped_record(
            mapped,
            reviewed_at=reviewed_at,
            model=parsed.result.model,
            provider=parsed.provider,
            submission_id=submission_id,
            curator_priority=parsed.result.curator_priority,
            audit_event_id=audit_event_id,
        )
        for mapped in mapping.mapped_by_record.values()
    )

    return MachineReviewAuditProjection(
        record_reviews=record_reviews,
        unmapped_findings=mapping.unmapped_findings,
        mapping_warnings=mapping.mapping_warnings,
        parse_warnings=parsed.parse_warnings,
    )


def record_machine_reviews_from_audit_events(
    *,
    events_with_links: Sequence[
        tuple[SubmissionAuditEventLike, Sequence[AuditRecordLink]]
    ],
) -> tuple[RecordMachineReview, ...]:
    """Collect record-scoped reviews from many ``(event, links)`` pairs.

    Each pair is projected independently via
    :func:`record_machine_reviews_from_submission_audit_event` and the resulting
    :class:`RecordMachineReview` objects are concatenated in input order. The
    caller feeds these to
    :func:`~app.services.machine_review.read_model.build_machine_review_record_summary`
    to pick the latest review per record. Non-machine-review events and
    unmappable findings simply contribute nothing.
    """
    reviews: list[RecordMachineReview] = []
    for event, links in events_with_links:
        projection = record_machine_reviews_from_submission_audit_event(
            event=event, submission_record_links=links
        )
        reviews.extend(projection.record_reviews)
    return tuple(reviews)
