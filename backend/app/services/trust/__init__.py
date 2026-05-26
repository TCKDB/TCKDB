"""Deterministic trust / evidence evaluator.

Implements the read-time, code-defined rubrics specified in
``backend/docs/specs/automated_trust_layer.md``. The evaluator answers
"how much supporting provenance is attached to this record?", **not**
"is this record scientifically correct?". The metric is therefore named
``evidence_completeness`` (never ``quality_score`` or
``confidence_score``).

This package contains the rubric foundation plus the first MVP rubric,
``computed_calculation_v1``. Additional rubrics (kinetics, thermo,
statmech, transport, experimental) land in later slices per
§14 of the spec.
"""

from app.services.trust.evaluator import (
    evaluate_computed_calculation,
    evaluate_loaded_calculation,
    select_rubric,
)
from app.services.trust.models import (
    EvidenceBadge,
    EvidenceCheckKind,
    EvidenceCheckResult,
    EvidenceCheckSpec,
    EvidenceEvaluation,
    EvidenceOutcome,
    EvidenceRubric,
    HardFailReason,
    TrustFragment,
    TrustLLMPrecheck,
    label_from_completeness,
)
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION_V1,
    RUBRIC_REGISTRY,
)

__all__ = [
    "COMPUTED_CALCULATION_V1",
    "EvidenceBadge",
    "EvidenceCheckKind",
    "EvidenceCheckResult",
    "EvidenceCheckSpec",
    "EvidenceEvaluation",
    "EvidenceOutcome",
    "EvidenceRubric",
    "HardFailReason",
    "RUBRIC_REGISTRY",
    "TrustFragment",
    "TrustLLMPrecheck",
    "evaluate_computed_calculation",
    "evaluate_loaded_calculation",
    "label_from_completeness",
    "select_rubric",
]
