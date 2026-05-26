"""Pydantic models and constants used by the deterministic trust layer.

The model layer here mirrors the concepts defined in
``backend/docs/specs/automated_trust_layer.md`` §5:

* :class:`EvidenceCheckSpec` is the *declaration* of a single check
  (name, kind, weight, explanation) inside a rubric.
* :class:`EvidenceCheckResult` is the *runtime outcome* of evaluating
  one such check against one record.
* :class:`EvidenceEvaluation` is the aggregated result of running every
  check in a rubric against one record.
* :class:`EvidenceRubric` is the versioned, code-defined bundle of
  checks (per §5.2).
* :class:`TrustFragment` is the JSON shape returned under ``trust:`` on
  scientific reads (per §10.1).

The module also centralises the deterministic label thresholds
(:func:`label_from_completeness`) so any future rubric inherits the
same mapping unless it explicitly overrides it.

None of the names in this module are allowed to drift toward
"quality score" framing. The metric is *evidence completeness*; the
label is the *evidence badge*. A rubric never produces
``is_certified=True``; that flag is reserved for curator action and is
always emitted as ``False`` by the evaluator.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceBadge(str, Enum):
    """Human-facing evidence-completeness label.

    Mirrors §5.4 / §6.1 of the spec. ``hard_failed`` is the only value
    that may be produced independent of the completeness ratio — it is
    triggered by a discrete structural failure signal (see
    :class:`HardFailReason`).
    """

    well_supported = "well_supported"
    mostly_supported = "mostly_supported"
    partial = "partial"
    sparse = "sparse"
    unsupported = "unsupported"
    hard_failed = "hard_failed"


class EvidenceOutcome(str, Enum):
    """Outcome of running a single :class:`EvidenceCheckSpec` against a record.

    * ``passed`` — the check's condition held; positive evidence.
    * ``missing`` — the check applies and did not pass; the explanation
      is surfaced in ``missing_checks``.
    * ``warning`` — the check applies, the underlying signal is tri-state
      (typically a :class:`ValidationStatus.warning`), and the result
      is informational only. Warnings never reduce the completeness
      ratio (warning-kind checks contribute zero weight).
    * ``not_applicable`` — the check's ``applies_when`` predicate is
      false; the check is excluded from both numerator and denominator.
    """

    passed = "passed"
    missing = "missing"
    warning = "warning"
    not_applicable = "not_applicable"


class EvidenceCheckKind(str, Enum):
    """Classification of a check inside a rubric (per §5.1).

    * ``required`` — failure prevents reaching ``well_supported``.
    * ``optional`` — contributes to completeness but absence does not
      block any label.
    * ``warning`` — informational only; contributes zero weight.
    """

    required = "required"
    optional = "optional"
    warning = "warning"


class HardFailReason(str, Enum):
    """Discrete structural failure signals for deterministic evidence rubrics.

    Hard fails override the completeness ratio (§8) and force the
    rubric output into the ``hard_failed`` family. Names are stable
    identifiers; ``explain`` strings in
    :class:`EvidenceEvaluation.hard_fail_reason` may be richer.
    """

    calculation_missing = "calculation_missing"
    calculation_rejected = "calculation_rejected"
    kinetics_missing = "kinetics_missing"
    invalid_temperature_range = "invalid_temperature_range"
    geometry_validation_failed = "geometry_validation_failed"
    missing_required_identity = "missing_required_identity"
    result_block_missing_when_successful = "result_block_missing_when_successful"
    source_calculation_hard_failed_for_required_role = (
        "source_calculation_hard_failed_for_required_role"
    )


class EvidenceCheckSpec(BaseModel):
    """Declaration of a single deterministic check inside a rubric.

    A rubric's check set is built from a list of these. Each spec is
    paired with a pure ``runner`` callable that takes the loaded record
    graph and returns an :class:`EvidenceOutcome`. Runners must not
    issue their own database queries — that constraint is what keeps
    the evaluator deterministic and free of N+1 surprises (see §12 of
    the spec).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(..., description="Stable identifier for the check.")
    kind: EvidenceCheckKind = Field(
        ...,
        description="required / optional / warning — controls weight and label gating.",
    )
    weight: int = Field(
        default=1,
        ge=0,
        description=(
            "Numerator/denominator weight when the check is required or optional. "
            "Warning-kind checks always contribute zero weight regardless of value."
        ),
    )
    explain: str = Field(
        default="",
        description="Short human string surfaced under missing_checks / warnings.",
    )
    runner: Callable[..., EvidenceOutcome] = Field(
        ...,
        description="Pure callable: (record, *, context) -> EvidenceOutcome.",
    )


