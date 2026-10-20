"""Read-layer composition helpers for deterministic trust fragments."""

from __future__ import annotations

from enum import Enum

from app.services.trust.models import (
    EvidenceEvaluation,
    TransitionStateScopedAssessment,
    TransitionStateTrustFragment,
    TrustFragment,
)


def build_trust_fragment(
    evaluation: EvidenceEvaluation,
    *,
    review_status: Enum | str | None = None,
) -> TrustFragment:
    """Build the public trust fragment for a scientific read response.

    The evaluator owns deterministic evidence completeness. The read
    layer owns curator review status, disabled LLM-precheck defaults,
    certification defaults, and the public evidence object shape.
    """
    status = _review_status_value(review_status)
    evidence = evaluation.model_dump(mode="json", exclude={"check_results"})
    evidence["rubric"] = _public_rubric_name(evaluation)
    return TrustFragment(
        review_status=status,
        trust_status=evaluation.label.value,
        evidence=evidence,
        is_certified=evaluation.is_certified,
    )


def build_transition_state_trust_fragment(
    evaluation: EvidenceEvaluation,
    *,
    review_status: Enum | str | None = None,
    structure_assessment: TransitionStateScopedAssessment,
) -> TransitionStateTrustFragment:
    """The transition-state trust fragment: :func:`build_trust_fragment` plus the scoped structure assessment (#723).

    Wraps the shared builder rather than changing it. The assessment is carried beside the badge and is read by
    nothing that computes it: ``trust_status``, the completeness numbers and ``is_certified`` come from the evaluation
    alone.
    """
    base = build_trust_fragment(evaluation, review_status=review_status)
    return TransitionStateTrustFragment(
        review_status=base.review_status,
        trust_status=base.trust_status,
        evidence=base.evidence,
        llm_precheck=base.llm_precheck,
        is_certified=base.is_certified,
        structure_assessment=structure_assessment,
    )


def _review_status_value(review_status: Enum | str | None) -> str:
    if review_status is None:
        return "not_reviewed"
    if isinstance(review_status, Enum):
        return str(review_status.value)
    return review_status


def _public_rubric_name(evaluation: EvidenceEvaluation) -> str:
    suffix = f"_v{evaluation.rubric_version}"
    return f"{evaluation.rubric}{suffix}"


__all__ = ["build_transition_state_trust_fragment", "build_trust_fragment"]
