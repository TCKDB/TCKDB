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

from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
)
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    KineticsCalculationRole,
    ReactionRole,
    StatmechCalculationRole,
    ThermoCalculationRole,
    TransitionStateEntryStatus,
    TransportCalculationRole,
    ValidationStatus,
)
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.reaction import ReactionEntry
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
)
from app.db.models.thermo import Thermo, ThermoSourceCalculation
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport, TransportSourceCalculation
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
    COMPUTED_STATMECH_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_TRANSITION_STATE_V1,
    COMPUTED_TRANSPORT_V1,
    _ts_representative_freq_result,
    _ts_source_calculations,
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


_THERMO_REQUIRED_SOURCE_ROLES: frozenset[ThermoCalculationRole] = frozenset(
    {
        ThermoCalculationRole.opt,
        ThermoCalculationRole.freq,
    }
)

_STATMECH_REQUIRED_SOURCE_ROLES: frozenset[StatmechCalculationRole] = frozenset(
    {
        StatmechCalculationRole.opt,
        StatmechCalculationRole.freq,
    }
)

def _thermo_has_representation(thermo: Thermo) -> bool:
    """Return True when scalar, NASA coefficients, or populated points exist."""
    if thermo.h298_kj_mol is not None or thermo.s298_j_mol_k is not None:
        return True
    if thermo.nasa is not None:
        coefficient_fields = (
            "a1",
            "a2",
            "a3",
            "a4",
            "a5",
            "a6",
            "a7",
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "b6",
            "b7",
        )
        if all(getattr(thermo.nasa, field) is not None for field in coefficient_fields):
            return True
    return any(
        point.cp_j_mol_k is not None
        or point.h_kj_mol is not None
        or point.s_j_mol_k is not None
        or point.g_kj_mol is not None
        for point in thermo.points
    )


def _thermo_temperature_range_invalid(thermo: Thermo) -> bool:
    """Return True when any populated thermo temperature range is invalid."""
    if thermo.tmin_k is not None or thermo.tmax_k is not None:
        if thermo.tmin_k is None or thermo.tmax_k is None:
            return True
        if not (0 < thermo.tmin_k < thermo.tmax_k <= 10_000):
            return True

    nasa = thermo.nasa
    if nasa is None:
        return False
    if nasa.t_low is None and nasa.t_mid is None and nasa.t_high is None:
        return False
    if nasa.t_low is None or nasa.t_mid is None or nasa.t_high is None:
        return True
    return not (0 < nasa.t_low < nasa.t_mid < nasa.t_high <= 10_000)


def _detect_thermo_hard_fail(thermo: Thermo) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``thermo`` if a structural failure is present."""
    if thermo.species_entry_id is None or thermo.species_entry is None:
        return HardFailReason.species_entry_missing

    if not _thermo_has_representation(thermo):
        return HardFailReason.no_thermo_representation_present

    if _thermo_temperature_range_invalid(thermo):
        return HardFailReason.invalid_temperature_range

    for link in thermo.source_calculations:
        if link.role not in _THERMO_REQUIRED_SOURCE_ROLES:
            continue
        calc = link.calculation
        if calc is not None and _detect_calculation_hard_fail(calc) is not None:
            return HardFailReason.source_calculation_hard_failed_for_required_role

    return None


def _statmech_has_torsions_or_torsion_treatment(statmech: Statmech) -> bool:
    """Return True when statmech source roles should include scan evidence."""
    treatment = statmech.statmech_treatment
    treatment_value = getattr(treatment, "value", treatment)
    return len(statmech.torsions) >= 1 or (
        isinstance(treatment_value, str)
        and ("1d" in treatment_value or "nd" in treatment_value)
    )


def _detect_statmech_hard_fail(statmech: Statmech) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``statmech`` if a structural failure is present."""
    if statmech.species_entry_id is None or statmech.species_entry is None:
        return HardFailReason.species_entry_missing

    if statmech.external_symmetry is not None and statmech.external_symmetry < 1:
        return HardFailReason.invalid_external_symmetry

    if any(torsion.dimension < 1 for torsion in statmech.torsions):
        return HardFailReason.invalid_torsion_dimension

    required_roles = set(_STATMECH_REQUIRED_SOURCE_ROLES)
    if _statmech_has_torsions_or_torsion_treatment(statmech):
        required_roles.add(StatmechCalculationRole.scan)

    for link in statmech.source_calculations:
        if link.role not in required_roles:
            continue
        calc = link.calculation
        if calc is not None and _detect_calculation_hard_fail(calc) is not None:
            return HardFailReason.source_calculation_hard_failed_for_required_role

    for torsion in statmech.torsions:
        calc = torsion.source_scan_calculation
        if calc is not None and _detect_calculation_hard_fail(calc) is not None:
            return HardFailReason.source_calculation_hard_failed_for_required_role

    return None


