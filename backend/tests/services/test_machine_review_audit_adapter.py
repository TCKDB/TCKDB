"""Unit tests for the private submission-audit -> machine-review adapter.

These prove the bridge in ``app/services/machine_review/audit_adapter.py``:
given a persisted ``submission_audit_event``-like object (an LLM-authored
``llm_precheck_recorded`` event whose ``details_json`` is a provider-contract
dump, the shape the runner records) plus the submission's record links, project
record-addressed findings into internal
:class:`~app.services.machine_review.read_model.RecordMachineReview` objects —
without persistence, public exposure, or any mutation of scientific, evidence,
trust, or moderation state.

The adapter under test is pure, so these tests build lightweight event /
record-link stand-ins and the *real* persisted ``details_json`` shape (via
:func:`machine_review_v2_result_to_details_json`) rather than touching the database.
The mapper, derivation, and read-model layers are exercised through the adapter
end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.services.machine_review import (
    MachineReviewCategory,
    MachineReviewSeverity,
    MachineReviewStatus,
    build_machine_review_record_summary,
)
from app.services.machine_review.audit_adapter import (
    MachineReviewAuditProjection,
    machine_review_result_from_audit_event,
    record_machine_reviews_from_audit_events,
    record_machine_reviews_from_submission_audit_event,
)
from app.services.machine_review.providers.interface import (
    machine_review_v2_result_to_details_json,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewProviderFindingV2,
    MachineReviewProviderResultV2,
)

# A fixed reference instant so tests never depend on wall-clock time.
_T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Lightweight stand-ins (the adapter only reads attributes; it never writes).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _AuditEvent:
    """Minimal ``SubmissionAuditEvent``-like object for the adapter."""

    details_json: dict[str, Any] | None
    event_kind: Any = SubmissionAuditEventKind.llm_precheck_recorded
    actor_kind: Any = SubmissionActorKind.llm
    created_at: datetime | None = _T0
    submission_id: int | None = 5
    id: int | None = None


@dataclass(frozen=True)
class _Link:
    """Minimal ``SubmissionRecordLink``-like object (record_type + record_id)."""

    record_type: Any
    record_id: int


def _details(
    *,
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
    findings: tuple[MachineReviewProviderFindingV2, ...] = (),
    model: str | None = "fake_test/simple-v1",
    summary: str | None = "advisory summary",
    provider: str | None = "FakeLLMPrecheckProvider",
    used_rag: bool = False,
) -> dict[str, Any]:
    """Build a realistic persisted ``details_json`` payload.

    Mirrors ``record_machine_review_v2_audit_event``: a provider-contract dump
    (``schema_version`` marker included) with the optional ``provider`` key.
    ``used_rag`` is set on the dump, since the contract refuses ``True`` at
    construction.
    """
    result = MachineReviewProviderResultV2(
        schema_version=MACHINE_REVIEW_V2_SCHEMA_VERSION,
        status=status,
        summary=summary,
        findings=findings,
        model=model,
        provider=None,
    )
    payload = machine_review_v2_result_to_details_json(result)
    if provider is not None:
        payload["provider"] = provider
    if used_rag:
        payload["used_rag"] = True
    return payload


def _record_finding(
    *,
    record_type: str = "calculation",
    record_id: int | None = 1,
    severity: MachineReviewSeverity = MachineReviewSeverity.warning,
    category: MachineReviewCategory = MachineReviewCategory.provenance,
    message: str = "Missing source artifact summary.",
) -> MachineReviewProviderFindingV2:
    """Build one provider finding, optionally addressing a record by internal id."""
    return MachineReviewProviderFindingV2(
        severity=severity,
        category=category,
        record_type=record_type,
        record_id=record_id,
        message=message,
        evidence_keys=("missing_checks.source_artifact_present",),
    )


# --------------------------------------------------------------------------- #
# Event gating
# --------------------------------------------------------------------------- #


def test_non_machine_review_audit_event_is_ignored():
    """A non-precheck / non-LLM event yields an empty projection, no warnings."""
    # Right payload shape, but the wrong event kind: not a machine-review event.
    event = _AuditEvent(
        details_json=_details(findings=(_record_finding(),)),
        event_kind=SubmissionAuditEventKind.submission_created,
        actor_kind=SubmissionActorKind.user,
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection == MachineReviewAuditProjection()
    assert projection.record_reviews == ()
    assert projection.parse_warnings == ()
    assert projection.mapping_warnings == ()
    assert projection.unmapped_findings == ()


def test_llm_event_with_curator_actor_is_not_machine_review():
    """Even ``llm_precheck_recorded`` is ignored if the actor is not the LLM."""
    event = _AuditEvent(
        details_json=_details(findings=(_record_finding(),)),
        event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
        actor_kind=SubmissionActorKind.curator,
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection == MachineReviewAuditProjection()


# --------------------------------------------------------------------------- #
# Happy path: a record-addressed finding becomes a record review
# --------------------------------------------------------------------------- #


def test_valid_audit_event_maps_record_finding_to_record_review():
    """A finding naming a linked record produces exactly one record review."""
    event = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_warning,
            findings=(_record_finding(record_type="calculation", record_id=42),),
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 42)],
    )

    assert len(projection.record_reviews) == 1
    review = projection.record_reviews[0]
    assert review.record_type == "calculation"
    # The private matching key is the stringified internal id; the real int is
    # carried through as passthrough metadata.
    assert review.record_ref == "42"
    assert review.record_id == 42
    # Status is derived from the (single, warning) record-scoped finding.
    assert review.status is MachineReviewStatus.machine_screened_warning
    assert len(review.findings) == 1
    # Nothing was left unmapped, and no warnings were produced.
    assert projection.unmapped_findings == ()
    assert projection.mapping_warnings == ()
    assert projection.parse_warnings == ()


def test_record_review_carries_source_audit_event_id():
    """The projected review records which audit event it came from (provenance).

    ``audit_event_id`` is read from ``event.id`` and is the read model's primary
    latest-selection tie-break and the per-record history key.
    """
    event = _AuditEvent(
        details_json=_details(
            findings=(_record_finding(record_type="calculation", record_id=42),),
        ),
        id=777,
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 42)],
    )

    assert projection.record_reviews[0].audit_event_id == 777


def test_failed_review_status_with_record_finding_yields_failed_status():
    """A failed reviewer status dominates the record-finding-derived status.

    The payload's ``machine_review_failed`` status is the reviewer-completion signal
    for the pass; even though the finding alone would derive a ``warning``, the
    mapped record's status is ``machine_review_failed`` (event-level outcome,
    not a concern fanned out).
    """
    event = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_review_failed,
            findings=(_record_finding(record_type="calculation", record_id=42),),
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 42)],
    )

    assert len(projection.record_reviews) == 1
    assert (
        projection.record_reviews[0].status
        is MachineReviewStatus.machine_review_failed
    )


# --------------------------------------------------------------------------- #
# Anti-fan-out: submission-scoped and unlinked findings never become reviews
# --------------------------------------------------------------------------- #


def test_submission_scoped_finding_does_not_create_record_review():
    """A finding with no ``record_type`` stays submission-scoped (no fan-out)."""
    submission_finding = MachineReviewProviderFindingV2(
        severity=MachineReviewSeverity.critical,
        category=MachineReviewCategory.consistency,
        record_type=None,
        record_id=None,
        message="Submission-level concern across the bundle.",
    )
    event = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_needs_attention,
            findings=(submission_finding,),
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        # Two linked records: a fan-out bug would attach the finding to both.
        submission_record_links=[
            _Link(SubmissionRecordType.calculation, 1),
            _Link(SubmissionRecordType.calculation, 2),
        ],
    )

    assert projection.record_reviews == ()
    # Submission-scoped is expected, not a defect -> routed to unmapped with no
    # warning text.
    assert len(projection.unmapped_findings) == 1
    assert projection.mapping_warnings == ()


def test_unlinked_record_finding_does_not_create_record_review():
    """A finding naming a record not linked to this submission must not map."""
    event = _AuditEvent(
        details_json=_details(
            findings=(_record_finding(record_type="calculation", record_id=999),),
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        # Linked record is 1, not 999.
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection.record_reviews == ()
    assert len(projection.unmapped_findings) == 1
    assert len(projection.mapping_warnings) == 1
    assert "not linked" in projection.mapping_warnings[0]


# --------------------------------------------------------------------------- #
# Only the current format is read: anything else is refused, not guessed at
# --------------------------------------------------------------------------- #


def test_a_payload_without_the_format_marker_is_an_unknown_format():
    """Kills: a marker-less payload falling back to the retired precheck (v1) reading.

    The retired contract recorded a ``label`` and no marker. Such a payload now
    degrades to no result and a warning naming the missing marker; it is not
    translated, even though every field it has is well-formed.
    """
    legacy = {"label": "warning", "summary": "advisory", "findings": [], "model": "m", "used_rag": False}
    parsed = machine_review_result_from_audit_event(legacy)
    assert parsed.result is None
    assert len(parsed.parse_warnings) == 1 and "schema_version None" in parsed.parse_warnings[0]

    # A current payload with only its marker removed is refused the same way.
    current = _details(findings=(_record_finding(),))
    assert machine_review_result_from_audit_event(current).result is not None
    del current["schema_version"]
    assert machine_review_result_from_audit_event(current).result is None

    projection = record_machine_reviews_from_submission_audit_event(
        event=_AuditEvent(details_json=legacy),
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )
    assert projection.record_reviews == ()
    assert len(projection.parse_warnings) == 1


def test_a_payload_with_another_marker_is_refused_not_read_as_current():
    payload = _details(findings=(_record_finding(),))
    payload["schema_version"] = "machine_review_v3"
    parsed = machine_review_result_from_audit_event(payload)
    assert parsed.result is None
    assert "machine_review_v3" in parsed.parse_warnings[0]


# --------------------------------------------------------------------------- #
# Malformed payloads degrade, they never raise
# --------------------------------------------------------------------------- #


def test_malformed_details_json_returns_parse_warning_not_exception():
    """A malformed payload degrades to no reviews + a parse warning, no raise."""
    # Carries the marker but is not a valid provider payload (status is not in the enum).
    event = _AuditEvent(
        details_json={"schema_version": MACHINE_REVIEW_V2_SCHEMA_VERSION, "status": "not-a-real-status", "findings": []}
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection.record_reviews == ()
    assert len(projection.parse_warnings) == 1


def test_missing_details_json_returns_parse_warning_not_exception():
    """A ``None`` payload is handled the same way: warning, never a raise."""
    event = _AuditEvent(details_json=None)

    projection = record_machine_reviews_from_submission_audit_event(
        event=event, submission_record_links=[]
    )

    assert projection.record_reviews == ()
    assert len(projection.parse_warnings) == 1


def test_rag_payload_is_rejected_as_parse_warning():
    """RAG is a non-goal; a payload claiming RAG degrades, it does not project."""
    event = _AuditEvent(
        details_json=_details(
            findings=(_record_finding(),),
            used_rag=True,
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection.record_reviews == ()
    assert len(projection.parse_warnings) == 1


# --------------------------------------------------------------------------- #
# Provenance: where each field on the projected review comes from
# --------------------------------------------------------------------------- #


def test_reviewed_at_comes_from_audit_event_created_at():
    """``reviewed_at`` on the review is the event's ``created_at``."""
    created = _T0 + timedelta(hours=3)
    event = _AuditEvent(
        details_json=_details(findings=(_record_finding(),)),
        created_at=created,
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection.record_reviews[0].reviewed_at == created


def test_submission_id_comes_from_audit_event():
    """``submission_id`` on the review is the event's ``submission_id``."""
    event = _AuditEvent(
        details_json=_details(findings=(_record_finding(),)),
        submission_id=4321,
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    assert projection.record_reviews[0].submission_id == 4321


def test_model_and_provider_are_preserved():
    """``model`` (from the result) and ``provider`` (event extra) are preserved."""
    event = _AuditEvent(
        details_json=_details(
            findings=(_record_finding(),),
            model="cloud/reviewer-v2",
            provider="OnlineApiProvider",
        ),
    )

    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )

    review = projection.record_reviews[0]
    assert review.model == "cloud/reviewer-v2"
    assert review.provider == "OnlineApiProvider"


# --------------------------------------------------------------------------- #
# Status translation for the non-finding (failed) outcome
# --------------------------------------------------------------------------- #


def test_failed_review_payload_projects_machine_review_failed():
    """A ``machine_review_failed`` payload projects ``machine_review_failed``.

    A failed review carries no findings, so it produces no record review; the
    failure is captured at the result layer (the status axis), which is what
    this asserts.
    """
    parsed = machine_review_result_from_audit_event(
        _details(status=MachineReviewStatus.machine_review_failed, findings=())
    )

    assert parsed.result is not None
    assert parsed.result.status is MachineReviewStatus.machine_review_failed
    assert parsed.parse_warnings == ()

    # End-to-end: a submission-scoped failure never fans out to a record review.
    event = _AuditEvent(
        details_json=_details(status=MachineReviewStatus.machine_review_failed, findings=())
    )
    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )
    assert projection.record_reviews == ()


