"""Unit tests for the private/admin machine-review inspection service.

These prove ``app/services/machine_review/inspection.py``: given a record
identity plus existing submission audit events and record links, derive the
machine-review summaries that *can* be projected — purely for admin/debugging,
without any persistence, public exposure, or mutation of scientific, evidence,
trust, or moderation state.

The service is pure, so these tests build lightweight event / record-link
stand-ins and the *real* persisted ``details_json`` shape (via
:func:`llm_precheck_result_to_details_json`). The audit adapter, mapping,
read-model, and derivation layers are exercised through the inspection service
end-to-end. The heavy run-time non-interference fixture lives in
``test_machine_review_non_interference.py`` and is not duplicated here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckLabel,
    LLMPrecheckResult,
    llm_precheck_result_to_details_json,
)
from app.services.machine_review import MachineReviewSeverity, MachineReviewStatus
from app.services.machine_review.inspection import (
    MachineReviewInspectionView,
    build_machine_review_inspection_view,
    get_machine_review_summaries_for_record,
)

# A fixed reference instant so tests never depend on wall-clock time.
_T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Lightweight stand-ins (the service only reads attributes; it never writes).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _AuditEvent:
    """Minimal ``SubmissionAuditEvent``-like object."""

    details_json: dict[str, Any] | None
    submission_id: int | None = 5
    event_kind: Any = SubmissionAuditEventKind.llm_precheck_recorded
    actor_kind: Any = SubmissionActorKind.llm
    created_at: datetime | None = _T0


@dataclass(frozen=True)
class _Link:
    """Minimal ``SubmissionRecordLink``-like object (with submission scope)."""

    record_type: Any
    record_id: int
    submission_id: int | None = None


def _details(
    *,
    label: LLMPrecheckLabel = LLMPrecheckLabel.warning,
    findings: tuple[LLMFinding, ...] = (),
    model: str | None = "fake_test/simple-v1",
    provider: str | None = "FakeLLMPrecheckProvider",
) -> dict[str, Any]:
    """Build a realistic persisted ``details_json`` payload."""
    result = LLMPrecheckResult(
        label=label,
        summary="advisory summary",
        findings=findings,
        model=model,
        used_rag=False,
    )
    payload = llm_precheck_result_to_details_json(result)
    if provider is not None:
        payload["provider"] = provider
    return payload


def _record_finding(
    *,
    record_type: str = "calculation",
    record_id: int | None = 1,
    severity: LLMFindingSeverity = LLMFindingSeverity.warning,
) -> LLMFinding:
    """Build one record-addressed precheck finding."""
    return LLMFinding(
        severity=severity,
        category=LLMFindingCategory.provenance,
        record_type=record_type,
        record_id=record_id,
        message="Missing source artifact summary.",
        evidence_keys=("missing_checks.source_artifact_present",),
    )


def _mr_event(
    *,
    label: LLMPrecheckLabel = LLMPrecheckLabel.warning,
    findings: tuple[LLMFinding, ...] = (),
    submission_id: int = 5,
    created_at: datetime | None = _T0,
    model: str | None = "fake_test/simple-v1",
) -> _AuditEvent:
    """Build a machine-review (llm_precheck_recorded / llm) audit event."""
    return _AuditEvent(
        details_json=_details(label=label, findings=findings, model=model),
        submission_id=submission_id,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# Empty / not_run
# --------------------------------------------------------------------------- #


def test_inspection_no_reviews_returns_not_run():
    """No audit events -> a not_run view, not a failure (behavior step 7)."""
    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
        submission_audit_events=[],
    )

    assert isinstance(view, MachineReviewInspectionView)
    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    assert view.source_submission_ids == ()
    assert view.unmapped_findings == ()
    assert view.parse_warnings == ()
    # Record identity is echoed back using the stack's matching key.
    assert view.record_type == "calculation"
    assert view.record_ref == "1"
    assert view.record_id == 1


# --------------------------------------------------------------------------- #
# Event gating
# --------------------------------------------------------------------------- #


def test_inspection_ignores_non_machine_review_events():
    """Non-precheck / non-LLM events contribute nothing — not even diagnostics."""
    non_mr = _AuditEvent(
        details_json=_details(findings=(_record_finding(record_id=1),)),
        event_kind=SubmissionAuditEventKind.submission_created,
        actor_kind=SubmissionActorKind.user,
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
        submission_audit_events=[non_mr],
    )

    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    assert view.unmapped_findings == ()
    assert view.parse_warnings == ()


# --------------------------------------------------------------------------- #
# Exact-record matching only
# --------------------------------------------------------------------------- #


def test_inspection_uses_only_exact_record_matches():
    """Only a finding naming the requested record produces a record review."""
    event = _mr_event(
        findings=(
            _record_finding(record_id=1, severity=LLMFindingSeverity.warning),
            _record_finding(record_id=2, severity=LLMFindingSeverity.critical),
        ),
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[
            _Link(SubmissionRecordType.calculation, 1, submission_id=5),
            _Link(SubmissionRecordType.calculation, 2, submission_id=5),
        ],
        submission_audit_events=[event],
    )

    # Record 1 sees only its own warning, never record 2's critical.
    assert len(view.all_record_reviews) == 1
    assert view.all_record_reviews[0].record_ref == "1"
    assert view.latest_summary.status is MachineReviewStatus.machine_screened_warning
    assert view.latest_summary.highest_severity is MachineReviewSeverity.warning
    assert view.source_submission_ids == (5,)


def test_inspection_sibling_record_findings_do_not_affect_latest_summary():
    """A newer, more-severe sibling-record review never leaks into the target."""
    target = _mr_event(
        findings=(_record_finding(record_id=10, severity=LLMFindingSeverity.info),),
        submission_id=1,
        created_at=_T0,
    )
    sibling = _mr_event(
        label=LLMPrecheckLabel.needs_attention,
        findings=(
            _record_finding(record_id=20, severity=LLMFindingSeverity.critical),
        ),
        submission_id=2,
        created_at=_T0 + timedelta(hours=5),  # strictly newer
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=10,
        submission_record_links=[
            _Link(SubmissionRecordType.calculation, 10, submission_id=1),
            _Link(SubmissionRecordType.calculation, 20, submission_id=2),
        ],
        submission_audit_events=[target, sibling],
    )

    # The sibling's newer critical must not be selected for record 10.
    assert view.latest_summary.status is MachineReviewStatus.machine_screened_pass
    assert view.latest_summary.highest_severity is MachineReviewSeverity.info
    assert view.source_submission_ids == (1,)
    assert all(r.record_ref == "10" for r in view.all_record_reviews)


# --------------------------------------------------------------------------- #
# Anti-fan-out & diagnostics
# --------------------------------------------------------------------------- #


def test_inspection_does_not_fan_out_submission_scoped_findings():
    """A submission-level finding never becomes the requested record's summary."""
    submission_finding = LLMFinding(
        severity=LLMFindingSeverity.critical,
        category=LLMFindingCategory.consistency,
        record_type=None,
        record_id=None,
        message="Submission-level concern across the bundle.",
    )
    event = _mr_event(
        label=LLMPrecheckLabel.needs_attention,
        findings=(submission_finding,),
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        # Two linked records: a fan-out bug would attach the finding to both.
        submission_record_links=[
            _Link(SubmissionRecordType.calculation, 1, submission_id=5),
            _Link(SubmissionRecordType.calculation, 2, submission_id=5),
        ],
        submission_audit_events=[event],
    )

    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    # The submission-scoped finding survives only as a diagnostic.
    assert len(view.unmapped_findings) == 1


