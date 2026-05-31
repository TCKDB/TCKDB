"""Unit tests for the pure machine-review context/currency adapter.

These prove the adapter closes the private loop from real deterministic trust
output to a currency verdict, with no persistence and no public exposure::

    TrustFragment -> MachineReviewEvidenceContext -> digest
                  -> StoredMachineReviewProjection -> classify -> state

The adapter under test is pure: tests build real
:class:`~app.services.trust.models.EvidenceEvaluation` /
:class:`~app.services.trust.models.TrustFragment` objects and in-memory
:class:`RecordMachineReview` passes directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.machine_review import (
    GeometryValidationContext,
    MachineReviewCurrencyState,
    MachineReviewStaleReason,
    MachineReviewStatus,
    RecordMachineReview,
    SourceCalculationContext,
    build_machine_review_context_hash,
    build_machine_review_evidence_context_from_trust,
    classify_machine_review_currency,
    stored_projection_from_record_machine_review,
)
from app.services.trust.fragment import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation, HardFailReason

_T0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

_RECORD_TYPE = "kinetics"
_RECORD_REF = "kin_9001"
_PROMPT = "prompt_v3"
_RUBRICS = {"kinetics": "computed_kinetics_v1"}


def _evaluation(
    *,
    passed_checks: tuple[str, ...] = ("a_present", "ea_present"),
    missing_checks: tuple[str, ...] = ("uncertainty_present",),
    warning_checks: tuple[str, ...] = ("thin_provenance",),
    not_applicable_checks: tuple[str, ...] = ("irc_evidence_present",),
    hard_fail_reason: HardFailReason | None = None,
    rubric_version: int = 1,
    is_certified: bool = False,
) -> EvidenceEvaluation:
    """Build a representative deterministic evidence evaluation."""
    return EvidenceEvaluation(
        record_type=_RECORD_TYPE,
        record_id=9001,
        rubric="computed_kinetics",
        rubric_version=rubric_version,
        label=EvidenceBadge.mostly_supported,
        passed_checks=passed_checks,
        missing_checks=missing_checks,
        warning_checks=warning_checks,
        not_applicable_checks=not_applicable_checks,
        passed_count=2,
        possible_count=3,
        evidence_completeness=0.67,
        is_certified=is_certified,
        hard_fail_reason=hard_fail_reason,
    )


def _fragment(*, review_status: str = "not_reviewed", **eval_kwargs):
    """Build a public trust fragment from a deterministic evaluation."""
    return build_trust_fragment(
        _evaluation(**eval_kwargs), review_status=review_status
    )


def _context(**kwargs):
    """Build the machine-review evidence context from a trust fragment."""
    fragment = kwargs.pop("fragment", None) or _fragment()
    return build_machine_review_evidence_context_from_trust(
        record_type=kwargs.pop("record_type", _RECORD_TYPE),
        record_ref=kwargs.pop("record_ref", _RECORD_REF),
        trust_fragment=fragment,
        **kwargs,
    )


def _digest(**kwargs) -> str:
    return build_machine_review_context_hash(_context(**kwargs)).context_hash


def _review(
    *,
    reviewed_at: datetime = _T0,
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
    audit_event_id: int | None = 555,
    record_id: int | None = 9001,
) -> RecordMachineReview:
    return RecordMachineReview(
        record_type=_RECORD_TYPE,
        record_ref=_RECORD_REF,
        status=status,
        reviewed_at=reviewed_at,
        audit_event_id=audit_event_id,
        record_id=record_id,
    )


# --------------------------------------------------------------------------- #
# Context construction preserves deterministic evidence
# --------------------------------------------------------------------------- #


def test_build_context_from_trust_preserves_evidence_fields():
    """The context mirrors the trust fragment's evidence value-for-value."""
    fragment = _fragment(hard_fail_reason=None)
    context = build_machine_review_evidence_context_from_trust(
        record_type=_RECORD_TYPE,
        record_ref=_RECORD_REF,
        trust_fragment=fragment,
    )

    assert context.record_type == _RECORD_TYPE
    assert context.record_ref == _RECORD_REF
    # Public rubric name carries the _vN suffix; version preserved separately.
    assert context.rubric_name == "computed_kinetics_v1"
    assert context.rubric_version == 1
    assert context.passed_checks == ("a_present", "ea_present")
    assert context.missing_checks == ("uncertainty_present",)
    assert context.warning_checks == ("thin_provenance",)
    assert context.not_applicable_checks == ("irc_evidence_present",)
    assert context.hard_fail_reason is None
    # Read-only human-review context inputs, observed not owned.
    assert context.review_status == "not_reviewed"
    assert context.is_certified is False