# --------------------------------------------------------------------------- #
# Multi-event composition into the latest-record summary
# --------------------------------------------------------------------------- #


def test_multiple_audit_events_can_build_latest_record_summary():
    """Reviews from several events feed the read model's latest-wins summary."""
    older = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_needs_attention,
            findings=(
                _record_finding(
                    record_id=7, severity=MachineReviewSeverity.critical
                ),
            ),
        ),
        created_at=_T0,
        submission_id=1,
    )
    newer = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_warning,
            findings=(
                _record_finding(
                    record_id=7, severity=MachineReviewSeverity.warning
                ),
            ),
        ),
        created_at=_T0 + timedelta(hours=2),
        submission_id=2,
    )
    links = [_Link(SubmissionRecordType.calculation, 7)]

    reviews = record_machine_reviews_from_audit_events(
        events_with_links=[(older, links), (newer, links)]
    )
    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="7", reviews=reviews
    )

    # The newer event wins regardless of input order.
    assert summary.status is MachineReviewStatus.machine_screened_warning
    assert summary.highest_severity is MachineReviewSeverity.warning
    assert summary.findings_count == 1
    assert summary.submission_id == 2
    assert summary.reviewed_at == _T0 + timedelta(hours=2)


def test_sibling_record_finding_does_not_affect_selected_record_summary():
    """A newer review for a sibling record never leaks into another's summary."""
    target_event = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_pass,
            findings=(),  # a clean pass on the target record
        ),
        created_at=_T0,
        submission_id=1,
    )
    sibling_event = _AuditEvent(
        details_json=_details(
            status=MachineReviewStatus.machine_screened_needs_attention,
            findings=(
                _record_finding(
                    record_id=20, severity=MachineReviewSeverity.critical
                ),
            ),
        ),
        # Strictly newer; would win if record scope leaked.
        created_at=_T0 + timedelta(hours=5),
        submission_id=2,
    )

    reviews = record_machine_reviews_from_audit_events(
        events_with_links=[
            (target_event, [_Link(SubmissionRecordType.calculation, 10)]),
            (sibling_event, [_Link(SubmissionRecordType.calculation, 20)]),
        ]
    )

    # A clean pass has no findings, so the target produced no record review;
    # the sibling's review must not be selected for record 10.
    summary_target = build_machine_review_record_summary(
        record_type="calculation", record_ref="10", reviews=reviews
    )
    assert summary_target.status is MachineReviewStatus.not_run

    # And the sibling's own summary is unaffected by the target.
    summary_sibling = build_machine_review_record_summary(
        record_type="calculation", record_ref="20", reviews=reviews
    )
    assert summary_sibling.status is MachineReviewStatus.machine_screened_needs_attention
    assert summary_sibling.submission_id == 2


