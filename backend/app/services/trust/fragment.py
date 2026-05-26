"""Read-layer composition helpers for deterministic trust fragments."""

from __future__ import annotations

from enum import Enum

from app.services.trust.models import EvidenceEvaluation, TrustFragment


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


def _review_status_value(review_status: Enum | str | None) -> str:
    if review_status is None:
        return "not_reviewed"
    if isinstance(review_status, Enum):
        return str(review_status.value)
    return review_status


def _public_rubric_name(evaluation: EvidenceEvaluation) -> str:
    suffix = f"_v{evaluation.rubric_version}"
    if evaluation.rubric.endswith(suffix):
        return evaluation.rubric
    return f"{evaluation.rubric}{suffix}"


__all__ = ["build_trust_fragment"]