def test_build_context_does_not_mutate_trust_fragment():
    """The adapter is non-interfering: the fragment/evidence dict is unchanged."""
    fragment = _fragment()
    evidence_before = dict(fragment.evidence)
    build_machine_review_evidence_context_from_trust(
        record_type=_RECORD_TYPE, record_ref=_RECORD_REF, trust_fragment=fragment
    )
    assert fragment.evidence == evidence_before
    assert fragment.review_status == "not_reviewed"
    assert fragment.is_certified is False


def test_record_ref_falls_back_to_evidence_record_id():
    """A None record_ref resolves to the evidence record_id (stringified)."""
    context = build_machine_review_evidence_context_from_trust(
        record_type=_RECORD_TYPE, record_ref=None, trust_fragment=_fragment()
    )
    assert context.record_ref == "9001"


# --------------------------------------------------------------------------- #
# Digest sensitivity to deterministic evidence changes
# --------------------------------------------------------------------------- #


def test_context_digest_changes_when_passed_checks_change():
    base = _digest()
    changed = _digest(fragment=_fragment(passed_checks=("a_present",)))
    assert base != changed


def test_context_digest_changes_when_missing_checks_change():
    base = _digest()
    changed = _digest(fragment=_fragment(missing_checks=()))
    assert base != changed


def test_context_digest_changes_when_warning_checks_change():
    base = _digest()
    changed = _digest(
        fragment=_fragment(warning_checks=("thin_provenance", "implausible_value"))
    )
    assert base != changed


def test_context_digest_changes_when_hard_fail_reason_changes():
    base = _digest()
    changed = _digest(
        fragment=_fragment(hard_fail_reason=HardFailReason.calculation_rejected)
    )
    assert base != changed


def test_context_digest_changes_when_review_status_changes():
    """review_status is a context input here, so changing it changes the digest."""
    base = _digest()
    changed = _digest(fragment=_fragment(review_status="approved"))
    assert base != changed


# --------------------------------------------------------------------------- #
# Optional source/geometry/artifact inputs are included in the digest
# --------------------------------------------------------------------------- #


def test_context_digest_includes_source_calculations():
    base = _digest()
    with_sources = _digest(
        source_calculations=(
            SourceCalculationContext(ref="calc_sp", role="sp"),
            SourceCalculationContext(ref="calc_opt", role="opt"),
        )
    )
    assert base != with_sources


def test_context_digest_includes_geometry_validations():
    base = _digest()
    with_geom = _digest(
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="valid"),
        )
    )
    assert base != with_geom


def test_context_digest_includes_artifact_kinds():
    base = _digest()
    with_artifacts = _digest(artifact_kinds=("input", "output"))
    assert base != with_artifacts


# --------------------------------------------------------------------------- #
# RecordMachineReview -> StoredMachineReviewProjection
# --------------------------------------------------------------------------- #


def test_record_review_projects_to_stored_currency_projection():
    """The projection carries the verdict status and stamped currency dimensions."""
    digest = build_machine_review_context_hash(_context())
    review = _review(status=MachineReviewStatus.machine_screened_needs_attention)

    projection = stored_projection_from_record_machine_review(
        review,
        context_digest=digest,
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
        id=42,
    )

    assert projection.record_type == _RECORD_TYPE
    assert projection.record_id == 9001
    assert projection.status is MachineReviewStatus.machine_screened_needs_attention
    assert projection.context_hash == digest.context_hash
    assert projection.context_schema_version == digest.context_schema_version
    assert projection.prompt_version == _PROMPT
    assert projection.rubric_versions == _RUBRICS
    assert projection.id == 42


def test_projection_preserves_source_audit_event_id():
    """audit_event_id bridges to source_audit_event_id; explicit arg wins."""
    digest = build_machine_review_context_hash(_context())
    review = _review(audit_event_id=777)

    # Falls back to the review's audit_event_id when not supplied.
    bridged = stored_projection_from_record_machine_review(
        review, context_digest=digest, prompt_version=_PROMPT, rubric_versions=_RUBRICS
    )
    assert bridged.source_audit_event_id == 777

    # An explicit argument overrides the review's audit_event_id.
    overridden = stored_projection_from_record_machine_review(
        review,
        context_digest=digest,
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
        source_audit_event_id=999,
    )
    assert overridden.source_audit_event_id == 999