# --------------------------------------------------------------------------- #
# Boundary: the adapter touches no scientific / evidence / trust / moderation state
# --------------------------------------------------------------------------- #


def test_projection_shape_carries_no_forbidden_state():
    """The projection and its reviews expose no mutation/trust/evidence field.

    Adapter-specific boundary coverage: the deterministic-evidence /
    moderation non-interference invariants are already proven in
    ``test_machine_review_non_interference.py``; here we only assert the
    projection's *shape* cannot smuggle any of that state.
    """
    forbidden = (
        "review_status",
        "benchmark_reference",
        "is_certified",
        "evidence_completeness",
        "checks",
        "hard_fail_reason",
        "trust_status",
        "status",  # submission.status specifically — guarded below by context
    )

    projection_fields = set(MachineReviewAuditProjection.__dataclass_fields__)
    # The projection holds only review/diagnostic collections — no scalar state.
    assert projection_fields == {
        "record_reviews",
        "unmapped_findings",
        "mapping_warnings",
        "parse_warnings",
    }

    event = _AuditEvent(details_json=_details(findings=(_record_finding(),)))
    projection = record_machine_reviews_from_submission_audit_event(
        event=event,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
    )
    review_fields = set(projection.record_reviews[0].__dataclass_fields__)
    # The only ``status`` present is the machine-review axis, never moderation
    # or human-review status; none of the deterministic-evidence fields appear.
    for name in forbidden:
        if name == "status":
            continue
        assert name not in projection_fields
        assert name not in review_fields


def test_adapter_does_not_mutate_input_links_or_event():
    """The adapter reads its inputs only; it never mutates them."""
    details = _details(findings=(_record_finding(),))
    details_snapshot = dict(details)
    event = _AuditEvent(details_json=details)
    links = [_Link(SubmissionRecordType.calculation, 1)]

    record_machine_reviews_from_submission_audit_event(
        event=event, submission_record_links=links
    )

    # Inputs are untouched.
    assert event.details_json == details_snapshot
    assert links == [_Link(SubmissionRecordType.calculation, 1)]
