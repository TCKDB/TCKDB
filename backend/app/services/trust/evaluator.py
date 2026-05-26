"""Deterministic evaluator entrypoints for the trust layer.

The evaluator is a pure function over already-loaded ORM rows. It:

1. Selects the rubric for the record type.
2. Detects discrete structural hard-fail signals first (calc missing,
   calc rejected, geometry validation failed). When a hard-fail
   signal fires, the evaluator still runs every check so the report
   is complete, but forces the badge to
   :attr:`EvidenceBadge.hard_failed` and populates
   :attr:`EvidenceEvaluation.hard_fail_reason`.
3. Runs every :class:`EvidenceCheckSpec.runner` against the record,
   collecting :class:`EvidenceCheckResult` rows.
4. Computes the deterministic completeness ratio and maps it to a
   badge via :func:`label_from_completeness`.

The evaluator does not mutate scientific records and does not require
any LLM provider, API key, or external network. It is safe to call
from inside a read serializer and from offline batch jobs alike.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationQuality,
    KineticsCalculationRole,
    ReactionRole,
    ValidationStatus,
)
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.reaction import ReactionEntry
from app.services.trust.models import (
    EvidenceBadge,
    EvidenceCheckKind,
    EvidenceCheckResult,
    EvidenceEvaluation,
    EvidenceOutcome,
    EvidenceRubric,
    HardFailReason,
    label_from_completeness,
)
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    get_rubric_for_record_type,
)


def select_rubric(record_type: str) -> Optional[EvidenceRubric]:
    """Return the active rubric for ``record_type``, or ``None`` if none defined.

    Thin wrapper around :func:`get_rubric_for_record_type` so callers
    in unrelated modules can import a single namespace.
    """
    return get_rubric_for_record_type(record_type)


def _detect_calculation_hard_fail(calc: Calculation) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``calc`` if a structural failure is present.

    The set is intentionally narrow (per §8 of the spec) — only
    discrete, evidenced failures qualify. Low completeness on its own
    is not a hard fail.
    """
    if calc.quality is CalculationQuality.rejected:
        return HardFailReason.calculation_rejected
    gv = calc.geometry_validation
    if gv is not None and gv.validation_status is ValidationStatus.fail:
        return HardFailReason.geometry_validation_failed
    return None


_KINETICS_REQUIRED_SOURCE_ROLES: frozenset[KineticsCalculationRole] = frozenset(
    {
        KineticsCalculationRole.reactant_energy,
        KineticsCalculationRole.product_energy,
        KineticsCalculationRole.ts_energy,
        KineticsCalculationRole.freq,
    }
)