def test_projection_preserves_reviewed_at():
    """reviewed_at is carried through verbatim (the latest-selection key)."""
    digest = build_machine_review_context_hash(_context())
    review = _review(reviewed_at=_T0)
    projection = stored_projection_from_record_machine_review(
        review, context_digest=digest, prompt_version=_PROMPT, rubric_versions=_RUBRICS
    )
    assert projection.reviewed_at == _T0


# --------------------------------------------------------------------------- #
# Currency classification against real evidence digests
# --------------------------------------------------------------------------- #


def _projection_for(digest, **review_kwargs):
    return stored_projection_from_record_machine_review(
        _review(**review_kwargs),
        context_digest=digest,
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
        id=1,
    )


def test_current_projection_classifies_current_against_matching_digest():
    digest = build_machine_review_context_hash(_context())
    projection = _projection_for(digest)

    result = classify_machine_review_currency(
        [projection],
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.current
    assert result.stale_reasons == ()


def test_changed_digest_classifies_latest_review_stale():
    """Evidence changes after the review -> the stored digest no longer matches."""
    digest_old = build_machine_review_context_hash(_context())
    projection = _projection_for(digest_old)

    digest_new = build_machine_review_context_hash(
        _context(fragment=_fragment(missing_checks=()))
    )
    result = classify_machine_review_currency(
        [projection],
        current_context=digest_new,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert MachineReviewStaleReason.context_hash_mismatch in result.stale_reasons


def test_changed_prompt_version_classifies_latest_review_stale():
    digest = build_machine_review_context_hash(_context())
    projection = _projection_for(digest)

    result = classify_machine_review_currency(
        [projection],
        current_context=digest,
        active_prompt_version="prompt_v4",  # active prompt advanced
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.prompt_version_mismatch,
    )


def test_changed_rubric_versions_classifies_latest_review_stale():
    digest = build_machine_review_context_hash(_context())
    projection = _projection_for(digest)

    result = classify_machine_review_currency(
        [projection],
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions={"kinetics": "computed_kinetics_v2"},
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.rubric_versions_mismatch,
    )


def test_no_reviews_classifies_not_run():
    digest = build_machine_review_context_hash(_context())
    result = classify_machine_review_currency(
        [],
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert result.state is MachineReviewCurrencyState.not_run
    assert result.active_review is None


# --------------------------------------------------------------------------- #
# Pure end-to-end: real evidence -> current, then one field changes -> stale
# --------------------------------------------------------------------------- #


def test_end_to_end_current_then_stale_on_evidence_change():
    """Full pure loop: trust -> context -> digest -> projection -> classify."""
    # 1. Deterministic trust output -> context -> digest, and a review pass.
    fragment = _fragment()
    context = build_machine_review_evidence_context_from_trust(
        record_type=_RECORD_TYPE, record_ref=_RECORD_REF, trust_fragment=fragment
    )
    digest = build_machine_review_context_hash(context)
    review = _review()
    projection = stored_projection_from_record_machine_review(
        review,
        context_digest=digest,
        prompt_version=_PROMPT,
        rubric_versions=_RUBRICS,
        id=1,
    )

    # 2. Against the same evidence digest -> current.
    current = classify_machine_review_currency(
        [projection],
        current_context=digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert current.state is MachineReviewCurrencyState.current
    assert current.active_review is projection

    # 3. One deterministic evidence field changes -> fresh digest -> stale.
    changed_fragment = _fragment(passed_checks=("a_present", "ea_present", "tst_present"))
    changed_context = build_machine_review_evidence_context_from_trust(
        record_type=_RECORD_TYPE,
        record_ref=_RECORD_REF,
        trust_fragment=changed_fragment,
    )
    changed_digest = build_machine_review_context_hash(changed_context)

    stale = classify_machine_review_currency(
        [projection],  # still carries the OLD digest
        current_context=changed_digest,
        active_prompt_version=_PROMPT,
        active_rubric_versions=_RUBRICS,
    )
    assert stale.state is MachineReviewCurrencyState.stale
    assert MachineReviewStaleReason.context_hash_mismatch in stale.stale_reasons
