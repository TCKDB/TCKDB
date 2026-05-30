"""Unit tests for the internal latest-machine-review read model.

These prove the read-only projection in
``app/services/machine_review/read_model.py``: given zero or more
machine-review passes whose findings were already mapped to one scientific
record, compute the *latest* machine-review summary a future public
``trust.machine_review`` fragment (spec §4/§10) could render — without any
persistence, public exposure, or mutation of scientific/trust state.

The read model under test is pure: tests build
:class:`RecordMachineReview` wrappers directly (and, where the
submission-scope boundary is at issue, drive the real mapper and wrap its
output via :meth:`RecordMachineReview.from_mapped_record`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.machine_review import (
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewSeverity,
    MachineReviewStatus,
    SubmissionRecordLinkRef,
    build_machine_review_record_summary,
    map_findings_to_submission_records,
    select_latest_machine_review_for_record,
)
from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
)

# A fixed reference instant so tests never depend on wall-clock time.
_T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def _finding(
    severity: MachineReviewSeverity,
    *,
    record_type: str | None = None,
    record_ref: str | None = None,
    message: str = "advisory finding",
    category: MachineReviewCategory = MachineReviewCategory.consistency,
) -> MachineReviewFinding:
    """Build a minimal valid finding, optionally addressing a record."""
    return MachineReviewFinding(
        severity=severity,
        category=category,
        record_type=record_type,
        record_ref=record_ref,
        message=message,
    )


def _review(
    *,
    record_type: str = "calculation",
    record_ref: str = "calc_aaa",
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_pass,
    findings: tuple[MachineReviewFinding, ...] = (),
    reviewed_at: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
    submission_id: int | None = None,
    record_id: int | None = None,
) -> RecordMachineReview:
    """Build a record-scoped review pass wrapper."""
    return RecordMachineReview(
        record_type=record_type,
        record_ref=record_ref,
        status=status,
        findings=findings,
        reviewed_at=reviewed_at,
        model=model,
        provider=provider,
        submission_id=submission_id,
        record_id=record_id,
    )


# --------------------------------------------------------------------------- #
# Empty input
# --------------------------------------------------------------------------- #


def test_no_reviews_returns_not_run_summary():
    """No review for the record -> a not_run summary, not a failure (rule 1)."""
    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[]
    )

    assert isinstance(summary, MachineReviewRecordSummary)
    assert summary.status is MachineReviewStatus.not_run
    assert summary.findings_count == 0
    assert summary.highest_severity is None
    assert summary.model is None
    assert summary.provider is None
    assert summary.reviewed_at is None
    assert summary.submission_id is None
    assert summary.curator_priority is None


# --------------------------------------------------------------------------- #
# Latest-wins selection
# --------------------------------------------------------------------------- #


def test_latest_review_by_reviewed_at_wins():
    """The newest review by reviewed_at drives the summary (policy step 2)."""
    older = _review(
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(_finding(MachineReviewSeverity.critical),),
        reviewed_at=_T0,
        model="old/model",
        provider="old-provider",
        submission_id=1,
    )
    newer = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(_finding(MachineReviewSeverity.warning),),
        reviewed_at=_T0 + timedelta(hours=1),
        model="new/model",
        provider="new-provider",
        submission_id=2,
    )

    # Order in the input must not matter; the timestamp decides.
    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[newer, older]
    )

    assert summary.status is MachineReviewStatus.machine_screened_warning
    assert summary.model == "new/model"
    assert summary.provider == "new-provider"
    assert summary.reviewed_at == _T0 + timedelta(hours=1)
    assert summary.submission_id == 2


def test_tie_break_is_deterministic():
    """Identical reviewed_at values are resolved by submission_id first, then
    input order, so latest-selection is deterministic: the higher submission_id
    wins (step 3)."""
    low = _review(
        status=MachineReviewStatus.machine_screened_pass,
        reviewed_at=_T0,
        model="sub-7/model",
        submission_id=7,
    )
    high = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(_finding(MachineReviewSeverity.warning),),
        reviewed_at=_T0,
        model="sub-9/model",
        submission_id=9,
    )

    # Same timestamp; the higher submission_id (9) must be selected regardless
    # of input order, and the choice must be stable across calls.
    a = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[low, high]
    )
    b = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[high, low]
    )

    assert a == b
    assert a.submission_id == 9
    assert a.model == "sub-9/model"
    assert a.status is MachineReviewStatus.machine_screened_warning


def test_tie_break_falls_through_to_input_order():
    """Identical reviewed_at values are resolved by submission_id first, then
    input order, so latest-selection is deterministic: with submission_id also
    tied (both None), the later input position wins (step 3)."""
    first = _review(reviewed_at=_T0, model="first", submission_id=None)
    second = _review(reviewed_at=_T0, model="second", submission_id=None)

    selected = select_latest_machine_review_for_record(
        record_type="calculation", record_ref="calc_aaa", reviews=[first, second]
    )

    assert selected is second
    assert selected.model == "second"


# --------------------------------------------------------------------------- #
# Findings count / severity use only the selected review
# --------------------------------------------------------------------------- #


def test_findings_count_uses_only_selected_record_findings():
    """findings_count reflects the selected review only, not earlier passes (step 5)."""
    older = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _finding(MachineReviewSeverity.warning),
            _finding(MachineReviewSeverity.info),
            _finding(MachineReviewSeverity.info),
        ),
        reviewed_at=_T0,
        submission_id=1,
    )
    newer = _review(
        status=MachineReviewStatus.machine_screened_pass,
        findings=(_finding(MachineReviewSeverity.info),),
        reviewed_at=_T0 + timedelta(hours=1),
        submission_id=2,
    )

    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[older, newer]
    )

    # The newer review has exactly one finding; the older review's three are
    # not aggregated in.
    assert summary.findings_count == 1


def test_highest_severity_uses_only_selected_record_findings():
    """highest_severity is computed from the selected review only (step 6)."""
    older = _review(
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(_finding(MachineReviewSeverity.critical),),
        reviewed_at=_T0,
        submission_id=1,
    )
    newer = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _finding(MachineReviewSeverity.info),
            _finding(MachineReviewSeverity.warning),
        ),
        reviewed_at=_T0 + timedelta(hours=1),
        submission_id=2,
    )

    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[older, newer]
    )

    # Selected (newer) tops out at warning; the older review's critical does
    # not bleed into the highest severity.
    assert summary.highest_severity is MachineReviewSeverity.warning


# --------------------------------------------------------------------------- #
# Scope isolation: submission-scoped findings and sibling records
# --------------------------------------------------------------------------- #


def test_submission_scoped_findings_are_not_included():
    """Submission-scoped findings never reach a record summary (policy step 7).

    Drives the real mapper so the boundary is exercised end-to-end: a
    submission-scoped finding (no record_type) plus a record-addressed finding
    go in; only the record-addressed one survives into the wrapped review and
    therefore into the summary.
    """
    links = [SubmissionRecordLinkRef("calculation", "calc_aaa", 1)]
    submission_finding = _finding(MachineReviewSeverity.critical)  # no record_type
    record_finding = _finding(
        MachineReviewSeverity.warning,
        record_type="calculation",
        record_ref="calc_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[submission_finding, record_finding],
        submission_record_links=links,
    )
    mapped = mapping.mapped_by_record[("calculation", "calc_aaa")]
    review = RecordMachineReview.from_mapped_record(
        mapped, reviewed_at=_T0, model="m", submission_id=1
    )

    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[review]
    )

    # Only the record-scoped warning counts; the submission-scoped critical is
    # absent from both the count and the highest severity.
    assert summary.findings_count == 1
    assert summary.highest_severity is MachineReviewSeverity.warning
    assert summary.status is MachineReviewStatus.machine_screened_warning


def test_sibling_record_findings_do_not_affect_summary():
    """A review for a sibling record is never selected for the target (step 8)."""
    target = _review(
        record_ref="calc_aaa",
        status=MachineReviewStatus.machine_screened_pass,
        findings=(_finding(MachineReviewSeverity.info),),
        reviewed_at=_T0,
        submission_id=1,
    )
    sibling = _review(
        record_ref="calc_bbb",
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(
            _finding(MachineReviewSeverity.critical),
            _finding(MachineReviewSeverity.critical),
        ),
        # Strictly newer than the target — would win if scope leaked.
        reviewed_at=_T0 + timedelta(hours=5),
        submission_id=2,
    )

    summary = build_machine_review_record_summary(
        record_type="calculation", record_ref="calc_aaa", reviews=[target, sibling]
    )

    # The sibling's newer, more-severe review must not be selected or counted.
    assert summary.status is MachineReviewStatus.machine_screened_pass
    assert summary.findings_count == 1
    assert summary.highest_severity is MachineReviewSeverity.info
    assert summary.submission_id == 1


# --------------------------------------------------------------------------- #
# Non-finding outcomes carried through latest selection
# --------------------------------------------------------------------------- #


def test_failed_latest_review_returns_machine_review_failed():
    """A failed latest review surfaces machine_review_failed with no findings."""
    earlier_pass = _review(
        status=MachineReviewStatus.machine_screened_pass,
        findings=(_finding(MachineReviewSeverity.info),),
        reviewed_at=_T0,
        submission_id=1,
    )
    latest_failed = _review(
        status=MachineReviewStatus.machine_review_failed,
        findings=(),  # a failed review produces no findings
        reviewed_at=_T0 + timedelta(hours=1),
        submission_id=2,
    )

    summary = build_machine_review_record_summary(
        record_type="calculation",
        record_ref="calc_aaa",
        reviews=[earlier_pass, latest_failed],
    )

    assert summary.status is MachineReviewStatus.machine_review_failed
    assert summary.findings_count == 0
    assert summary.highest_severity is None


def test_not_performed_latest_review_returns_not_run():
    """A not_run latest review surfaces not_run (distinct from no review at all)."""
    earlier_warning = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(_finding(MachineReviewSeverity.warning),),
        reviewed_at=_T0,
        submission_id=1,
    )
    latest_not_run = _review(
        status=MachineReviewStatus.not_run,
        findings=(),
        reviewed_at=_T0 + timedelta(hours=1),
        submission_id=2,
    )

    summary = build_machine_review_record_summary(
        record_type="calculation",
        record_ref="calc_aaa",
        reviews=[earlier_warning, latest_not_run],
    )

    assert summary.status is MachineReviewStatus.not_run
    assert summary.findings_count == 0
    assert summary.highest_severity is None


# --------------------------------------------------------------------------- #
# The summary shape can carry no mutation payload
# --------------------------------------------------------------------------- #


def test_summary_shape_contains_no_mutation_payload():
    """The summary is a pure projection: no mutation/instruction field exists."""
    field_names = set(MachineReviewRecordSummary.model_fields)

    # No field name hints at a state change or instruction.
    forbidden_tokens = ("set_", "mutation", "override", "apply", "is_certified",
                        "benchmark", "review_status", "trust_status", "evidence")
    for token in forbidden_tokens:
        assert not any(token in name for name in field_names), token

    # extra="forbid" -> an injected mutation key is rejected, not silently kept.
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MachineReviewRecordSummary(
            status=MachineReviewStatus.machine_screened_pass,
            set_review_status="reviewed",  # type: ignore[call-arg]
        )