def test_inspection_preserves_unmapped_diagnostics():
    """A finding naming an unlinked record is kept as a mapping diagnostic."""
    event = _mr_event(
        findings=(_record_finding(record_id=999),),  # 999 is not linked
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[
            _Link(SubmissionRecordType.calculation, 1, submission_id=5)
        ],
        submission_audit_events=[event],
    )

    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    assert len(view.unmapped_findings) == 1
    assert len(view.mapping_warnings) == 1
    assert "not linked" in view.mapping_warnings[0]


def test_inspection_preserves_parse_warnings():
    """A malformed payload degrades to a preserved parse warning, never a raise."""
    malformed = _AuditEvent(
        details_json={"label": "not-a-real-label", "findings": []},
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1)],
        submission_audit_events=[malformed],
    )

    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    assert len(view.parse_warnings) == 1


# --------------------------------------------------------------------------- #
# Latest selection / failed outcome
# --------------------------------------------------------------------------- #


def test_inspection_latest_summary_uses_read_model_tie_break():
    """Multiple events for one record resolve via the read-model latest helper."""
    older = _mr_event(
        label=LLMPrecheckLabel.needs_attention,
        findings=(_record_finding(record_id=7, severity=LLMFindingSeverity.critical),),
        submission_id=1,
        created_at=_T0,
    )
    newer = _mr_event(
        label=LLMPrecheckLabel.warning,
        findings=(_record_finding(record_id=7, severity=LLMFindingSeverity.warning),),
        submission_id=2,
        created_at=_T0 + timedelta(hours=2),
    )
    links = [_Link(SubmissionRecordType.calculation, 7, submission_id=s) for s in (1, 2)]

    # Input order must not matter; the newer event wins.
    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=7,
        submission_record_links=links,
        submission_audit_events=[newer, older],
    )

    assert view.latest_summary.status is MachineReviewStatus.machine_screened_warning
    assert view.latest_summary.submission_id == 2
    assert view.latest_summary.reviewed_at == _T0 + timedelta(hours=2)
    # Both record-7 reviews are retained for audit; both submissions are sources.
    assert len(view.all_record_reviews) == 2
    assert view.source_submission_ids == (1, 2)