def _transport_has_property(transport: Transport) -> bool:
    """Return True when at least one structured transport property is populated."""
    return (
        transport.sigma_angstrom is not None
        or transport.epsilon_over_k_k is not None
        or transport.dipole_debye is not None
        or transport.polarizability_angstrom3 is not None
        or transport.rotational_relaxation is not None
    )


def _transport_has_invalid_lj_pair(transport: Transport) -> bool:
    """Return True when exactly one LJ pair field is populated."""
    return (transport.sigma_angstrom is None) != (transport.epsilon_over_k_k is None)


def _transport_hard_fail_source_roles(
    transport: Transport,
) -> frozenset[TransportCalculationRole]:
    """Return source roles whose linked calculation failure should hard-fail."""
    roles = {TransportCalculationRole.full_transport}
    if transport.dipole_debye is not None:
        roles.add(TransportCalculationRole.dipole)
    if transport.polarizability_angstrom3 is not None:
        roles.add(TransportCalculationRole.polarizability)
    if _transport_has_property(transport):
        roles.add(TransportCalculationRole.supporting_geometry)
    return frozenset(roles)


def _detect_transport_hard_fail(transport: Transport) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``transport`` if a structural failure is present."""
    if transport.species_entry_id is None or transport.species_entry is None:
        return HardFailReason.species_entry_missing

    if not _transport_has_property(transport):
        return HardFailReason.no_transport_property_present

    if _transport_has_invalid_lj_pair(transport):
        return HardFailReason.invalid_lj_pair

    required_roles = _transport_hard_fail_source_roles(transport)
    for link in transport.source_calculations:
        if link.role not in required_roles:
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


def _empty_evaluation_for_missing_thermo(
    thermo_id: Optional[int],
    rubric: EvidenceRubric,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing thermo row."""
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=thermo_id,
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
        hard_fail_reason=HardFailReason.thermo_missing,
        check_results=(),
    )


def _empty_evaluation_for_missing_statmech(
    statmech_id: Optional[int],
    rubric: EvidenceRubric,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing statmech row."""
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=statmech_id,
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
        hard_fail_reason=HardFailReason.statmech_missing,
        check_results=(),
    )


def _empty_evaluation_for_missing_transition_state_entry(
    ts_entry_id: Optional[int],
    rubric: EvidenceRubric,
    reason: HardFailReason = HardFailReason.transition_state_entry_missing,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing TS entry."""
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=ts_entry_id,
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
        hard_fail_reason=reason,
        check_results=(),
    )


def _detect_transition_state_entry_hard_fail(
    ts_entry: TransitionStateEntry,
) -> Optional[HardFailReason]:
    """Return a hard-fail reason for ``ts_entry`` if a structural failure is present.

    Mirrors §6.1 of the TS rubric spec. The set is intentionally narrow —
    only discrete, evidenced structural failures qualify. Missing IRC,
    missing path-search, missing SP, missing review, and missing
    SMILES/mol must NOT hard-fail here.
    """
    if ts_entry.transition_state_id is None or ts_entry.transition_state is None:
        return HardFailReason.transition_state_parent_missing

    parent = ts_entry.transition_state
    if parent.reaction_entry_id is None or parent.reaction_entry is None:
        return HardFailReason.reaction_entry_missing

    if ts_entry.status is TransitionStateEntryStatus.rejected:
        return HardFailReason.ts_entry_status_rejected

    if ts_entry.multiplicity is not None and ts_entry.multiplicity < 1:
        return HardFailReason.multiplicity_invalid

    source = _ts_source_calculations(ts_entry)

    # Any source-calc geometry validation failure structurally compromises
    # the TS evidence (spec §6.1).
    for calc in source:
        gv = calc.geometry_validation
        if gv is not None and gv.validation_status is ValidationStatus.fail:
            return HardFailReason.geometry_validation_failed_for_source_calculation

    # If every source calc is itself a hard fail at the calculation rubric
    # level, the TS entry inherits a hard fail.
    if source and all(
        _detect_calculation_hard_fail(calc) is not None for calc in source
    ):
        return HardFailReason.all_source_calculations_hard_failed

    # Frequency contradictions for status-validated TS entries are hard
    # fails per spec §8.2.
    if ts_entry.status in {
        TransitionStateEntryStatus.optimized,
        TransitionStateEntryStatus.validated,
    }:
        freq = _ts_representative_freq_result(ts_entry)
        if freq is not None and freq.n_imag is not None:
            if freq.n_imag == 0:
                return (
                    HardFailReason.frequency_source_has_zero_imaginary_modes_for_validated_ts
                )
            if freq.n_imag > 1:
                return (
                    HardFailReason.frequency_source_has_multiple_imaginary_modes_for_validated_ts
                )

    return None


