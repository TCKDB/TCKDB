"""Private adapter: submission audit events -> record-scoped machine reviews.

This module is the bridge from **current persistence** —
``submission_audit_event.details_json`` written by the optional AI Review
Assistant (``event_kind=llm_precheck_recorded``, ``actor_kind=llm``) — to the
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

1. **Parse / translate** (:func:`machine_review_result_from_audit_event`): the
   untrusted ``details_json`` is validated against the *existing* persisted
   contract (:class:`~app.services.llm_precheck.schemas.LLMPrecheckResult`) and
   translated into the machine-review contract
   (:class:`~app.services.machine_review.schemas.MachineReviewResult`). The
   precheck ``label`` becomes a machine-review ``status``; precheck finding
   categories/severities map onto the machine-review vocabulary. Malformed or
   non-projectable payloads degrade to *no result + a parse warning* — they
   never raise.

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

Precheck findings address records by internal ``record_id`` (the public
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
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.machine_review.mapping import (
    SubmissionRecordLinkRef,
    UnmappedFinding,
    map_findings_to_submission_records,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewProviderResultV2,
    MachineReviewResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)

# Only an LLM-authored precheck-recorded event carries a machine-review payload.
# Both conditions must hold: a non-LLM actor or any other event kind is not a
# machine-review event and is ignored without warning.
_MACHINE_REVIEW_EVENT_KIND = SubmissionAuditEventKind.llm_precheck_recorded
_MACHINE_REVIEW_ACTOR_KIND = SubmissionActorKind.llm

# Translation from the persisted precheck label vocabulary to the machine-review
# status vocabulary (spec §3/§5). ``failed_to_review`` is the reviewer-failure
# axis -> ``machine_review_failed``; it is never a record failure.
_LABEL_TO_STATUS: dict[LLMPrecheckLabel, MachineReviewStatus] = {
    LLMPrecheckLabel.not_run: MachineReviewStatus.not_run,
    LLMPrecheckLabel.pass_: MachineReviewStatus.machine_screened_pass,
    LLMPrecheckLabel.warning: MachineReviewStatus.machine_screened_warning,
    LLMPrecheckLabel.needs_attention: (
        MachineReviewStatus.machine_screened_needs_attention
    ),
    LLMPrecheckLabel.failed_to_review: MachineReviewStatus.machine_review_failed,
}

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


def _machine_review_finding_from_precheck(
    finding: LLMFinding,
) -> MachineReviewFinding:
    """Translate one precheck finding into the machine-review contract.

    The precheck severity/category vocabularies are subsets of the
    machine-review ones, so they map by value. The internal ``record_id`` is
    rendered as the private ``record_ref`` matching key (see module docstring);
    a finding with no ``record_id`` carries no ``record_ref`` and stays
    submission-scoped at mapping time. Raises :class:`ValueError` /
    :class:`ValidationError` if a field cannot be projected, which the caller
    converts into a parse warning.
    """
    return MachineReviewFinding(
        severity=MachineReviewSeverity(finding.severity.value),
        category=MachineReviewCategory(finding.category.value),
        record_type=finding.record_type,
        record_ref=(
            str(finding.record_id) if finding.record_id is not None else None
        ),
        message=finding.message,
        evidence_keys=finding.evidence_keys,
    )


def _machine_review_result_from_precheck(
    precheck: LLMPrecheckResult,
) -> MachineReviewResult:
    """Project a validated precheck result onto the machine-review contract.

    The precheck ``label`` becomes a machine-review ``status``; findings are
    translated one-for-one. ``used_rag`` is passed through so the contract's
    ``Literal[False]`` constraint rejects any payload claiming RAG (a non-goal)
    — that rejection surfaces as a parse warning, never an exception, in the
    high-level adapter.
    """
    return MachineReviewResult(
        status=_LABEL_TO_STATUS[precheck.label],
        summary=precheck.summary,
        findings=tuple(
            _machine_review_finding_from_precheck(f) for f in precheck.findings
        ),
        model=precheck.model,
        used_rag=precheck.used_rag,
    )


def _machine_review_finding_from_v2(
    finding: Any,
) -> MachineReviewFinding:
    """Convert one v2 provider finding into the internal machine-review finding.

    v2 findings already speak the machine-review vocabulary, so this is a
    field copy. A finding that cites only ``record_id`` (no ``record_ref``)
    gets ``record_ref = str(record_id)`` — the same internal-id mapping key the
    v1 path uses (module docstring).
    """
    record_ref = finding.record_ref
    if record_ref is None and finding.record_id is not None:
        record_ref = str(finding.record_id)
    return MachineReviewFinding(
        severity=finding.severity,
        category=finding.category,
        record_type=finding.record_type,
        record_ref=record_ref,
        message=finding.message,
        evidence_keys=finding.evidence_keys,
        recommended_action=finding.recommended_action,
    )


def _machine_review_result_from_v2(
    v2: MachineReviewProviderResultV2,
) -> MachineReviewResult:
    """Project a validated v2 provider payload onto the internal contract.

    No label->status translation: v2 carries the machine-review ``status`` and
    ``curator_priority`` natively. Findings are converted one-for-one. The
    v2-only ``provider`` field is handled by the caller (it is not part of the
    internal :class:`MachineReviewResult`).
    """
    return MachineReviewResult(
        status=v2.status,
        curator_priority=v2.curator_priority,
        summary=v2.summary,
        findings=tuple(_machine_review_finding_from_v2(f) for f in v2.findings),
        model=v2.model,
        used_rag=v2.used_rag,
    )


def machine_review_result_from_audit_event(
    details_json: Any,
) -> ParsedMachineReviewPayload:
    """Validate and translate one audit event's ``details_json`` payload.

    Treats ``details_json`` as **untrusted** and dispatches on the root
    ``schema_version`` marker (machine_review_provider_contract_v2.md §3/§7):

    * ``schema_version == "machine_review_v2"`` -> validate the v2 provider
      contract directly and project it (no label->status translation). The
      v2 ``provider`` field, when present, takes precedence over the sibling
      ``details_json["provider"]`` key.
    * ``schema_version`` **absent** -> legacy v1 path: validate against the
      precheck contract and translate.
    * any **other** ``schema_version`` value -> a parse warning (an unknown
      future version is not silently treated as v1).

    Any validation/projection failure degrades to ``result=None`` plus a parse
    warning — this function never raises on malformed input. The optional
    ``provider`` sibling key is read out separately (the v1 precheck contract
    has no such field).
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
    if schema_version is not None:
        return _parse_versioned_payload(
            details_json, schema_version, sibling_provider
        )

    # Legacy v1 path: no version marker.
    try:
        precheck = LLMPrecheckResult.model_validate(details_json)
    except ValidationError:
        return ParsedMachineReviewPayload(
            result=None,
            provider=sibling_provider,
            parse_warnings=(
                "machine-review audit payload failed precheck-contract validation.",
            ),
        )

    try:
        result = _machine_review_result_from_precheck(precheck)
    except (ValidationError, ValueError):
        return ParsedMachineReviewPayload(
            result=None,
            provider=sibling_provider,
            parse_warnings=(
                "machine-review audit payload could not be projected onto the "
                "machine-review contract.",
            ),
        )

    return ParsedMachineReviewPayload(result=result, provider=sibling_provider)