def test_inspection_failed_submission_review_does_not_create_record_review():
    """A failed submission review (no findings) yields no record-level summary."""
    event = _mr_event(
        label=LLMPrecheckLabel.failed_to_review,
        findings=(),
        submission_id=5,
    )

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1, submission_id=5)],
        submission_audit_events=[event],
    )

    # The reviewer-failure axis is submission-scoped; it never fans out to the
    # record, so the record's machine review is simply not_run.
    assert view.latest_summary.status is MachineReviewStatus.not_run
    assert view.all_record_reviews == ()
    assert view.source_submission_ids == ()


# --------------------------------------------------------------------------- #
# Shape / immutability boundary
# --------------------------------------------------------------------------- #


def test_inspection_view_forbids_mutation_payload_fields():
    """The view is frozen, names no mutation/trust/evidence field, and rejects extras."""
    field_names = {f.name for f in dataclasses.fields(MachineReviewInspectionView)}

    # No field hints at a state change or at deterministic-evidence/trust state
    # the inspection service must never own.
    forbidden = (
        "set_",
        "mutation",
        "override",
        "apply",
        "review_status",
        "benchmark_reference",
        "is_certified",
        "evidence_completeness",
        "passed_checks",
        "missing_checks",
        "warning_checks",
        "not_applicable_checks",
        "hard_fail_reason",
        "trust_status",
    )
    for token in forbidden:
        assert not any(token in name for name in field_names), token

    view = build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1, submission_id=5)],
        submission_audit_events=[
            _mr_event(findings=(_record_finding(record_id=1),), submission_id=5)
        ],
    )

    # Frozen: a post-hoc mutation is rejected.
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.latest_summary = None  # type: ignore[misc]

    # extra fields are rejected at construction (no smuggled mutation payload).
    with pytest.raises(TypeError):
        MachineReviewInspectionView(
            record_type="calculation",
            latest_summary=view.latest_summary,
            set_review_status="reviewed",  # type: ignore[call-arg]
        )


def test_inspection_does_not_mutate_inputs():
    """The service reads its inputs only; it never mutates them."""
    details = _details(findings=(_record_finding(record_id=1),))
    details_snapshot = dict(details)
    event = _AuditEvent(details_json=details, submission_id=5)
    links = [_Link(SubmissionRecordType.calculation, 1, submission_id=5)]

    build_machine_review_inspection_view(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=links,
        submission_audit_events=[event],
    )

    assert event.details_json == details_snapshot
    assert links == [_Link(SubmissionRecordType.calculation, 1, submission_id=5)]


# --------------------------------------------------------------------------- #
# Convenience wrapper
# --------------------------------------------------------------------------- #


def test_get_machine_review_summaries_for_record_returns_latest_summary():
    """The convenience wrapper returns exactly the view's latest_summary."""
    event = _mr_event(
        findings=(_record_finding(record_id=1, severity=LLMFindingSeverity.warning),),
        submission_id=5,
    )

    summary = get_machine_review_summaries_for_record(
        record_type=SubmissionRecordType.calculation,
        record_id=1,
        submission_record_links=[_Link(SubmissionRecordType.calculation, 1, submission_id=5)],
        submission_audit_events=[event],
    )

    assert summary.status is MachineReviewStatus.machine_screened_warning
    assert summary.submission_id == 5