def _detect_kinetics_hard_fail(kinetics: Kinetics) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``kinetics`` if a structural failure is present."""
    reaction_entry = kinetics.reaction_entry
    if reaction_entry is None:
        return HardFailReason.missing_required_identity

    has_reactant = any(
        participant.role is ReactionRole.reactant
        for participant in reaction_entry.structure_participants
    )
    has_product = any(
        participant.role is ReactionRole.product
        for participant in reaction_entry.structure_participants
    )
    if not (has_reactant and has_product):
        return HardFailReason.missing_required_identity

    if kinetics.tmin_k is not None and kinetics.tmax_k is not None:
        if not (0 < kinetics.tmin_k < kinetics.tmax_k <= 10_000):
            return HardFailReason.invalid_temperature_range

    for link in kinetics.source_calculations:
        if link.role not in _KINETICS_REQUIRED_SOURCE_ROLES:
            continue
        calc = link.calculation
        if calc is not None and _detect_calculation_hard_fail(calc) is not None:
            return HardFailReason.source_calculation_hard_failed_for_required_role

    return None


def _aggregate_results(
    rubric: EvidenceRubric,
    check_results: tuple[EvidenceCheckResult, ...],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    int,
    int,
    float,
    bool,
]:
    """Bucket check results and compute the completeness numerator/denominator.

    Returns ``(passed, missing, warning, not_applicable, passed_count,
    possible_count, completeness, all_required_passed)``. ``passed`` and
    ``missing`` include only required/optional checks; ``warning`` lists
    fired warning-kind checks; ``not_applicable`` lists every skipped
    check regardless of kind.
    """
    passed: list[str] = []
    missing: list[str] = []
    warning: list[str] = []
    not_applicable: list[str] = []

    passed_weight = 0
    possible_weight = 0
    all_required_passed = True

    for result in check_results:
        if result.outcome is EvidenceOutcome.not_applicable:
            not_applicable.append(result.name)
            continue
        if result.kind is EvidenceCheckKind.warning:
            if result.outcome is EvidenceOutcome.warning:
                warning.append(result.name)
            continue
        # required / optional
        possible_weight += result.weight
        if result.outcome is EvidenceOutcome.passed:
            passed.append(result.name)
            passed_weight += result.weight
        elif result.outcome is EvidenceOutcome.warning:
            # required/optional checks should not return warning, but
            # be defensive: treat as a soft pass for now and record the warning.
            warning.append(result.name)
            possible_weight -= result.weight  # do not count toward ratio
        else:
            missing.append(result.name)
            if result.kind is EvidenceCheckKind.required:
                all_required_passed = False

    completeness = passed_weight / possible_weight if possible_weight > 0 else 0.0
    completeness = round(completeness, 4)

    return (
        tuple(passed),
        tuple(missing),
        tuple(warning),
        tuple(not_applicable),
        len(passed),
        len(passed) + len(missing),
        completeness,
        all_required_passed,
    )


def _empty_evaluation_for_missing_calculation(
    calculation_id: Optional[int],
    rubric: EvidenceRubric,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing calculation.

    The evaluator never raises on a missing record — it returns a
    structured hard-fail so the read API can surface "no such record"
    as a trust signal instead of a 5xx.
    """
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=calculation_id,
        rubric=rubric.name,
        rubric_version=rubric.version,
        label=EvidenceBadge.hard_failed,
        passed_checks=(),
        missing_checks=(),
        warning_checks=(),
        not_applicable_checks=tuple(spec.name for spec in rubric.checks),
        passed_count=0,
        possible_count=0,
        evidence_completeness=0.0,
        is_certified=False,
        hard_fail_reason=HardFailReason.calculation_missing,
        check_results=(),
    )


