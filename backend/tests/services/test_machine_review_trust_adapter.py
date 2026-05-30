"""Unit tests for the private machine-review trust-envelope adapter.

These prove the adapter in ``app/services/machine_review/trust_adapter.py``:
a future ``trust.machine_review`` block can be assembled *beside* the
deterministic evidence/trust evaluator output without altering, recomputing, or
perturbing it — and without changing the public
:class:`~app.services.trust.models.TrustFragment` response shape.

The adapter is pure, so these tests build a representative
:class:`EvidenceEvaluation` and the *real* public fragment via
:func:`build_trust_fragment`, then wrap it. No database, no route, no public
exposure is involved.

The core invariant — deterministic evidence is byte-identical with or without
the projection — is asserted directly here at the projection boundary; the
broader run-time non-interference invariant (an advisory machine-review *run*
does not perturb persisted evidence) lives in
``test_machine_review_non_interference.py`` and is not duplicated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.services.machine_review import (
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
)
from app.services.machine_review.schemas import (
    MachineReviewCategory,
    MachineReviewFinding,
)
from app.services.machine_review.trust_adapter import (
    InternalTrustEnvelopeWithMachineReview,
    build_internal_machine_review_trust_fragment,
    build_private_trust_envelope_with_machine_review,
)
from app.services.trust import build_trust_fragment
from app.services.trust.models import (
    EvidenceBadge,
    EvidenceEvaluation,
    TrustFragment,
    TrustLLMPrecheck,
)

# A fixed reference instant so tests never depend on wall-clock time.
_T0 = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def _evaluation(*, is_certified: bool = False) -> EvidenceEvaluation:
    """Build a representative mid-band evidence evaluation (pure, no DB).

    A partial/mid-band evaluation (some passed, some missing) makes the
    byte-identical assertion meaningful rather than a degenerate snapshot.
    """
    return EvidenceEvaluation(
        record_type="calculation",
        record_id=1,
        rubric="computed_calculation",
        rubric_version=1,
        label=EvidenceBadge.partial,
        passed_checks=("final_geometry_present", "opt_converged"),
        missing_checks=("source_artifact_present",),
        warning_checks=(),
        not_applicable_checks=(),
        passed_count=2,
        possible_count=3,
        evidence_completeness=0.6667,
        is_certified=is_certified,
    )


def _finding(
    severity: MachineReviewSeverity,
    *,
    record_type: str = "calculation",
    record_ref: str = "1",
) -> MachineReviewFinding:
    """Build a minimal record-addressed machine-review finding."""
    return MachineReviewFinding(
        severity=severity,
        category=MachineReviewCategory.provenance,
        record_type=record_type,
        record_ref=record_ref,
        message="advisory finding",
    )


def _review(
    *,
    status: MachineReviewStatus,
    findings: tuple[MachineReviewFinding, ...] = (),
    reviewed_at: datetime | None = None,
    submission_id: int | None = None,
    record_ref: str = "1",
) -> RecordMachineReview:
    """Build a record-scoped machine-review pass wrapper."""
    return RecordMachineReview(
        record_type="calculation",
        record_ref=record_ref,
        status=status,
        findings=findings,
        reviewed_at=reviewed_at,
        submission_id=submission_id,
    )


# --------------------------------------------------------------------------- #
# The machine_review fragment itself
# --------------------------------------------------------------------------- #


def test_no_machine_review_builds_not_run_fragment():
    """With no record-level review, the fragment is a not_run summary (req 8)."""
    fragment = build_internal_machine_review_trust_fragment(reviews=())

    assert isinstance(fragment, MachineReviewRecordSummary)
    assert fragment.status is MachineReviewStatus.not_run
    assert fragment.findings_count == 0
    assert fragment.highest_severity is None


def test_latest_machine_review_is_selected_for_fragment():
    """When several reviews exist, the read-model latest helper picks one (req 9)."""
    older = _review(
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(_finding(MachineReviewSeverity.critical),),
        reviewed_at=_T0,
        submission_id=1,
    )
    newer = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(_finding(MachineReviewSeverity.warning),),
        reviewed_at=_T0 + timedelta(hours=1),
        submission_id=2,
    )

    # Input order must not matter; the timestamp decides.
    fragment = build_internal_machine_review_trust_fragment(
        reviews=[newer, older], record_type="calculation", record_ref="1"
    )

    assert fragment.status is MachineReviewStatus.machine_screened_warning
    assert fragment.highest_severity is MachineReviewSeverity.warning
    assert fragment.findings_count == 1
    assert fragment.submission_id == 2


def test_precomputed_summary_is_passed_through():
    """A precomputed summary is returned as-is (req 2)."""
    summary = MachineReviewRecordSummary(
        status=MachineReviewStatus.machine_screened_pass,
        findings_count=0,
        submission_id=9,
    )

    fragment = build_internal_machine_review_trust_fragment(summary=summary)

    assert fragment is summary


# --------------------------------------------------------------------------- #
# Assembly beside evidence + preservation of the deterministic half
# --------------------------------------------------------------------------- #


def test_machine_review_summary_can_sit_beside_evidence():
    """The envelope carries both deterministic evidence and a machine_review block."""
    public = build_trust_fragment(_evaluation())
    review = _review(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(_finding(MachineReviewSeverity.warning),),
        reviewed_at=_T0,
        submission_id=1,
    )

    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        reviews=[review],
        record_type="calculation",
        record_ref="1",
    )

    assert isinstance(envelope, InternalTrustEnvelopeWithMachineReview)
    # Evidence sits unchanged beside a populated machine_review summary.
    assert envelope.evidence == public.evidence
    assert envelope.machine_review.status is MachineReviewStatus.machine_screened_warning


def test_evidence_model_dump_is_byte_identical_before_and_after_projection():
    """The evaluator/evidence output is byte-identical across the projection (core invariant)."""
    evaluation = _evaluation()
    public = build_trust_fragment(evaluation)

    # Snapshot the deterministic outputs *before* the projection.
    import json

    evaluation_before = json.dumps(evaluation.model_dump(mode="json"), sort_keys=True)
    evidence_before = json.dumps(public.evidence, sort_keys=True)

    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        reviews=[
            _review(
                status=MachineReviewStatus.machine_screened_needs_attention,
                findings=(_finding(MachineReviewSeverity.critical),),
                reviewed_at=_T0,
                submission_id=1,
            )
        ],
        record_type="calculation",
        record_ref="1",
    )

    # The source evaluation is untouched, and the envelope's evidence is the
    # exact same dump the public fragment carried.
    assert json.dumps(evaluation.model_dump(mode="json"), sort_keys=True) == evaluation_before
    assert json.dumps(envelope.evidence, sort_keys=True) == evidence_before
    assert envelope.evidence == public.evidence


def test_review_status_is_preserved_exactly():
    """``review_status`` is carried through exactly as supplied (req 5)."""
    public = build_trust_fragment(_evaluation(), review_status="reviewed")
    assert public.review_status == "reviewed"

    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        machine_review_summary=MachineReviewRecordSummary(
            status=MachineReviewStatus.not_run
        ),
    )

    assert envelope.review_status == "reviewed"


def test_is_certified_is_preserved_exactly():
    """``is_certified`` is carried through exactly as supplied (req 6)."""
    # A curator-certified fragment (supplied as-is); the adapter must not flip it.
    public = TrustFragment(
        review_status="reviewed",
        trust_status="well_supported",
        evidence={"rubric": "computed_calculation_v1"},
        is_certified=True,
    )

    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        machine_review_summary=MachineReviewRecordSummary(
            status=MachineReviewStatus.machine_screened_pass
        ),
    )

    assert envelope.is_certified is True


def test_llm_precheck_remains_disabled_not_run():
    """The disabled/not_run llm_precheck default is preserved beside machine_review (req 4)."""
    public = build_trust_fragment(_evaluation())
    # Sanity: the public default really is disabled/not_run.
    assert public.llm_precheck == TrustLLMPrecheck()

    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        machine_review_summary=MachineReviewRecordSummary(
            status=MachineReviewStatus.machine_screened_warning
        ),
    )

    dumped = envelope.llm_precheck.model_dump(mode="json")
    assert dumped == {"enabled": False, "label": "not_run", "summary": None}


def test_machine_review_failed_can_be_projected_without_affecting_evidence():
    """A machine_review_failed summary projects beside untouched evidence (req 8 boundary)."""
    evaluation = _evaluation()
    public = build_trust_fragment(evaluation)
    evidence_before = dict(public.evidence)

    failed = _review(
        status=MachineReviewStatus.machine_review_failed,
        findings=(),  # a failed review carries no findings
        reviewed_at=_T0,
        submission_id=1,
    )
    envelope = build_private_trust_envelope_with_machine_review(
        trust_fragment=public,
        reviews=[failed],
        record_type="calculation",
        record_ref="1",
    )

    # The reviewer-failure axis surfaces in machine_review; evidence is intact.
    assert envelope.machine_review.status is MachineReviewStatus.machine_review_failed
    assert envelope.machine_review.findings_count == 0
    assert envelope.evidence == evidence_before
    # The evaluator's own label/certification are not touched by a failed review.
    assert envelope.trust_status == evaluation.label.value
    assert envelope.is_certified is False


# --------------------------------------------------------------------------- #
# The private envelope can carry no mutation payload
# --------------------------------------------------------------------------- #


def test_private_fragment_forbids_mutation_payload_fields():
    """The private envelope and its machine_review block reject injected state."""
    # No field name on the envelope hints at a state change / instruction.
    envelope_fields = set(InternalTrustEnvelopeWithMachineReview.model_fields)
    forbidden_tokens = ("set_", "mutation", "override", "apply")
    for token in forbidden_tokens:
        assert not any(token in name for name in envelope_fields), token

    public = build_trust_fragment(_evaluation())

    # extra="forbid" -> an injected mutation key is rejected, not silently kept.
    with pytest.raises(ValidationError):
        InternalTrustEnvelopeWithMachineReview(
            trust_status="partial",
            evidence=public.evidence,
            machine_review=MachineReviewRecordSummary(
                status=MachineReviewStatus.not_run
            ),
            set_review_status="reviewed",  # type: ignore[call-arg]
        )

    # The reused machine_review summary type is likewise mutation-proof.
    with pytest.raises(ValidationError):
        MachineReviewRecordSummary(
            status=MachineReviewStatus.not_run,
            set_is_certified=True,  # type: ignore[call-arg]
        )


# --------------------------------------------------------------------------- #
# Public trust output is unchanged: the adapter is invisible to it
# --------------------------------------------------------------------------- #


def test_public_trust_fragment_shape_is_unchanged():
    """The public TrustFragment still has the frozen precheck shape and no machine_review.

    Lightweight, pure echo of the contract asserted by
    ``test_machine_review_non_interference.py`` — here to pin that *this* adapter
    leaves the public read shape untouched.
    """
    dumped = build_trust_fragment(_evaluation()).model_dump(mode="json")

    assert dumped["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert "machine_review" not in dumped
    # The public field set is exactly the documented trust shape — no leakage.
    assert set(dumped) == {
        "review_status",
        "trust_status",
        "evidence",
        "llm_precheck",
        "is_certified",
    }