class EvidenceCheckResult(BaseModel):
    """Runtime outcome of evaluating one :class:`EvidenceCheckSpec`.

    Carries both the outcome and the originating spec metadata so the
    aggregator can build the deterministic passed / missing / warning
    sets without re-deriving anything.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    outcome: EvidenceOutcome
    kind: EvidenceCheckKind
    weight: int
    explain: Optional[str] = None

    @property
    def contributes_weight(self) -> bool:
        """Return True when this check is part of the completeness ratio.

        Warning-kind checks and ``not_applicable`` outcomes never
        contribute weight, matching §5.5 of the spec.
        """
        if self.kind is EvidenceCheckKind.warning:
            return False
        return self.outcome is not EvidenceOutcome.not_applicable


class EvidenceRubric(BaseModel):
    """Versioned bundle of checks tied to a record type (§5.2).

    A record can match at most one rubric per evaluator call (selection
    happens in :mod:`app.services.trust.rubrics`); the evaluator must
    raise rather than guess on ambiguity.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(..., description="Rubric name, e.g. computed_calculation.")
    version: int = Field(..., ge=1, description="Integer version of this rubric.")
    record_type: str = Field(
        ...,
        description="Discriminator for the kind of record this rubric applies to.",
    )
    checks: tuple[EvidenceCheckSpec, ...] = Field(
        ...,
        description="Ordered tuple of check specs. Order controls report stability.",
    )

    @property
    def qualified_name(self) -> str:
        """Return ``<name>@v<version>`` for telemetry and logs."""
        return f"{self.name}@v{self.version}"


class EvidenceEvaluation(BaseModel):
    """Aggregated result of running a rubric against one record.

    Field semantics:

    * ``passed_checks`` / ``missing_checks`` / ``warning_checks`` /
      ``not_applicable_checks`` — names of checks bucketed by their
      runtime outcome. ``passed`` and ``missing`` include only
      required/optional checks; ``warning`` is the bucket for fired
      warning-kind checks; ``not_applicable`` lists every skipped
      check regardless of kind.
    * ``passed_count`` / ``possible_count`` — the numerator and
      denominator of :attr:`evidence_completeness`. ``possible_count``
      excludes ``not_applicable`` and warning-kind checks.
    * ``evidence_completeness`` — ``passed_weight / possible_weight``,
      rounded to four decimals. Never exposed as a percentage by the
      evaluator (see §6 of the spec).
    * ``is_certified`` — always ``False`` for automated evaluations.
      Reserved for curator action (§6.2).
    * ``hard_fail_reason`` — populated only when a structural hard-fail
      signal forces ``label = hard_failed``.
    """

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_id: Optional[int]
    rubric: str
    rubric_version: int
    label: EvidenceBadge
    passed_checks: tuple[str, ...]
    missing_checks: tuple[str, ...]
    warning_checks: tuple[str, ...]
    not_applicable_checks: tuple[str, ...]
    passed_count: int
    possible_count: int
    evidence_completeness: float
    is_certified: bool = False
    hard_fail_reason: Optional[HardFailReason] = None
    check_results: tuple[EvidenceCheckResult, ...] = Field(default_factory=tuple)


class TrustLLMPrecheck(BaseModel):
    """Advisory LLM precheck metadata surfaced under ``trust.llm_precheck``.

    Default in this MVP is ``enabled=False`` and ``label='not_run'``
    because no LLM is wired in; if/when the precheck event is
    available, the read serializer can populate this without altering
    the rubric output.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    label: str = "not_run"
    summary: Optional[str] = None


class TrustFragment(BaseModel):
    """Read-fragment shape per §10.1 of the spec.

    Built from an :class:`EvidenceEvaluation` plus the record's
    :class:`~app.db.models.record_review.RecordReview` row (when
    present). The evaluator itself returns only the evaluation; the
    fragment is composed in the read serializer where the review row
    and any LLM-precheck audit event are already in scope.
    """

    model_config = ConfigDict(frozen=True)

    review_status: str = "not_reviewed"
    trust_status: str
    evidence: dict
    llm_precheck: TrustLLMPrecheck = Field(default_factory=TrustLLMPrecheck)
    is_certified: bool = False


COMPLETENESS_THRESHOLDS: tuple[tuple[float, EvidenceBadge], ...] = (
    (0.90, EvidenceBadge.well_supported),
    (0.75, EvidenceBadge.mostly_supported),
    (0.50, EvidenceBadge.partial),
    (0.25, EvidenceBadge.sparse),
)
"""Default thresholds per §6.1 of the spec, in descending order.

``well_supported`` additionally requires every ``required`` check to
have passed. Sub-``sparse`` ratios collapse to ``unsupported``.
"""


def label_from_completeness(
    completeness: float,
    *,
    all_required_passed: bool,
) -> EvidenceBadge:
    """Map a completeness ratio plus the required-checks signal to a badge.

    The mapping is deterministic and centralised so every rubric
    inherits the same thresholds unless it deliberately overrides
    them. ``all_required_passed`` is the gate documented in §6.1: a
    record cannot reach ``well_supported`` while any required check
    still fails, regardless of how strong the ratio is.
    """
    if completeness >= 0.90 and all_required_passed:
        return EvidenceBadge.well_supported
    for threshold, badge in COMPLETENESS_THRESHOLDS:
        if completeness >= threshold and badge is not EvidenceBadge.well_supported:
            return badge
    return EvidenceBadge.unsupported