def _parse_versioned_payload(
    details_json: dict[str, Any],
    schema_version: Any,
    sibling_provider: str | None,
) -> ParsedMachineReviewPayload:
    """Handle a payload carrying an explicit ``schema_version`` marker.

    Currently only ``machine_review_v2`` is recognised; any other value is an
    unknown (e.g. future) version and degrades to a parse warning rather than
    falling back to the v1 path.
    """
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
        v2 = MachineReviewProviderResultV2.model_validate(details_json)
    except ValidationError:
        return ParsedMachineReviewPayload(
            result=None,
            provider=sibling_provider,
            parse_warnings=(
                "machine-review v2 audit payload failed contract validation.",
            ),
        )

    # v2 carries provider as a first-class field; prefer it, else the sibling.
    provider = v2.provider if v2.provider is not None else sibling_provider

    try:
        result = _machine_review_result_from_v2(v2)
    except (ValidationError, ValueError):
        return ParsedMachineReviewPayload(
            result=None,
            provider=provider,
            parse_warnings=(
                "machine-review v2 audit payload could not be projected onto "
                "the machine-review contract.",
            ),
        )

    return ParsedMachineReviewPayload(result=result, provider=provider)


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
    )

    reviewed_at = getattr(event, "created_at", None)
    submission_id = getattr(event, "submission_id", None)
    record_reviews = tuple(
        RecordMachineReview.from_mapped_record(
            mapped,
            reviewed_at=reviewed_at,
            model=parsed.result.model,
            provider=parsed.provider,
            submission_id=submission_id,
            curator_priority=parsed.result.curator_priority,
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