def _empty_evaluation_for_missing_transport(
    transport_id: Optional[int],
    rubric: EvidenceRubric,
) -> EvidenceEvaluation:
    """Return a structured ``hard_failed`` evaluation for a missing transport row."""
    return EvidenceEvaluation(
        record_type=rubric.record_type,
        record_id=transport_id,
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
        hard_fail_reason=HardFailReason.transport_missing,
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


def evaluate_loaded_thermo(
    thermo: Thermo | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded thermo record.

    This entrypoint is pure over the ORM object graph it receives: it
    does not perform a lookup and the check runners must not issue
    their own queries. Callers are responsible for eager-loading the
    relationships required by ``computed_thermo_v1``.
    """
    rubric = COMPUTED_THERMO_V1
    if thermo is None:
        return _empty_evaluation_for_missing_thermo(None, rubric)

    hard_fail = _detect_thermo_hard_fail(thermo)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        if (
            hard_fail is HardFailReason.source_calculation_hard_failed_for_required_role
            and spec.name == "geometry_validation_not_failed_for_source_calculations"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(thermo)
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
        record_id=thermo.id,
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


def evaluate_loaded_statmech(
    statmech: Statmech | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded statmech record.

    This entrypoint is pure over the ORM object graph it receives: it
    does not perform a lookup and the check runners must not issue
    their own queries. Callers are responsible for eager-loading the
    relationships required by ``computed_statmech_v1``.
    """
    rubric = COMPUTED_STATMECH_V1
    if statmech is None:
        return _empty_evaluation_for_missing_statmech(None, rubric)

    hard_fail = _detect_statmech_hard_fail(statmech)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        if (
            hard_fail is HardFailReason.source_calculation_hard_failed_for_required_role
            and spec.name == "geometry_validation_not_failed_for_source_calculations"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(statmech)
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
        record_id=statmech.id,
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


def evaluate_loaded_transport(
    transport: Transport | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded transport record.

    This entrypoint is pure over the ORM object graph it receives: it
    does not perform a lookup and the check runners must not issue
    their own queries. Callers are responsible for eager-loading the
    relationships required by ``computed_transport_v1``.
    """
    rubric = COMPUTED_TRANSPORT_V1
    if transport is None:
        return _empty_evaluation_for_missing_transport(None, rubric)

    hard_fail = _detect_transport_hard_fail(transport)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        if (
            hard_fail is HardFailReason.source_calculation_hard_failed_for_required_role
            and spec.name == "geometry_validation_not_failed_for_source_calculations"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(transport)
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
        record_id=transport.id,
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


def evaluate_loaded_transition_state_entry(
    transition_state_entry: TransitionStateEntry | None,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for a loaded TS entry.

    Pure over the ORM object graph it receives — no queries are issued
    from inside this function or any check runner. Callers (typically the
    read serializer or the session/id wrapper below) are responsible for
    eager-loading the relationships required by
    ``computed_transition_state_v1``: the parent ``TransitionState`` and
    its ``ReactionEntry`` (and that entry's ``ChemReaction``), the
    directly-attached ``calculations`` with their result blocks,
    ``geometry_validation``, ``artifacts``, ``parent_dependencies`` /
    ``child_dependencies`` (both directions), and the linked calcs along
    each dependency edge.
    """
    rubric = COMPUTED_TRANSITION_STATE_V1
    if transition_state_entry is None:
        return _empty_evaluation_for_missing_transition_state_entry(None, rubric)

    hard_fail = _detect_transition_state_entry_hard_fail(transition_state_entry)

    check_results: list[EvidenceCheckResult] = []
    for spec in rubric.checks:
        # Suppress the geometry-validation warning check when the
        # underlying signal already hard-failed the entry; the hard-fail
        # carries the same evidence and the warning would be noise.
        if (
            hard_fail is HardFailReason.geometry_validation_failed_for_source_calculation
            and spec.name == "geometry_validation_not_failed_for_source_calculations"
        ):
            outcome = EvidenceOutcome.not_applicable
        else:
            outcome = spec.runner(transition_state_entry)
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
        record_id=transition_state_entry.id,
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


def evaluate_computed_thermo(
    session: Session,
    thermo_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one computed thermo row."""
    statement = (
        select(Thermo)
        .where(Thermo.id == thermo_id)
        .options(
            selectinload(Thermo.species_entry),
            selectinload(Thermo.nasa),
            selectinload(Thermo.points),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.artifacts),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.sp_result),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.opt_result),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.freq_result),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.irc_result),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.scan_result),
            selectinload(Thermo.source_calculations)
            .selectinload(ThermoSourceCalculation.calculation)
            .selectinload(Calculation.path_search_result),
        )
    )
    thermo = session.scalars(statement).one_or_none()
    if thermo is None:
        return _empty_evaluation_for_missing_thermo(thermo_id, COMPUTED_THERMO_V1)
    return evaluate_loaded_thermo(thermo)


def evaluate_computed_statmech(
    session: Session,
    statmech_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one computed statmech row."""
    statement = (
        select(Statmech)
        .where(Statmech.id == statmech_id)
        .options(
            selectinload(Statmech.species_entry),
            selectinload(Statmech.frequency_scale_factor),
            selectinload(Statmech.torsions).selectinload(StatmechTorsion.coordinates),
            selectinload(Statmech.torsions)
            .selectinload(StatmechTorsion.source_scan_calculation)
            .selectinload(Calculation.artifacts),
            selectinload(Statmech.torsions)
            .selectinload(StatmechTorsion.source_scan_calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(Statmech.torsions)
            .selectinload(StatmechTorsion.source_scan_calculation)
            .selectinload(Calculation.scan_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.artifacts),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.sp_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.opt_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.freq_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.irc_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.scan_result),
            selectinload(Statmech.source_calculations)
            .selectinload(StatmechSourceCalculation.calculation)
            .selectinload(Calculation.path_search_result),
        )
    )
    statmech = session.scalars(statement).one_or_none()
    if statmech is None:
        return _empty_evaluation_for_missing_statmech(statmech_id, COMPUTED_STATMECH_V1)
    return evaluate_loaded_statmech(statmech)


def evaluate_computed_transport(
    session: Session,
    transport_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one computed transport row."""
    statement = (
        select(Transport)
        .where(Transport.id == transport_id)
        .options(
            selectinload(Transport.species_entry),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.artifacts),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.sp_result),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.opt_result),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.freq_result),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.irc_result),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.scan_result),
            selectinload(Transport.source_calculations)
            .selectinload(TransportSourceCalculation.calculation)
            .selectinload(Calculation.path_search_result),
        )
    )
    transport = session.scalars(statement).one_or_none()
    if transport is None:
        return _empty_evaluation_for_missing_transport(
            transport_id, COMPUTED_TRANSPORT_V1
        )
    return evaluate_loaded_transport(transport)


def evaluate_computed_transition_state_entry(
    session: Session,
    transition_state_entry_id: int,
) -> EvidenceEvaluation:
    """Evaluate deterministic evidence completeness for one TS entry.

    Session/id wrapper around :func:`evaluate_loaded_transition_state_entry`.
    The wrapper eager-loads the graph the rubric inspects so the loaded
    evaluator and its check runners issue no further queries. Read
    serializers that already have a loaded TS entry should call the
    loaded evaluator directly to avoid the duplicate query.
    """
    statement = (
        select(TransitionStateEntry)
        .where(TransitionStateEntry.id == transition_state_entry_id)
        .options(
            selectinload(TransitionStateEntry.transition_state)
            .selectinload(TransitionState.reaction_entry)
            .selectinload(ReactionEntry.reaction),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.artifacts
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.geometry_validation
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.sp_result
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.opt_result
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.freq_result
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.irc_result
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.scan_result
            ),
            selectinload(TransitionStateEntry.calculations).selectinload(
                Calculation.path_search_result
            ),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.artifacts),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.sp_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.opt_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.freq_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.irc_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.parent_dependencies)
            .selectinload(CalculationDependency.child_calculation)
            .selectinload(Calculation.path_search_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.artifacts),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.geometry_validation),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.sp_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.opt_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.freq_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.scan_result),
            selectinload(TransitionStateEntry.calculations)
            .selectinload(Calculation.child_dependencies)
            .selectinload(CalculationDependency.parent_calculation)
            .selectinload(Calculation.path_search_result),
        )
    )
    ts_entry = session.scalars(statement).one_or_none()
    if ts_entry is None:
        return _empty_evaluation_for_missing_transition_state_entry(
            transition_state_entry_id, COMPUTED_TRANSITION_STATE_V1
        )
    return evaluate_loaded_transition_state_entry(ts_entry)