def _empty_evaluation_for_missing_kinetics(
    kinetics_id: Optional[int],
    rubric: EvidenceRubric,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing kinetics row."""
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=kinetics_id,
        rubric=rubric.name,
        rubric_version=rubric.version,
        label=EvidenceBadge.hard_failed,
        passed_checks=(),
        missing_checks=(),
        warning_checks=(),
        not_applicable_checks=tuple(spec.name for spec in rubric.checks),
        passed_count=0,
        possible_count=0,
        evidence_completeness=0.0,
        is_certified=False,
        hard_fail_reason=HardFailReason.kinetics_missing,
        check_results=(),
    )


def evaluate_loaded_calculation(
    calculation: Calculation | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded calculation.

    This entrypoint is pure over the ORM object graph it receives: it
    does not perform a lookup and the check runners must not issue
    their own queries. Callers are responsible for eager-loading the
    relationships required by ``computed_calculation_v1``.
    """
    rubric = COMPUTED_CALCULATION_V1
    if calculation is None:
        return _empty_evaluation_for_missing_calculation(None, rubric)

    hard_fail = _detect_calculation_hard_fail(calculation)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        # Suppress the geometry-validation warning check when the
        # underlying row is a hard-fail — the hard-fail signal is the
        # primary report; surfacing the same condition again as a
        # warning would be noise.
        if (
            hard_fail is HardFailReason.geometry_validation_failed
            and spec.name == "geometry_validation_passed_or_warning"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(calculation)
        check_results.append(
            EvidenceCheckResult(
                name=spec.name,
                outcome=outcome,
                kind=spec.kind,
                weight=spec.weight,
                explain=spec.explain,
            )
        )

    results_tuple = tuple(check_results)
    (
        passed,
        missing,
        warning,
        not_applicable,
        passed_count,
        possible_count,
        completeness,
        all_required_passed,
    ) = _aggregate_results(rubric, results_tuple)

    if hard_fail is not None:
        label = EvidenceBadge.hard_failed
    else:
        label = label_from_completeness(
            completeness,
            all_required_passed=all_required_passed,
        )

    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=calculation.id,
        rubric=rubric.name,
        rubric_version=rubric.version,
        label=label,
        passed_checks=passed,
        missing_checks=missing,
        warning_checks=warning,
        not_applicable_checks=not_applicable,
        passed_count=passed_count,
        possible_count=possible_count,
        evidence_completeness=completeness,
        is_certified=False,
        hard_fail_reason=hard_fail,
        check_results=results_tuple,
    )


def evaluate_loaded_kinetics(
    kinetics: Kinetics | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded kinetics record.

    This entrypoint is pure over the ORM object graph it receives: it
    does not perform a lookup and the check runners must not issue
    their own queries. Callers are responsible for eager-loading the
    relationships required by ``computed_kinetics_v1``.
    """
    rubric = COMPUTED_KINETICS_V1
    if kinetics is None:
        return _empty_evaluation_for_missing_kinetics(None, rubric)

    hard_fail = _detect_kinetics_hard_fail(kinetics)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        if (
            hard_fail is HardFailReason.source_calculation_hard_failed_for_required_role
            and spec.name == "geometry_validation_not_failed_for_source_calculations"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(kinetics)
        check_results.append(
            EvidenceCheckResult(
                name=spec.name,
                outcome=outcome,
                kind=spec.kind,
                weight=spec.weight,
                explain=spec.explain,
            )
        )

    results_tuple = tuple(check_results)
    (
        passed,
        missing,
        warning,
        not_applicable,
        passed_count,
        possible_count,
        completeness,
        all_required_passed,
    ) = _aggregate_results(rubric, results_tuple)

    if hard_fail is not None:
        label = EvidenceBadge.hard_failed
    else:
        label = label_from_completeness(
            completeness,
            all_required_passed=all_required_passed,
        )

    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=kinetics.id,
        rubric=rubric.name,
        rubric_version=rubric.version,
        label=label,
        passed_checks=passed,
        missing_checks=missing,
        warning_checks=warning,
        not_applicable_checks=not_applicable,
        passed_count=passed_count,
        possible_count=possible_count,
        evidence_completeness=completeness,
        is_certified=False,
        hard_fail_reason=hard_fail,
        check_results=results_tuple,
    )


def evaluate_computed_calculation(
    session: Session,
    calculation_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one computed calculation.

    Backward-compatible session/id wrapper around
    :func:`evaluate_loaded_calculation`. The wrapper performs the one
    explicit lookup required by the legacy API; read serializers that
    already have a loaded calculation should call
    :func:`evaluate_loaded_calculation` directly.
    """
    calculation: Optional[Calculation] = session.get(Calculation, calculation_id)
    if calculation is None:
        return _empty_evaluation_for_missing_calculation(
            calculation_id, COMPUTED_CALCULATION_V1
        )
    return evaluate_loaded_calculation(calculation)


def evaluate_computed_kinetics(
    session: Session,
    kinetics_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one computed kinetics row."""
    statement = (
        select(Kinetics)
        .where(Kinetics.id == kinetics_id)
        .options(
            selectinload(Kinetics.reaction_entry).selectinload(
                ReactionEntry.structure_participants
            ),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.artifacts),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.sp_result),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.opt_result),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.freq_result),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.irc_result),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.scan_result),
            selectinload(Kinetics.source_calculations)
            .selectinload(KineticsSourceCalculation.calculation)
            .selectinload(Calculation.path_search_result),
        )
    )
    kinetics = session.scalars(statement).one_or_none()
    if kinetics is None:
        return _empty_evaluation_for_missing_kinetics(kinetics_id, COMPUTED_KINETICS_V1)
    return evaluate_loaded_kinetics(kinetics)
