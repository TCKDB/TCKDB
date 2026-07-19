"""Code-defined rubrics for the deterministic trust evaluator.

This module is intentionally small: a check-runner library plus the
first MVP rubric, ``computed_calculation_v1``. Each rubric is a tuple
of :class:`EvidenceCheckSpec` rows whose ``runner`` is a pure function
over an already-loaded :class:`~app.db.models.calculation.Calculation`
graph.

Adding more rubrics (kinetics, thermo, statmech, transport, experimental)
should follow the same shape:

* declare a runner per check,
* assemble them into a :class:`EvidenceRubric`,
* register the rubric under :data:`RUBRIC_REGISTRY`.

A real plugin system is deferred until at least three rubrics exist;
the simple dict-based registry below covers everything we need today.
"""

from __future__ import annotations

from typing import Optional

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationQuality,
    CalculationType,
    KineticsCalculationRole,
    KineticsModelKind,
    ReactionRole,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    ThermoCalculationRole,
    TransitionStateEntryStatus,
    TransportCalculationRole,
    ValidationStatus,
)
from app.db.models.kinetics import Kinetics
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport
from app.services.trust.models import (
    EvidenceCheckKind,
    EvidenceCheckSpec,
    EvidenceOutcome,
    EvidenceRubric,
)

_TYPES_WITH_OUTPUT_GEOMETRY: frozenset[CalculationType] = frozenset(
    {CalculationType.opt, CalculationType.irc, CalculationType.path_search}
)
"""Calculation types that the spec (§9.5) expects to produce an output geometry.

``conf`` and ``sp`` calcs do not produce a separate output geometry; ``freq``
reads back the input geometry; ``scan`` produces a sample, not a single output.
"""

_TYPES_REQUIRING_GEOMETRY_VALIDATION: frozenset[CalculationType] = frozenset(
    {CalculationType.opt}
)
"""Calculation types where geometry validation is expected to be applicable.

The spec lists "opt or ts"; TS-ness is encoded via ownership
(``transition_state_entry_id``) rather than its own ``CalculationType``,
so we trigger geometry-validation checks for any ``opt`` calc regardless
of whether the owner is a species or a transition state.
"""

_TYPES_EXPECTED_TO_HAVE_PARENTS: frozenset[CalculationType] = frozenset(
    {
        CalculationType.freq,
        CalculationType.sp,
        CalculationType.irc,
        CalculationType.scan,
    }
)
"""Calculation types where an upstream parent calculation (opt → freq, opt → sp, …)
is expected. Used by ``calculation_dependencies_present_when_expected``.
"""


def _bool_outcome(passed: bool) -> EvidenceOutcome:
    """Return ``passed`` or ``missing`` from a boolean predicate."""
    return EvidenceOutcome.passed if passed else EvidenceOutcome.missing


def _check_calculation_has_owner(calc: Calculation) -> EvidenceOutcome:
    """Return passed when exactly one owner FK is populated.

    The DB ``one_owner`` constraint enforces this; the check restates
    it as positive evidence so the report explicitly lists "owner
    present" instead of silently assuming it.
    """
    has_species = calc.species_entry_id is not None
    has_ts = calc.transition_state_entry_id is not None
    return _bool_outcome(has_species ^ has_ts)


def _check_calculation_type_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when ``calculation.type`` is set."""
    return _bool_outcome(calc.type is not None)


def _check_level_of_theory_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when the calc resolves to a level_of_theory row."""
    return _bool_outcome(calc.lot_id is not None)


def _check_software_release_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when the calc resolves to a software_release row."""
    return _bool_outcome(calc.software_release_id is not None)


def _check_workflow_tool_release_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when the calc resolves to a workflow_tool_release row."""
    return _bool_outcome(calc.workflow_tool_release_id is not None)


def _check_input_geometry_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when at least one input geometry is linked."""
    return _bool_outcome(len(calc.input_geometries) >= 1)


def _check_output_geometry_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when an output geometry is present for geometry-producing types.

    Not applicable for ``sp``, ``freq``, ``scan``, ``conf`` calculations,
    matching the spec's note that this check applies only when the calc
    type produces a geometry (§9.5).
    """
    if calc.type not in _TYPES_WITH_OUTPUT_GEOMETRY:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(len(calc.output_geometries) >= 1)


_RESULT_BLOCK_BY_TYPE: dict[CalculationType, str] = {
    CalculationType.sp: "sp_result",
    CalculationType.opt: "opt_result",
    CalculationType.freq: "freq_result",
    CalculationType.irc: "irc_result",
    CalculationType.scan: "scan_result",
    CalculationType.path_search: "path_search_result",
}


def _check_result_block_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when the appropriate ``calc_*_result`` row is attached.

    ``conf`` calculations have no dedicated result block; the check
    returns ``not_applicable`` for that type rather than silently
    failing.
    """
    attr_name = _RESULT_BLOCK_BY_TYPE.get(calc.type)
    if attr_name is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(getattr(calc, attr_name) is not None)


def _check_quality_recorded(calc: Calculation) -> EvidenceOutcome:
    """Return passed when ``quality`` has been curated past the default ``raw``.

    A default ``raw`` quality counts as "not recorded" for the purposes
    of this evidence check; ``rejected`` would normally short-circuit
    into a hard-fail before reaching here, so we do not list it as
    passed evidence either.
    """
    if calc.quality is None:
        return EvidenceOutcome.missing
    if calc.quality is CalculationQuality.curated:
        return EvidenceOutcome.passed
    return EvidenceOutcome.missing


def _check_geometry_validation_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when geometry-validation evidence is attached for opt calcs.

    Not applicable for non-opt calculation types (§9.5).
    """
    if calc.type not in _TYPES_REQUIRING_GEOMETRY_VALIDATION:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(calc.geometry_validation is not None)


def _check_geometry_validation_passed_or_warning(calc: Calculation) -> EvidenceOutcome:
    """Return passed/warning/not_applicable based on geometry-validation status.

    ``ValidationStatus.fail`` is intentionally NOT reported here — it is
    promoted to a hard-fail signal by the evaluator before this check
    is reached. A warning row produces a warning outcome (advisory only,
    zero weight).
    """
    if calc.geometry_validation is None:
        return EvidenceOutcome.not_applicable
    status = calc.geometry_validation.validation_status
    if status is ValidationStatus.passed:
        return EvidenceOutcome.passed
    if status is ValidationStatus.warning:
        return EvidenceOutcome.warning
    return EvidenceOutcome.not_applicable


def _check_scf_stability_present_if_claimed(calc: Calculation) -> EvidenceOutcome:
    """Return passed when SCF stability evidence is attached.

    Absence of a ``calc_scf_stability`` row means "not checked", per
    the model docstring — which the spec accepts as ``not_applicable``
    rather than ``missing``. Producers that *do* attach a row get
    credit for the explicit declaration.
    """
    if calc.scf_stability is None:
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.passed


def _check_artifacts_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when at least one calculation_artifact is attached."""
    return _bool_outcome(len(calc.artifacts) >= 1)


def _check_parameters_parsed(calc: Calculation) -> EvidenceOutcome:
    """Return passed when ESS execution parameters are attached.

    Either an EAV row in ``calculation_parameter`` OR a populated
    ``parameters_json`` snapshot counts as "parameters parsed",
    matching the three-layer provenance design (vocab / EAV rows /
    JSONB snapshot).
    """
    if calc.parameters and len(calc.parameters) >= 1:
        return EvidenceOutcome.passed
    if calc.parameters_json:
        return EvidenceOutcome.passed
    return EvidenceOutcome.missing


def _check_calculation_dependencies_present(calc: Calculation) -> EvidenceOutcome:
    """Return passed when an expected upstream parent dependency exists.

    Applies only to types where a parent is expected (freq depends on
    opt, sp depends on opt, …). ``opt`` and ``conf`` are not_applicable
    because they typically have no parent in this schema.
    """
    if calc.type not in _TYPES_EXPECTED_TO_HAVE_PARENTS:
        return EvidenceOutcome.not_applicable
    # child_dependencies = rows where THIS calc is the child (i.e. has parents).
    return _bool_outcome(len(calc.child_dependencies) >= 1)


_ARRHENIUS_MODEL_KINDS: frozenset[KineticsModelKind] = frozenset(
    {
        KineticsModelKind.arrhenius,
        KineticsModelKind.modified_arrhenius,
    }
)

_KINETICS_STRONG_SOURCE_ROLES: frozenset[KineticsCalculationRole] = frozenset(
    {
        KineticsCalculationRole.reactant_energy,
        KineticsCalculationRole.product_energy,
        KineticsCalculationRole.ts_energy,
        KineticsCalculationRole.freq,
    }
)

_THERMO_STRONG_SOURCE_ROLES: frozenset[ThermoCalculationRole] = frozenset(
    {
        ThermoCalculationRole.opt,
        ThermoCalculationRole.freq,
        ThermoCalculationRole.sp,
        ThermoCalculationRole.composite,
    }
)

_THERMO_REQUIRED_SOURCE_ROLES: frozenset[ThermoCalculationRole] = frozenset(
    {
        ThermoCalculationRole.opt,
        ThermoCalculationRole.freq,
    }
)

_NASA_COEFFICIENT_FIELDS: tuple[str, ...] = (
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


def _kinetics_sources_by_role(
    kinetics: Kinetics, role: KineticsCalculationRole
) -> list:
    """Return kinetics source-calculation links for ``role``."""
    return [link for link in kinetics.source_calculations if link.role is role]


def _kinetics_source_calculations(kinetics: Kinetics) -> list[Calculation]:
    """Return non-null linked source calculations for ``kinetics``."""
    return [
        link.calculation
        for link in kinetics.source_calculations
        if link.calculation is not None
    ]


def _reaction_participant_count(kinetics: Kinetics, role: ReactionRole) -> int:
    """Return the number of loaded reaction-entry structure participants by role."""
    reaction_entry = kinetics.reaction_entry
    if reaction_entry is None:
        return 0
    return sum(
        1
        for participant in reaction_entry.structure_participants
        if participant.role is role
    )


def _check_kinetics_reaction_entry_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when the kinetics row is attached to a reaction_entry."""
    return _bool_outcome(
        kinetics.reaction_entry_id is not None and kinetics.reaction_entry is not None
    )


def _check_kinetics_model_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when the kinetics model kind is set."""
    return _bool_outcome(kinetics.model_kind is not None)


def _check_arrhenius_parameters_complete(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when Arrhenius-family scalar parameters are complete."""
    if kinetics.model_kind not in _ARRHENIUS_MODEL_KINDS:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        kinetics.a is not None
        and kinetics.a_units is not None
        and kinetics.n is not None
        and kinetics.ea_kj_mol is not None
    )


def _check_arrhenius_units_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when Arrhenius A units are populated for Arrhenius models."""
    if kinetics.model_kind not in _ARRHENIUS_MODEL_KINDS:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(kinetics.a_units is not None)


def _check_temperature_range_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when both temperature bounds are populated."""
    return _bool_outcome(kinetics.tmin_k is not None and kinetics.tmax_k is not None)


def _check_temperature_range_valid(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when the populated temperature range is physically plausible."""
    if kinetics.tmin_k is None or kinetics.tmax_k is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(0 < kinetics.tmin_k < kinetics.tmax_k <= 10_000)


def _check_source_calculations_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when at least one kinetics_source_calculation row is linked."""
    return _bool_outcome(len(kinetics.source_calculations) >= 1)


def _check_ts_energy_source_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when a TS energy source calculation is linked."""
    return _bool_outcome(
        len(_kinetics_sources_by_role(kinetics, KineticsCalculationRole.ts_energy)) >= 1
    )


def _check_reactant_energy_sources_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when reactant-energy sources cover the loaded reactants."""
    expected = _reaction_participant_count(kinetics, ReactionRole.reactant)
    if expected == 0:
        return EvidenceOutcome.not_applicable
    actual = len(
        _kinetics_sources_by_role(kinetics, KineticsCalculationRole.reactant_energy)
    )
    return _bool_outcome(actual >= expected)


def _check_product_energy_sources_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when product-energy sources cover the loaded products."""
    expected = _reaction_participant_count(kinetics, ReactionRole.product)
    if expected == 0:
        return EvidenceOutcome.not_applicable
    actual = len(
        _kinetics_sources_by_role(kinetics, KineticsCalculationRole.product_energy)
    )
    return _bool_outcome(actual >= expected)


def _check_frequency_source_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when a frequency source calculation is linked."""
    return _bool_outcome(
        len(_kinetics_sources_by_role(kinetics, KineticsCalculationRole.freq)) >= 1
    )


def _check_master_equation_or_fit_source_present_if_applicable(
    kinetics: Kinetics,
) -> EvidenceOutcome:
    """Return passed for explicit master-equation or fit-source evidence.

    The current schema has only Arrhenius-family model kinds, so there is no
    deterministic way to infer that a master-equation or fit-source link is
    required. When either role is present, credit the evidence; otherwise skip.
    """
    has_source = any(
        link.role
        in {
            KineticsCalculationRole.master_equation,
            KineticsCalculationRole.fit_source,
        }
        for link in kinetics.source_calculations
    )
    return EvidenceOutcome.passed if has_source else EvidenceOutcome.not_applicable


def _check_source_calculation_lot_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when all linked source calculations have a level of theory."""
    calcs = _kinetics_source_calculations(kinetics)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.lot_id is not None for calc in calcs))


def _check_source_calculation_software_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when all linked source calculations have software release metadata."""
    calcs = _kinetics_source_calculations(kinetics)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.software_release_id is not None for calc in calcs))


def _check_workflow_tool_release_present_for_source_calculations(
    kinetics: Kinetics,
) -> EvidenceOutcome:
    """Return passed when kinetics or at least one source calc carries workflow-tool metadata."""
    if kinetics.workflow_tool_release_id is not None:
        return EvidenceOutcome.passed
    return _bool_outcome(
        any(
            calc.workflow_tool_release_id is not None
            for calc in _kinetics_source_calculations(kinetics)
        )
    )


def _check_source_calculation_artifacts_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when at least one linked source calculation retains artifacts."""
    return _bool_outcome(
        any(
            len(calc.artifacts) >= 1 for calc in _kinetics_source_calculations(kinetics)
        )
    )


def _check_source_calculation_result_blocks_present(
    kinetics: Kinetics,
) -> EvidenceOutcome:
    """Return passed when every linked source calculation has its expected result block."""
    calcs = _kinetics_source_calculations(kinetics)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            _check_result_block_present(calc) is EvidenceOutcome.passed
            for calc in calcs
        )
    )


def _check_geometry_validation_present_for_source_calculations(
    kinetics: Kinetics,
) -> EvidenceOutcome:
    """Return passed when strong source calculations carry geometry validation."""
    strong_calcs = [
        link.calculation
        for link in kinetics.source_calculations
        if link.role in _KINETICS_STRONG_SOURCE_ROLES and link.calculation is not None
    ]
    if not strong_calcs:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        all(calc.geometry_validation is not None for calc in strong_calcs)
    )


def _check_geometry_validation_not_failed_for_source_calculations(
    kinetics: Kinetics,
) -> EvidenceOutcome:
    """Return passed/warning based on geometry-validation status on source calcs."""
    validations = [
        link.calculation.geometry_validation
        for link in kinetics.source_calculations
        if link.role in _KINETICS_STRONG_SOURCE_ROLES
        and link.calculation is not None
        and link.calculation.geometry_validation is not None
    ]
    if not validations:
        return EvidenceOutcome.not_applicable
    if any(
        validation.validation_status is ValidationStatus.warning
        for validation in validations
    ):
        return EvidenceOutcome.warning
    if any(
        validation.validation_status is ValidationStatus.fail
        for validation in validations
    ):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.passed


def _check_irc_evidence_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when explicit IRC evidence is linked or present on a source calc."""
    if _kinetics_sources_by_role(kinetics, KineticsCalculationRole.irc):
        return EvidenceOutcome.passed
    return _bool_outcome(
        any(
            calc.irc_result is not None
            for calc in _kinetics_source_calculations(kinetics)
        )
    )


def _check_path_search_evidence_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when a linked source calculation has path-search evidence."""
    return _bool_outcome(
        any(
            calc.path_search_result is not None
            for calc in _kinetics_source_calculations(kinetics)
        )
    )


def _check_uncertainty_present(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when at least one kinetics uncertainty field is populated."""
    has_a_uncertainty = (
        kinetics.a_uncertainty is not None and kinetics.a_uncertainty_kind is not None
    )
    return _bool_outcome(
        has_a_uncertainty
        or kinetics.n_uncertainty is not None
        or kinetics.ea_uncertainty_kj_mol is not None
    )


def _check_tunneling_metadata_present_if_claimed(kinetics: Kinetics) -> EvidenceOutcome:
    """Return passed when a declared tunneling model has a non-empty identifier."""
    if kinetics.tunneling_model is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(bool(kinetics.tunneling_model.strip()))


def _thermo_sources_by_role(thermo: Thermo, role: ThermoCalculationRole) -> list:
    """Return thermo source-calculation links for ``role``."""
    return [link for link in thermo.source_calculations if link.role is role]


def _thermo_source_calculations(thermo: Thermo) -> list[Calculation]:
    """Return non-null linked source calculations for ``thermo``."""
    return [
        link.calculation
        for link in thermo.source_calculations
        if link.calculation is not None
    ]


def _thermo_source_calculations_for_roles(
    thermo: Thermo, roles: frozenset[ThermoCalculationRole]
) -> list[Calculation]:
    """Return non-null source calculations whose link role is in ``roles``."""
    return [
        link.calculation
        for link in thermo.source_calculations
        if link.role in roles and link.calculation is not None
    ]


def _thermo_has_scalar_representation(thermo: Thermo) -> bool:
    """Return True when scalar H298 and/or S298 values are populated."""
    return thermo.h298_kj_mol is not None or thermo.s298_j_mol_k is not None


def _thermo_has_nasa_representation(thermo: Thermo) -> bool:
    """Return True when a NASA row with all coefficient fields is attached."""
    nasa = thermo.nasa
    if nasa is None:
        return False
    return all(getattr(nasa, field) is not None for field in _NASA_COEFFICIENT_FIELDS)


def _thermo_has_point_representation(thermo: Thermo) -> bool:
    """Return True when at least one tabulated point carries a thermo value."""
    return any(
        point.cp_j_mol_k is not None
        or point.h_kj_mol is not None
        or point.s_j_mol_k is not None
        or point.g_kj_mol is not None
        for point in thermo.points
    )


def _thermo_has_nasa9_representation(thermo: Thermo) -> bool:
    """Return True when at least one NASA-9 polynomial interval is attached."""
    return bool(thermo.nasa9_intervals)


def _thermo_has_wilhoit_representation(thermo: Thermo) -> bool:
    """Return True when a Wilhoit heat-capacity form is attached."""
    return thermo.wilhoit is not None


def _thermo_has_any_representation(thermo: Thermo) -> bool:
    """Return True when scalar, NASA-7, point, NASA-9, or Wilhoit evidence exists."""
    return (
        _thermo_has_scalar_representation(thermo)
        or _thermo_has_nasa_representation(thermo)
        or _thermo_has_point_representation(thermo)
        or _thermo_has_nasa9_representation(thermo)
        or _thermo_has_wilhoit_representation(thermo)
    )


def _thermo_range_is_present(thermo: Thermo) -> bool:
    """Return True when a top-level, NASA-7, or NASA-9 temperature range exists.

    Wilhoit is a continuous form with no intrinsic piecewise range, so it only
    contributes a range via the top-level ``tmin_k``/``tmax_k`` bounds.
    """
    if thermo.tmin_k is not None or thermo.tmax_k is not None:
        return True
    if _thermo_has_nasa9_representation(thermo):
        return True
    nasa = thermo.nasa
    if nasa is None:
        return False
    return nasa.t_low is not None or nasa.t_mid is not None or nasa.t_high is not None


# Upper sanity bound for thermochemical temperature ranges. Set at the NASA
# Glenn (Gordon & McBride) maximum of 20,000 K: canonical NASA-9 fits use a
# 6,000-20,000 K high-temperature interval, and NASA-7 / top-level ranges never
# legitimately exceed this. A tighter cap would hard-fail valid high-T data.
_MAX_THERMO_TEMPERATURE_K = 20_000


def _thermo_range_is_valid(thermo: Thermo) -> bool:
    """Return True when all populated thermo temperature ranges are plausible."""
    if thermo.tmin_k is not None or thermo.tmax_k is not None:
        if thermo.tmin_k is None or thermo.tmax_k is None:
            return False
        if not (0 < thermo.tmin_k < thermo.tmax_k <= _MAX_THERMO_TEMPERATURE_K):
            return False

    for interval in thermo.nasa9_intervals:
        if not (
            0 < interval.t_min_k < interval.t_max_k <= _MAX_THERMO_TEMPERATURE_K
        ):
            return False

    nasa = thermo.nasa
    if nasa is None:
        return True
    if nasa.t_low is None and nasa.t_mid is None and nasa.t_high is None:
        return True
    if nasa.t_low is None or nasa.t_mid is None or nasa.t_high is None:
        return False
    return 0 < nasa.t_low < nasa.t_mid < nasa.t_high <= _MAX_THERMO_TEMPERATURE_K


def _check_thermo_species_entry_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when the thermo row is attached to a species_entry."""
    return _bool_outcome(
        thermo.species_entry_id is not None and thermo.species_entry is not None
    )


def _check_thermo_origin_is_computed(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when the thermo record declares computed origin."""
    return _bool_outcome(thermo.scientific_origin is ScientificOriginKind.computed)


def _check_thermo_model_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when a deterministic thermo representation implies a model."""
    return _bool_outcome(_thermo_has_any_representation(thermo))


def _check_scalar_thermo_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed for scalar H298/S298 evidence, otherwise N/A if another model exists."""
    if _thermo_has_scalar_representation(thermo):
        return EvidenceOutcome.passed
    if _thermo_has_any_representation(thermo):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.missing


def _check_nasa_coefficients_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when an attached NASA row has complete coefficient blocks."""
    if thermo.nasa is None:
        return (
            EvidenceOutcome.not_applicable
            if _thermo_has_any_representation(thermo)
            else EvidenceOutcome.missing
        )
    return _bool_outcome(_thermo_has_nasa_representation(thermo))


def _check_thermo_points_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when attached thermo points carry at least one value."""
    if thermo.points:
        return _bool_outcome(_thermo_has_point_representation(thermo))
    return (
        EvidenceOutcome.not_applicable
        if _thermo_has_any_representation(thermo)
        else EvidenceOutcome.missing
    )


def _check_at_least_one_thermo_representation_present(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when scalar, NASA-7, point, NASA-9, or Wilhoit evidence exists."""
    return _bool_outcome(_thermo_has_any_representation(thermo))


def _check_temperature_range_present_if_applicable(thermo: Thermo) -> EvidenceOutcome:
    """Return passed for range-bearing thermo representations when bounds exist.

    NASA-7, NASA-9, and top-level bounds carry an intrinsic temperature range;
    Wilhoit and pure-scalar/point records without top-level bounds do not, so the
    check is not applicable for them.
    """
    if (
        thermo.nasa is None
        and not _thermo_has_nasa9_representation(thermo)
        and thermo.tmin_k is None
        and thermo.tmax_k is None
    ):
        return EvidenceOutcome.not_applicable
    return _bool_outcome(_thermo_range_is_present(thermo))


def _check_thermo_temperature_range_valid(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when a populated thermo temperature range is valid."""
    if not _thermo_range_is_present(thermo):
        return EvidenceOutcome.not_applicable
    return _bool_outcome(_thermo_range_is_valid(thermo))


def _check_thermo_source_calculations_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when at least one thermo_source_calculation row is linked."""
    return _bool_outcome(len(thermo.source_calculations) >= 1)


def _check_opt_source_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when an opt source calculation is linked."""
    return _bool_outcome(
        len(_thermo_sources_by_role(thermo, ThermoCalculationRole.opt)) >= 1
    )


def _check_freq_source_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when a frequency source calculation is linked."""
    return _bool_outcome(
        len(_thermo_sources_by_role(thermo, ThermoCalculationRole.freq)) >= 1
    )


def _check_sp_or_composite_source_present_if_applicable(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when SP/composite source evidence is linked.

    The current schema cannot distinguish pure empirical/scalar import paths from
    SP-derived computed thermo. Treat the check as applicable for computed rows.
    """
    return _bool_outcome(
        len(_thermo_sources_by_role(thermo, ThermoCalculationRole.sp))
        + len(_thermo_sources_by_role(thermo, ThermoCalculationRole.composite))
        >= 1
    )


def _check_thermo_source_calculation_lot_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when all linked source calculations have a level of theory."""
    calcs = _thermo_source_calculations(thermo)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.lot_id is not None for calc in calcs))


def _check_thermo_source_calculation_software_present(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when all linked source calculations have software metadata."""
    calcs = _thermo_source_calculations(thermo)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.software_release_id is not None for calc in calcs))


def _check_thermo_source_calculation_workflow_tool_present(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when thermo or one source calc carries workflow-tool metadata."""
    if thermo.workflow_tool_release_id is not None:
        return EvidenceOutcome.passed
    return _bool_outcome(
        any(
            calc.workflow_tool_release_id is not None
            for calc in _thermo_source_calculations(thermo)
        )
    )


def _check_thermo_source_calculation_artifacts_present(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when at least one linked source calculation retains artifacts."""
    return _bool_outcome(
        any(len(calc.artifacts) >= 1 for calc in _thermo_source_calculations(thermo))
    )


def _check_thermo_source_calculation_result_blocks_present(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when every linked source calculation has its expected result block."""
    calcs = _thermo_source_calculations(thermo)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            _check_result_block_present(calc) is EvidenceOutcome.passed
            for calc in calcs
        )
    )


def _check_source_calculation_has_non_hard_failed_evidence(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when linked source calculations avoid deterministic hard-fail signals."""
    calcs = _thermo_source_calculations(thermo)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            calc.quality is not CalculationQuality.rejected
            and (
                calc.geometry_validation is None
                or calc.geometry_validation.validation_status
                is not ValidationStatus.fail
            )
            for calc in calcs
        )
    )


def _check_thermo_geometry_validation_present_for_source_calculations(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed when strong source calculations carry geometry validation."""
    strong_calcs = _thermo_source_calculations_for_roles(
        thermo, _THERMO_STRONG_SOURCE_ROLES
    )
    if not strong_calcs:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        all(calc.geometry_validation is not None for calc in strong_calcs)
    )


def _check_thermo_geometry_validation_not_failed_for_source_calculations(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Return passed/warning based on geometry-validation status on source calcs."""
    validations = [
        calc.geometry_validation
        for calc in _thermo_source_calculations_for_roles(
            thermo, _THERMO_STRONG_SOURCE_ROLES
        )
        if calc.geometry_validation is not None
    ]
    if not validations:
        return EvidenceOutcome.not_applicable
    if any(
        validation.validation_status is ValidationStatus.warning
        for validation in validations
    ):
        return EvidenceOutcome.warning
    if any(
        validation.validation_status is ValidationStatus.fail
        for validation in validations
    ):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.passed


def _check_statmech_present(thermo: Thermo) -> EvidenceOutcome:
    """Skip statmech linkage until a thermo-to-statmech relationship exists."""
    return EvidenceOutcome.not_applicable


def _check_frequency_scale_factor_present_if_applicable(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Require a frequency scale factor only when a frequency source is linked.

    The current thermo schema has no direct scale-factor relationship, so the
    linked frequency role makes this check applicable but currently missing.
    """
    if not _thermo_sources_by_role(thermo, ThermoCalculationRole.freq):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.missing


def _check_thermo_uncertainty_present(thermo: Thermo) -> EvidenceOutcome:
    """Return passed when at least one scalar uncertainty field is populated."""
    return _bool_outcome(
        thermo.h298_uncertainty_kj_mol is not None
        or thermo.s298_uncertainty_j_mol_k is not None
    )


def _check_thermo_not_rejected_or_deprecated_if_applicable(
    thermo: Thermo,
) -> EvidenceOutcome:
    """Skip curator status checks until thermo-level review/deprecation is modeled."""
    return EvidenceOutcome.not_applicable


_STATMECH_STRONG_SOURCE_ROLES: frozenset[StatmechCalculationRole] = frozenset(
    {
        StatmechCalculationRole.opt,
        StatmechCalculationRole.freq,
        StatmechCalculationRole.scan,
    }
)

_STATMECH_REQUIRED_SOURCE_ROLES: frozenset[StatmechCalculationRole] = frozenset(
    {
        StatmechCalculationRole.opt,
        StatmechCalculationRole.freq,
    }
)

_TORSION_BEARING_STATMECH_TREATMENTS: frozenset[StatmechTreatmentKind] = frozenset(
    {
        StatmechTreatmentKind.rrho_1d,
        StatmechTreatmentKind.rrho_nd,
        StatmechTreatmentKind.rrho_1d_nd,
    }
)


def _statmech_sources_by_role(
    statmech: Statmech, role: StatmechCalculationRole
) -> list:
    """Return statmech source-calculation links for ``role``."""
    return [link for link in statmech.source_calculations if link.role is role]


def _statmech_source_calculations(statmech: Statmech) -> list[Calculation]:
    """Return non-null linked source calculations for ``statmech``."""
    return [
        link.calculation
        for link in statmech.source_calculations
        if link.calculation is not None
    ]


def _statmech_source_calculations_for_roles(
    statmech: Statmech, roles: frozenset[StatmechCalculationRole]
) -> list[Calculation]:
    """Return non-null source calculations whose link role is in ``roles``."""
    return [
        link.calculation
        for link in statmech.source_calculations
        if link.role in roles and link.calculation is not None
    ]


def _statmech_has_torsion_treatment(statmech: Statmech) -> bool:
    """Return True when statmech treatment or rows imply internal rotors."""
    return (
        statmech.statmech_treatment in _TORSION_BEARING_STATMECH_TREATMENTS
        or len(statmech.torsions) >= 1
    )


def _check_statmech_species_entry_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when the statmech row is attached to a species_entry."""
    return _bool_outcome(
        statmech.species_entry_id is not None and statmech.species_entry is not None
    )


def _check_statmech_origin_is_computed(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when the statmech record declares computed origin."""
    return _bool_outcome(statmech.scientific_origin is ScientificOriginKind.computed)


def _check_statmech_treatment_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when the statmech treatment kind is set."""
    return _bool_outcome(statmech.statmech_treatment is not None)


def _check_rigid_rotor_kind_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when rigid-rotor treatment is declared."""
    return _bool_outcome(statmech.rigid_rotor_kind is not None)


def _check_external_symmetry_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when external symmetry is recorded and valid."""
    return _bool_outcome(
        statmech.external_symmetry is not None and statmech.external_symmetry >= 1
    )


def _check_point_group_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when point group metadata is recorded."""
    return _bool_outcome(bool(statmech.point_group))


def _check_is_linear_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when molecular linearity is explicitly recorded."""
    return _bool_outcome(statmech.is_linear is not None)


def _check_statmech_frequency_scale_factor_present_if_applicable(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Require a frequency scale factor only when frequency evidence is linked."""
    if not _statmech_sources_by_role(statmech, StatmechCalculationRole.freq):
        return EvidenceOutcome.not_applicable
    return _bool_outcome(statmech.frequency_scale_factor_id is not None)


def _check_uses_projected_frequencies_recorded(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when projected-frequency handling is explicitly recorded."""
    return _bool_outcome(statmech.uses_projected_frequencies is not None)


def _check_statmech_source_calculations_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when at least one statmech_source_calculation row is linked."""
    return _bool_outcome(len(statmech.source_calculations) >= 1)


def _check_statmech_opt_source_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when an opt source calculation is linked."""
    return _bool_outcome(
        len(_statmech_sources_by_role(statmech, StatmechCalculationRole.opt)) >= 1
    )


def _check_statmech_freq_source_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when a frequency source calculation is linked."""
    return _bool_outcome(
        len(_statmech_sources_by_role(statmech, StatmechCalculationRole.freq)) >= 1
    )


def _check_statmech_sp_or_composite_source_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when SP/composite supporting source evidence is linked."""
    return _bool_outcome(
        len(_statmech_sources_by_role(statmech, StatmechCalculationRole.sp))
        + len(_statmech_sources_by_role(statmech, StatmechCalculationRole.composite))
        >= 1
    )


def _check_scan_source_present_if_torsions_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when torsion-bearing statmech has scan source evidence."""
    if not _statmech_has_torsion_treatment(statmech):
        return EvidenceOutcome.not_applicable
    has_scan_link = (
        len(_statmech_sources_by_role(statmech, StatmechCalculationRole.scan)) >= 1
    )
    has_torsion_scan = any(
        torsion.source_scan_calculation_id is not None for torsion in statmech.torsions
    )
    return _bool_outcome(has_scan_link or has_torsion_scan)


def _check_statmech_source_calculation_lot_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when all linked source calculations have a level of theory."""
    calcs = _statmech_source_calculations(statmech)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.lot_id is not None for calc in calcs))


def _check_statmech_source_calculation_software_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when all linked source calculations have software metadata."""
    calcs = _statmech_source_calculations(statmech)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.software_release_id is not None for calc in calcs))


def _check_statmech_source_calculation_workflow_tool_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when statmech or one source calc carries workflow-tool metadata."""
    if statmech.workflow_tool_release_id is not None:
        return EvidenceOutcome.passed
    return _bool_outcome(
        any(
            calc.workflow_tool_release_id is not None
            for calc in _statmech_source_calculations(statmech)
        )
    )


def _check_statmech_source_calculation_artifacts_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when at least one linked source calculation retains artifacts."""
    return _bool_outcome(
        any(
            len(calc.artifacts) >= 1 for calc in _statmech_source_calculations(statmech)
        )
    )


def _check_statmech_source_calculation_result_blocks_present(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when every linked source calculation has its expected result block."""
    calcs = _statmech_source_calculations(statmech)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            _check_result_block_present(calc) is EvidenceOutcome.passed
            for calc in calcs
        )
    )


def _check_statmech_source_calculation_has_non_hard_failed_evidence(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when linked source calculations avoid deterministic hard-fail signals."""
    calcs = _statmech_source_calculations(statmech)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            calc.quality is not CalculationQuality.rejected
            and (
                calc.geometry_validation is None
                or calc.geometry_validation.validation_status
                is not ValidationStatus.fail
            )
            for calc in calcs
        )
    )


def _check_statmech_geometry_validation_present_for_source_calculations(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when strong source calculations carry geometry validation."""
    strong_calcs = _statmech_source_calculations_for_roles(
        statmech, _STATMECH_STRONG_SOURCE_ROLES
    )
    if not strong_calcs:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        all(calc.geometry_validation is not None for calc in strong_calcs)
    )


def _check_statmech_geometry_validation_not_failed_for_source_calculations(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed/warning based on geometry-validation status on source calcs."""
    validations = [
        calc.geometry_validation
        for calc in _statmech_source_calculations_for_roles(
            statmech, _STATMECH_STRONG_SOURCE_ROLES
        )
        if calc.geometry_validation is not None
    ]
    if not validations:
        return EvidenceOutcome.not_applicable
    if any(
        validation.validation_status is ValidationStatus.warning
        for validation in validations
    ):
        return EvidenceOutcome.warning
    if any(
        validation.validation_status is ValidationStatus.fail
        for validation in validations
    ):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.passed


def _check_torsions_recorded_if_hindered_rotor_treatment(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Return passed when torsion-bearing treatments include torsion rows."""
    if statmech.statmech_treatment not in _TORSION_BEARING_STATMECH_TREATMENTS:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(len(statmech.torsions) >= 1)


def _check_torsion_definitions_present(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when recorded torsions include at least one coordinate."""
    if not _statmech_has_torsion_treatment(statmech):
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        len(statmech.torsions) >= 1
        and all(len(torsion.coordinates) >= 1 for torsion in statmech.torsions)
    )


def _check_torsion_symmetry_recorded(statmech: Statmech) -> EvidenceOutcome:
    """Return passed when recorded torsions include valid symmetry numbers."""
    if not _statmech_has_torsion_treatment(statmech):
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        len(statmech.torsions) >= 1
        and all(
            torsion.symmetry_number is not None and torsion.symmetry_number >= 1
            for torsion in statmech.torsions
        )
    )


def _check_statmech_not_rejected_or_deprecated_if_applicable(
    statmech: Statmech,
) -> EvidenceOutcome:
    """Skip curator status checks until statmech-level review/deprecation is modeled."""
    return EvidenceOutcome.not_applicable


_TRANSPORT_STRONG_SOURCE_ROLES: frozenset[TransportCalculationRole] = frozenset(
    {
        TransportCalculationRole.full_transport,
        TransportCalculationRole.dipole,
        TransportCalculationRole.polarizability,
        TransportCalculationRole.supporting_geometry,
    }
)


def _transport_sources_by_role(
    transport: Transport, role: TransportCalculationRole
) -> list:
    """Return transport source-calculation links for ``role``."""
    return [link for link in transport.source_calculations if link.role is role]


def _transport_source_calculations(transport: Transport) -> list[Calculation]:
    """Return non-null linked source calculations for ``transport``."""
    return [
        link.calculation
        for link in transport.source_calculations
        if link.calculation is not None
    ]


def _transport_source_calculations_for_roles(
    transport: Transport, roles: frozenset[TransportCalculationRole]
) -> list[Calculation]:
    """Return non-null source calculations whose link role is in ``roles``."""
    return [
        link.calculation
        for link in transport.source_calculations
        if link.role in roles and link.calculation is not None
    ]


def _transport_has_lj_pair(transport: Transport) -> bool:
    """Return True when both Lennard-Jones parameters are populated."""
    return transport.sigma_angstrom is not None and transport.epsilon_over_k_k is not None


def _transport_has_partial_lj_pair(transport: Transport) -> bool:
    """Return True when exactly one Lennard-Jones parameter is populated."""
    return (transport.sigma_angstrom is None) != (transport.epsilon_over_k_k is None)


def _transport_has_any_property(transport: Transport) -> bool:
    """Return True when at least one structured transport property is populated."""
    return (
        transport.sigma_angstrom is not None
        or transport.epsilon_over_k_k is not None
        or transport.dipole_debye is not None
        or transport.polarizability_angstrom3 is not None
        or transport.rotational_relaxation is not None
    )


def _check_transport_species_entry_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when the transport row is attached to a species_entry."""
    return _bool_outcome(
        transport.species_entry_id is not None and transport.species_entry is not None
    )


def _check_transport_origin_is_computed(transport: Transport) -> EvidenceOutcome:
    """Return passed when the transport record declares computed origin."""
    return _bool_outcome(transport.scientific_origin is ScientificOriginKind.computed)


def _check_lj_pair_present_if_applicable(transport: Transport) -> EvidenceOutcome:
    """Return passed when an LJ representation is complete, or skip for non-LJ rows."""
    if _transport_has_lj_pair(transport):
        return EvidenceOutcome.passed
    if _transport_has_partial_lj_pair(transport):
        return EvidenceOutcome.missing
    return EvidenceOutcome.not_applicable


def _check_sigma_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when sigma is populated for an LJ representation."""
    if transport.epsilon_over_k_k is None and transport.sigma_angstrom is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(transport.sigma_angstrom is not None)


def _check_epsilon_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when epsilon/k is populated for an LJ representation."""
    if transport.epsilon_over_k_k is None and transport.sigma_angstrom is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(transport.epsilon_over_k_k is not None)


def _check_sigma_epsilon_pair_consistent(transport: Transport) -> EvidenceOutcome:
    """Return passed when sigma and epsilon are both present or both absent."""
    return _bool_outcome(not _transport_has_partial_lj_pair(transport))


def _check_dipole_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when dipole evidence is populated, otherwise skip if absent."""
    return (
        EvidenceOutcome.passed
        if transport.dipole_debye is not None
        else EvidenceOutcome.not_applicable
    )


def _check_polarizability_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when polarizability evidence is populated, otherwise skip if absent."""
    return (
        EvidenceOutcome.passed
        if transport.polarizability_angstrom3 is not None
        else EvidenceOutcome.not_applicable
    )


def _check_rotational_relaxation_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when rotational-relaxation evidence is populated, otherwise skip if absent."""
    return (
        EvidenceOutcome.passed
        if transport.rotational_relaxation is not None
        else EvidenceOutcome.not_applicable
    )


def _check_transport_property_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when at least one structured transport property exists."""
    return _bool_outcome(_transport_has_any_property(transport))


def _check_transport_model_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when a structured transport representation exists."""
    return _bool_outcome(_transport_has_any_property(transport))


def _check_transport_source_calculations_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when at least one transport_source_calculation row is linked."""
    return _bool_outcome(len(transport.source_calculations) >= 1)


def _check_full_transport_source_present(transport: Transport) -> EvidenceOutcome:
    """Return passed when a full-transport source calculation is linked."""
    return _bool_outcome(
        len(
            _transport_sources_by_role(
                transport, TransportCalculationRole.full_transport
            )
        )
        >= 1
    )


def _check_dipole_source_present_if_dipole_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when populated dipole evidence has a dipole source role."""
    if transport.dipole_debye is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        len(_transport_sources_by_role(transport, TransportCalculationRole.dipole))
        >= 1
    )


def _check_polarizability_source_present_if_polarizability_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when populated polarizability evidence has a polarizability source role."""
    if transport.polarizability_angstrom3 is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        len(
            _transport_sources_by_role(
                transport, TransportCalculationRole.polarizability
            )
        )
        >= 1
    )


def _check_supporting_geometry_source_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when a supporting-geometry source calculation is linked."""
    return _bool_outcome(
        len(
            _transport_sources_by_role(
                transport, TransportCalculationRole.supporting_geometry
            )
        )
        >= 1
    )


def _check_transport_source_calculation_lot_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when all linked source calculations have a level of theory."""
    calcs = _transport_source_calculations(transport)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.lot_id is not None for calc in calcs))


def _check_transport_source_calculation_software_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when all linked source calculations have software metadata."""
    calcs = _transport_source_calculations(transport)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.software_release_id is not None for calc in calcs))


def _check_transport_source_calculation_workflow_tool_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when transport or one source calc carries workflow-tool metadata."""
    if transport.workflow_tool_release_id is not None:
        return EvidenceOutcome.passed
    return _bool_outcome(
        any(
            calc.workflow_tool_release_id is not None
            for calc in _transport_source_calculations(transport)
        )
    )


def _check_transport_source_calculation_artifacts_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when at least one linked source calculation retains artifacts."""
    return _bool_outcome(
        any(len(calc.artifacts) >= 1 for calc in _transport_source_calculations(transport))
    )


def _check_transport_source_calculation_result_blocks_present(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when every linked source calculation has its expected result block."""
    calcs = _transport_source_calculations(transport)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            _check_result_block_present(calc) is EvidenceOutcome.passed
            for calc in calcs
        )
    )


def _check_transport_source_calculation_has_non_hard_failed_evidence(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when linked source calculations avoid deterministic hard-fail signals."""
    calcs = _transport_source_calculations(transport)
    if not calcs:
        return EvidenceOutcome.missing
    return _bool_outcome(
        all(
            calc.quality is not CalculationQuality.rejected
            and (
                calc.geometry_validation is None
                or calc.geometry_validation.validation_status
                is not ValidationStatus.fail
            )
            for calc in calcs
        )
    )


def _check_transport_geometry_validation_present_for_source_calculations(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed when strong source calculations carry geometry validation."""
    strong_calcs = _transport_source_calculations_for_roles(
        transport, _TRANSPORT_STRONG_SOURCE_ROLES
    )
    if not strong_calcs:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(
        all(calc.geometry_validation is not None for calc in strong_calcs)
    )


def _check_transport_geometry_validation_not_failed_for_source_calculations(
    transport: Transport,
) -> EvidenceOutcome:
    """Return passed/warning based on geometry-validation status on source calcs."""
    validations = [
        calc.geometry_validation
        for calc in _transport_source_calculations_for_roles(
            transport, _TRANSPORT_STRONG_SOURCE_ROLES
        )
        if calc.geometry_validation is not None
    ]
    if not validations:
        return EvidenceOutcome.not_applicable
    if any(
        validation.validation_status is ValidationStatus.warning
        for validation in validations
    ):
        return EvidenceOutcome.warning
    if any(
        validation.validation_status is ValidationStatus.fail
        for validation in validations
    ):
        return EvidenceOutcome.not_applicable
    return EvidenceOutcome.passed


def _check_transport_not_rejected_or_deprecated_if_applicable(
    transport: Transport,
) -> EvidenceOutcome:
    """Skip curator status checks until transport-level review/deprecation is modeled."""
    return EvidenceOutcome.not_applicable


COMPUTED_CALCULATION_V1: EvidenceRubric = EvidenceRubric(
    name="computed_calculation",
    version=1,
    record_type="calculation",
    checks=(
        EvidenceCheckSpec(
            name="calculation_has_owner",
            kind=EvidenceCheckKind.required,
            explain="Calculation must be owned by exactly one species_entry or transition_state_entry.",
            runner=_check_calculation_has_owner,
        ),
        EvidenceCheckSpec(
            name="calculation_type_present",
            kind=EvidenceCheckKind.required,
            explain="Calculation.type must be set.",
            runner=_check_calculation_type_present,
        ),
        EvidenceCheckSpec(
            name="level_of_theory_present",
            kind=EvidenceCheckKind.required,
            explain="Calculation must resolve to a level_of_theory row.",
            runner=_check_level_of_theory_present,
        ),
        EvidenceCheckSpec(
            name="software_release_present",
            kind=EvidenceCheckKind.optional,
            explain="Calculation should declare which software_release produced it.",
            runner=_check_software_release_present,
        ),
        EvidenceCheckSpec(
            name="workflow_tool_release_present",
            kind=EvidenceCheckKind.optional,
            explain="Calculation should declare which workflow_tool_release orchestrated it.",
            runner=_check_workflow_tool_release_present,
        ),
        EvidenceCheckSpec(
            name="input_geometry_present",
            kind=EvidenceCheckKind.required,
            explain="At least one input geometry must be linked.",
            runner=_check_input_geometry_present,
        ),
        EvidenceCheckSpec(
            name="output_geometry_present",
            kind=EvidenceCheckKind.optional,
            explain="Geometry-producing calculation types should record an output geometry.",
            runner=_check_output_geometry_present,
        ),
        EvidenceCheckSpec(
            name="result_block_present",
            kind=EvidenceCheckKind.required,
            explain="Calculation must have the result block matching its type (sp/opt/freq/irc/scan/path_search).",
            runner=_check_result_block_present,
        ),
        EvidenceCheckSpec(
            name="quality_recorded",
            kind=EvidenceCheckKind.optional,
            explain="CalculationQuality should be promoted past the default 'raw'.",
            runner=_check_quality_recorded,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present",
            kind=EvidenceCheckKind.optional,
            explain="Opt calculations should carry geometry-validation evidence.",
            runner=_check_geometry_validation_present,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_passed_or_warning",
            kind=EvidenceCheckKind.warning,
            explain="Geometry validation status is warning (advisory).",
            runner=_check_geometry_validation_passed_or_warning,
        ),
        EvidenceCheckSpec(
            name="scf_stability_present_if_claimed",
            kind=EvidenceCheckKind.optional,
            explain="SCF stability evidence should be attached when claimed.",
            runner=_check_scf_stability_present_if_claimed,
        ),
        EvidenceCheckSpec(
            name="artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one calculation_artifact (log, input, ...) should be retained.",
            runner=_check_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="parameters_parsed",
            kind=EvidenceCheckKind.optional,
            explain="ESS execution parameters should be parsed (EAV rows or JSONB snapshot).",
            runner=_check_parameters_parsed,
        ),
        EvidenceCheckSpec(
            name="calculation_dependencies_present_when_expected",
            kind=EvidenceCheckKind.optional,
            explain="Calculations derived from another step (freq/sp/irc/scan) should record their upstream parent.",
            runner=_check_calculation_dependencies_present,
        ),
    ),
)


COMPUTED_KINETICS_V1: EvidenceRubric = EvidenceRubric(
    name="computed_kinetics",
    version=1,
    record_type="kinetics",
    checks=(
        EvidenceCheckSpec(
            name="reaction_entry_present",
            kind=EvidenceCheckKind.required,
            explain="Kinetics must be attached to a reaction_entry.",
            runner=_check_kinetics_reaction_entry_present,
        ),
        EvidenceCheckSpec(
            name="kinetics_model_present",
            kind=EvidenceCheckKind.required,
            explain="Kinetics.model_kind must be set.",
            runner=_check_kinetics_model_present,
        ),
        EvidenceCheckSpec(
            name="arrhenius_parameters_complete",
            kind=EvidenceCheckKind.required,
            explain="Arrhenius-family kinetics should include A, A units, n, and Ea.",
            runner=_check_arrhenius_parameters_complete,
        ),
        EvidenceCheckSpec(
            name="arrhenius_units_present",
            kind=EvidenceCheckKind.optional,
            explain="Arrhenius A units should be populated for Arrhenius-family kinetics.",
            runner=_check_arrhenius_units_present,
        ),
        EvidenceCheckSpec(
            name="temperature_range_present",
            kind=EvidenceCheckKind.optional,
            explain="Both tmin_k and tmax_k should be populated.",
            runner=_check_temperature_range_present,
        ),
        EvidenceCheckSpec(
            name="temperature_range_valid",
            kind=EvidenceCheckKind.optional,
            explain="Temperature range should satisfy 0 < tmin_k < tmax_k <= 10000.",
            runner=_check_temperature_range_valid,
        ),
        EvidenceCheckSpec(
            name="source_calculations_present",
            kind=EvidenceCheckKind.required,
            explain="At least one kinetics_source_calculation row should support computed kinetics.",
            runner=_check_source_calculations_present,
        ),
        EvidenceCheckSpec(
            name="ts_energy_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed TST kinetics should link a TS energy source calculation.",
            runner=_check_ts_energy_source_present,
        ),
        EvidenceCheckSpec(
            name="reactant_energy_sources_present",
            kind=EvidenceCheckKind.optional,
            explain="Reactant energy source calculations should cover all loaded reactants.",
            runner=_check_reactant_energy_sources_present,
        ),
        EvidenceCheckSpec(
            name="product_energy_sources_present",
            kind=EvidenceCheckKind.optional,
            explain="Product energy source calculations should cover all loaded products.",
            runner=_check_product_energy_sources_present,
        ),
        EvidenceCheckSpec(
            name="frequency_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Frequency source calculations should be linked when available.",
            runner=_check_frequency_source_present,
        ),
        EvidenceCheckSpec(
            name="master_equation_or_fit_source_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Explicit master-equation or fit-source roles count when present.",
            runner=_check_master_equation_or_fit_source_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="source_calculation_lot_present",
            kind=EvidenceCheckKind.required,
            explain="All linked source calculations should resolve to level_of_theory.",
            runner=_check_source_calculation_lot_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_software_present",
            kind=EvidenceCheckKind.optional,
            explain="All linked source calculations should declare software_release.",
            runner=_check_source_calculation_software_present,
        ),
        EvidenceCheckSpec(
            name="workflow_tool_release_present",
            kind=EvidenceCheckKind.optional,
            explain="Kinetics or at least one source calc should declare workflow-tool release metadata.",
            runner=_check_workflow_tool_release_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="source_calculation_artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one linked source calculation should retain an artifact.",
            runner=_check_source_calculation_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_result_blocks_present",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should have their expected result blocks.",
            runner=_check_source_calculation_result_blocks_present,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present_for_source_calculations",
            kind=EvidenceCheckKind.optional,
            explain="Strong source calculations should carry geometry-validation evidence.",
            runner=_check_geometry_validation_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_not_failed_for_source_calculations",
            kind=EvidenceCheckKind.warning,
            explain="Source calculation geometry validation is warning (advisory).",
            runner=_check_geometry_validation_not_failed_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="irc_evidence_present",
            kind=EvidenceCheckKind.optional,
            explain="IRC evidence should be linked when available.",
            runner=_check_irc_evidence_present,
        ),
        EvidenceCheckSpec(
            name="path_search_evidence_present",
            kind=EvidenceCheckKind.optional,
            explain="Path-search evidence should be linked when available.",
            runner=_check_path_search_evidence_present,
        ),
        EvidenceCheckSpec(
            name="uncertainty_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one uncertainty field should be populated.",
            runner=_check_uncertainty_present,
        ),
        EvidenceCheckSpec(
            name="tunneling_metadata_present_if_claimed",
            kind=EvidenceCheckKind.optional,
            explain="A claimed tunneling model should have a non-empty identifier.",
            runner=_check_tunneling_metadata_present_if_claimed,
        ),
    ),
)


COMPUTED_THERMO_V1: EvidenceRubric = EvidenceRubric(
    name="computed_thermo",
    version=1,
    record_type="thermo",
    checks=(
        EvidenceCheckSpec(
            name="species_entry_present",
            kind=EvidenceCheckKind.required,
            explain="Thermo must be attached to a species_entry.",
            runner=_check_thermo_species_entry_present,
        ),
        EvidenceCheckSpec(
            name="thermo_origin_is_computed",
            kind=EvidenceCheckKind.required,
            explain="Thermo.scientific_origin should be computed for this rubric.",
            runner=_check_thermo_origin_is_computed,
        ),
        EvidenceCheckSpec(
            name="thermo_model_present",
            kind=EvidenceCheckKind.required,
            explain="Thermo should expose scalar, NASA-7, NASA-9, Wilhoit, or tabulated-point model evidence.",
            runner=_check_thermo_model_present,
        ),
        EvidenceCheckSpec(
            name="scalar_thermo_present",
            kind=EvidenceCheckKind.optional,
            explain="Scalar H298 or S298 values should be populated when using scalar thermo.",
            runner=_check_scalar_thermo_present,
        ),
        EvidenceCheckSpec(
            name="nasa_coefficients_present",
            kind=EvidenceCheckKind.optional,
            explain="NASA thermo should include a complete coefficient block.",
            runner=_check_nasa_coefficients_present,
        ),
        EvidenceCheckSpec(
            name="thermo_points_present",
            kind=EvidenceCheckKind.optional,
            explain="Tabulated thermo should include at least one point with a thermo value.",
            runner=_check_thermo_points_present,
        ),
        EvidenceCheckSpec(
            name="at_least_one_thermo_representation_present",
            kind=EvidenceCheckKind.required,
            explain="Thermo must have scalar, NASA-7, NASA-9, Wilhoit, or tabulated-point evidence.",
            runner=_check_at_least_one_thermo_representation_present,
        ),
        EvidenceCheckSpec(
            name="temperature_range_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Range-bearing thermo should declare temperature bounds.",
            runner=_check_temperature_range_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="temperature_range_valid",
            kind=EvidenceCheckKind.optional,
            explain="Temperature ranges should satisfy 0 < low < high <= 10000.",
            runner=_check_thermo_temperature_range_valid,
        ),
        EvidenceCheckSpec(
            name="source_calculations_present",
            kind=EvidenceCheckKind.required,
            explain="At least one thermo_source_calculation row should support computed thermo.",
            runner=_check_thermo_source_calculations_present,
        ),
        EvidenceCheckSpec(
            name="opt_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed thermo should link an optimization source calculation when available.",
            runner=_check_opt_source_present,
        ),
        EvidenceCheckSpec(
            name="freq_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed thermo should link a frequency source calculation when available.",
            runner=_check_freq_source_present,
        ),
        EvidenceCheckSpec(
            name="sp_or_composite_source_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Computed thermo should link single-point or composite energy evidence when applicable.",
            runner=_check_sp_or_composite_source_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="source_calculation_lot_present",
            kind=EvidenceCheckKind.required,
            explain="All linked source calculations should resolve to level_of_theory.",
            runner=_check_thermo_source_calculation_lot_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_software_present",
            kind=EvidenceCheckKind.optional,
            explain="All linked source calculations should declare software_release.",
            runner=_check_thermo_source_calculation_software_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_workflow_tool_present",
            kind=EvidenceCheckKind.optional,
            explain="Thermo or at least one source calc should declare workflow-tool release metadata.",
            runner=_check_thermo_source_calculation_workflow_tool_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one linked source calculation should retain an artifact.",
            runner=_check_thermo_source_calculation_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_result_blocks_present",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should have their expected result blocks.",
            runner=_check_thermo_source_calculation_result_blocks_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_has_non_hard_failed_evidence",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should avoid deterministic hard-fail signals.",
            runner=_check_source_calculation_has_non_hard_failed_evidence,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present_for_source_calculations",
            kind=EvidenceCheckKind.optional,
            explain="Strong source calculations should carry geometry-validation evidence.",
            runner=_check_thermo_geometry_validation_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_not_failed_for_source_calculations",
            kind=EvidenceCheckKind.warning,
            explain="Source calculation geometry validation is warning (advisory).",
            runner=_check_thermo_geometry_validation_not_failed_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="statmech_present",
            kind=EvidenceCheckKind.optional,
            explain="Linked statmech evidence is expected when schema support exists.",
            runner=_check_statmech_present,
        ),
        EvidenceCheckSpec(
            name="frequency_scale_factor_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Frequency-derived thermo should record its frequency scale factor when schema support exists.",
            runner=_check_frequency_scale_factor_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="uncertainty_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one thermo uncertainty field should be populated.",
            runner=_check_thermo_uncertainty_present,
        ),
        EvidenceCheckSpec(
            name="thermo_not_rejected_or_deprecated_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Thermo rejection/deprecation checks apply once modeled.",
            runner=_check_thermo_not_rejected_or_deprecated_if_applicable,
        ),
    ),
)


COMPUTED_STATMECH_V1: EvidenceRubric = EvidenceRubric(
    name="computed_statmech",
    version=1,
    record_type="statmech",
    checks=(
        EvidenceCheckSpec(
            name="species_entry_present",
            kind=EvidenceCheckKind.required,
            explain="Statmech must be attached to a species_entry.",
            runner=_check_statmech_species_entry_present,
        ),
        EvidenceCheckSpec(
            name="statmech_origin_is_computed",
            kind=EvidenceCheckKind.required,
            explain="Statmech.scientific_origin should be computed for this rubric.",
            runner=_check_statmech_origin_is_computed,
        ),
        EvidenceCheckSpec(
            name="statmech_treatment_present",
            kind=EvidenceCheckKind.required,
            explain="Statmech treatment kind should be recorded.",
            runner=_check_statmech_treatment_present,
        ),
        EvidenceCheckSpec(
            name="rigid_rotor_kind_present",
            kind=EvidenceCheckKind.required,
            explain="Rigid rotor treatment should be recorded.",
            runner=_check_rigid_rotor_kind_present,
        ),
        EvidenceCheckSpec(
            name="external_symmetry_present",
            kind=EvidenceCheckKind.optional,
            explain="External symmetry number should be recorded.",
            runner=_check_external_symmetry_present,
        ),
        EvidenceCheckSpec(
            name="point_group_present",
            kind=EvidenceCheckKind.optional,
            explain="Point group should be recorded when known.",
            runner=_check_point_group_present,
        ),
        EvidenceCheckSpec(
            name="is_linear_present",
            kind=EvidenceCheckKind.optional,
            explain="Linearity should be explicitly recorded.",
            runner=_check_is_linear_present,
        ),
        EvidenceCheckSpec(
            name="frequency_scale_factor_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Frequency-derived statmech should record a frequency scale factor.",
            runner=_check_statmech_frequency_scale_factor_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="uses_projected_frequencies_recorded",
            kind=EvidenceCheckKind.optional,
            explain="Projected-frequency handling should be explicitly recorded.",
            runner=_check_uses_projected_frequencies_recorded,
        ),
        EvidenceCheckSpec(
            name="source_calculations_present",
            kind=EvidenceCheckKind.required,
            explain="At least one statmech_source_calculation row should support computed statmech.",
            runner=_check_statmech_source_calculations_present,
        ),
        EvidenceCheckSpec(
            name="opt_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed statmech should link an optimization source calculation when available.",
            runner=_check_statmech_opt_source_present,
        ),
        EvidenceCheckSpec(
            name="freq_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed statmech should link a frequency source calculation when available.",
            runner=_check_statmech_freq_source_present,
        ),
        EvidenceCheckSpec(
            name="sp_or_composite_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed statmech may link single-point or composite supporting evidence.",
            runner=_check_statmech_sp_or_composite_source_present,
        ),
        EvidenceCheckSpec(
            name="scan_source_present_if_torsions_present",
            kind=EvidenceCheckKind.optional,
            explain="Torsion-bearing statmech should link scan source evidence.",
            runner=_check_scan_source_present_if_torsions_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_lot_present",
            kind=EvidenceCheckKind.required,
            explain="All linked source calculations should resolve to level_of_theory.",
            runner=_check_statmech_source_calculation_lot_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_software_present",
            kind=EvidenceCheckKind.optional,
            explain="All linked source calculations should declare software_release.",
            runner=_check_statmech_source_calculation_software_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_workflow_tool_present",
            kind=EvidenceCheckKind.optional,
            explain="Statmech or at least one source calc should declare workflow-tool release metadata.",
            runner=_check_statmech_source_calculation_workflow_tool_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one linked source calculation should retain an artifact.",
            runner=_check_statmech_source_calculation_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_result_blocks_present",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should have their expected result blocks.",
            runner=_check_statmech_source_calculation_result_blocks_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_has_non_hard_failed_evidence",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should avoid deterministic hard-fail signals.",
            runner=_check_statmech_source_calculation_has_non_hard_failed_evidence,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present_for_source_calculations",
            kind=EvidenceCheckKind.optional,
            explain="Strong source calculations should carry geometry-validation evidence.",
            runner=_check_statmech_geometry_validation_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_not_failed_for_source_calculations",
            kind=EvidenceCheckKind.warning,
            explain="Source calculation geometry validation is warning (advisory).",
            runner=_check_statmech_geometry_validation_not_failed_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="torsions_recorded_if_hindered_rotor_treatment",
            kind=EvidenceCheckKind.optional,
            explain="Hindered-rotor/statmech torsion treatments should include torsion rows.",
            runner=_check_torsions_recorded_if_hindered_rotor_treatment,
        ),
        EvidenceCheckSpec(
            name="torsion_definitions_present",
            kind=EvidenceCheckKind.optional,
            explain="Recorded torsions should include torsion coordinate definitions.",
            runner=_check_torsion_definitions_present,
        ),
        EvidenceCheckSpec(
            name="torsion_symmetry_recorded",
            kind=EvidenceCheckKind.optional,
            explain="Recorded torsions should include symmetry numbers.",
            runner=_check_torsion_symmetry_recorded,
        ),
        EvidenceCheckSpec(
            name="statmech_not_rejected_or_deprecated_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Statmech rejection/deprecation checks apply once modeled.",
            runner=_check_statmech_not_rejected_or_deprecated_if_applicable,
        ),
    ),
)


COMPUTED_TRANSPORT_V1: EvidenceRubric = EvidenceRubric(
    name="computed_transport",
    version=1,
    record_type="transport",
    checks=(
        EvidenceCheckSpec(
            name="species_entry_present",
            kind=EvidenceCheckKind.required,
            explain="Transport must be attached to a species_entry.",
            runner=_check_transport_species_entry_present,
        ),
        EvidenceCheckSpec(
            name="transport_origin_is_computed",
            kind=EvidenceCheckKind.required,
            explain="Transport.scientific_origin should be computed for this rubric.",
            runner=_check_transport_origin_is_computed,
        ),
        EvidenceCheckSpec(
            name="transport_model_present",
            kind=EvidenceCheckKind.required,
            explain="Transport should expose at least one structured property representation.",
            runner=_check_transport_model_present,
        ),
        EvidenceCheckSpec(
            name="lj_pair_present_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Lennard-Jones transport should include both sigma and epsilon/k.",
            runner=_check_lj_pair_present_if_applicable,
        ),
        EvidenceCheckSpec(
            name="sigma_present",
            kind=EvidenceCheckKind.optional,
            explain="Lennard-Jones transport should include sigma.",
            runner=_check_sigma_present,
        ),
        EvidenceCheckSpec(
            name="epsilon_present",
            kind=EvidenceCheckKind.optional,
            explain="Lennard-Jones transport should include epsilon/k.",
            runner=_check_epsilon_present,
        ),
        EvidenceCheckSpec(
            name="sigma_epsilon_pair_consistent",
            kind=EvidenceCheckKind.required,
            explain="sigma_angstrom and epsilon_over_k_k must be both present or both absent.",
            runner=_check_sigma_epsilon_pair_consistent,
        ),
        EvidenceCheckSpec(
            name="dipole_present",
            kind=EvidenceCheckKind.optional,
            explain="Dipole evidence should be populated when this representation is present.",
            runner=_check_dipole_present,
        ),
        EvidenceCheckSpec(
            name="polarizability_present",
            kind=EvidenceCheckKind.optional,
            explain="Polarizability evidence should be populated when this representation is present.",
            runner=_check_polarizability_present,
        ),
        EvidenceCheckSpec(
            name="rotational_relaxation_present",
            kind=EvidenceCheckKind.optional,
            explain="Rotational-relaxation evidence should be populated when present.",
            runner=_check_rotational_relaxation_present,
        ),
        EvidenceCheckSpec(
            name="transport_property_present",
            kind=EvidenceCheckKind.required,
            explain="At least one transport property must be populated.",
            runner=_check_transport_property_present,
        ),
        EvidenceCheckSpec(
            name="source_calculations_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one transport_source_calculation row should support computed transport.",
            runner=_check_transport_source_calculations_present,
        ),
        EvidenceCheckSpec(
            name="full_transport_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed transport should link a full-transport source calculation when available.",
            runner=_check_full_transport_source_present,
        ),
        EvidenceCheckSpec(
            name="dipole_source_present_if_dipole_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed dipole transport evidence should link a dipole source calculation.",
            runner=_check_dipole_source_present_if_dipole_present,
        ),
        EvidenceCheckSpec(
            name="polarizability_source_present_if_polarizability_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed polarizability transport evidence should link a polarizability source calculation.",
            runner=_check_polarizability_source_present_if_polarizability_present,
        ),
        EvidenceCheckSpec(
            name="supporting_geometry_source_present",
            kind=EvidenceCheckKind.optional,
            explain="Computed transport should link supporting-geometry evidence when available.",
            runner=_check_supporting_geometry_source_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_lot_present",
            kind=EvidenceCheckKind.optional,
            explain="All linked source calculations should resolve to level_of_theory.",
            runner=_check_transport_source_calculation_lot_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_software_present",
            kind=EvidenceCheckKind.optional,
            explain="All linked source calculations should declare software_release.",
            runner=_check_transport_source_calculation_software_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_workflow_tool_present",
            kind=EvidenceCheckKind.optional,
            explain="Transport or at least one source calc should declare workflow-tool release metadata.",
            runner=_check_transport_source_calculation_workflow_tool_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one linked source calculation should retain an artifact.",
            runner=_check_transport_source_calculation_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_result_blocks_present",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should have their expected result blocks.",
            runner=_check_transport_source_calculation_result_blocks_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_has_non_hard_failed_evidence",
            kind=EvidenceCheckKind.optional,
            explain="Linked source calculations should avoid deterministic hard-fail signals.",
            runner=_check_transport_source_calculation_has_non_hard_failed_evidence,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present_for_source_calculations",
            kind=EvidenceCheckKind.optional,
            explain="Strong source calculations should carry geometry-validation evidence.",
            runner=_check_transport_geometry_validation_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_not_failed_for_source_calculations",
            kind=EvidenceCheckKind.warning,
            explain="Source calculation geometry validation is warning (advisory).",
            runner=_check_transport_geometry_validation_not_failed_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="transport_not_rejected_or_deprecated_if_applicable",
            kind=EvidenceCheckKind.optional,
            explain="Transport rejection/deprecation checks apply once modeled.",
            runner=_check_transport_not_rejected_or_deprecated_if_applicable,
        ),
    ),
)


_TS_VALIDATED_STATUSES: frozenset[TransitionStateEntryStatus] = frozenset(
    {TransitionStateEntryStatus.optimized, TransitionStateEntryStatus.validated}
)

_TS_UPSTREAM_DEPENDENCY_ROLES: frozenset[CalculationDependencyRole] = frozenset(
    {
        CalculationDependencyRole.optimized_from,
        CalculationDependencyRole.scan_parent,
    }
)
"""Dependency roles where the TS-owned calc is the child and the upstream
parent (e.g. a path_search or scan that produced the TS guess) should be
pulled into the source set."""

_TS_DOWNSTREAM_DEPENDENCY_ROLES: frozenset[CalculationDependencyRole] = frozenset(
    {
        CalculationDependencyRole.freq_on,
        CalculationDependencyRole.single_point_on,
        CalculationDependencyRole.irc_start,
        CalculationDependencyRole.irc_followup,
    }
)
"""Dependency roles where the TS-owned opt calc is the parent and a
downstream child (freq/sp/irc) should be pulled into the source set."""


def _ts_source_calculations(ts_entry: TransitionStateEntry) -> list[Calculation]:
    """Return the deduplicated source-calculation set for a TS entry.

    Discovery is deterministic and pure over already-loaded relationships:
      1. Every ``calculation`` directly attached via
         ``calculation.transition_state_entry_id``.
      2. One dependency hop in both directions, restricted to the roles
         listed in :data:`_TS_UPSTREAM_DEPENDENCY_ROLES` and
         :data:`_TS_DOWNSTREAM_DEPENDENCY_ROLES` (spec §5.2).
    Order is stable: directly-attached calcs first (insertion order, which
    SQLAlchemy preserves from the loaded relationship), then any
    dependency-discovered calcs in the order they are first encountered.
    """
    source: dict[int, Calculation] = {}
    direct = list(ts_entry.calculations)
    for calc in direct:
        source.setdefault(calc.id, calc)
    for calc in direct:
        # child_dependencies = rows where this calc is the *child*; the
        # parent_calculation is the upstream we want when role is e.g.
        # optimized_from or scan_parent.
        for dep in calc.child_dependencies:
            if dep.dependency_role in _TS_UPSTREAM_DEPENDENCY_ROLES:
                parent = dep.parent_calculation
                if parent is not None:
                    source.setdefault(parent.id, parent)
        # parent_dependencies = rows where this calc is the *parent*; the
        # child_calculation is the downstream we want when role is e.g.
        # freq_on, single_point_on, irc_start, irc_followup.
        for dep in calc.parent_dependencies:
            if dep.dependency_role in _TS_DOWNSTREAM_DEPENDENCY_ROLES:
                child = dep.child_calculation
                if child is not None:
                    source.setdefault(child.id, child)
    return list(source.values())


def _ts_source_calc_ids(ts_entry: TransitionStateEntry) -> frozenset[int]:
    """Return the deduplicated id set of source calcs for a TS entry."""
    return frozenset(calc.id for calc in _ts_source_calculations(ts_entry))


def _ts_source_dependencies_present(ts_entry: TransitionStateEntry) -> bool:
    """Return True when at least one dependency edge spans the source set."""
    source_ids = _ts_source_calc_ids(ts_entry)
    if not source_ids:
        return False
    for calc in ts_entry.calculations:
        for dep in calc.parent_dependencies:
            if (
                dep.child_calculation is not None
                and dep.child_calculation.id in source_ids
            ):
                return True
        for dep in calc.child_dependencies:
            if (
                dep.parent_calculation is not None
                and dep.parent_calculation.id in source_ids
            ):
                return True
    return False


def _ts_representative_freq_result(ts_entry: TransitionStateEntry):
    """Return the deterministically-selected representative freq result.

    Selection rule per spec §8.5: latest ``calculation.created_at`` with
    ``calculation.id DESC`` tie-break. Returns ``None`` when no freq calc
    in the source set carries a ``calc_freq_result`` row.
    """
    freq_calcs = [
        calc
        for calc in _ts_source_calculations(ts_entry)
        if calc.type is CalculationType.freq and calc.freq_result is not None
    ]
    if not freq_calcs:
        return None
    chosen = max(freq_calcs, key=lambda c: (c.created_at, c.id))
    return chosen.freq_result


def _ts_has_calc_type(
    ts_entry: TransitionStateEntry, calc_type: CalculationType
) -> bool:
    """Return True when the source set contains a calc of ``calc_type``."""
    return any(calc.type is calc_type for calc in _ts_source_calculations(ts_entry))


def _ts_has_dependency_role_link(
    ts_entry: TransitionStateEntry,
    roles: frozenset[CalculationDependencyRole],
    *,
    direction: str,
) -> bool:
    """Return True when a directly-attached calc has an edge with ``roles``.

    ``direction='downstream'`` walks ``parent_dependencies`` (TS-owned calc
    is the parent); ``direction='upstream'`` walks ``child_dependencies``
    (TS-owned calc is the child).
    """
    for calc in ts_entry.calculations:
        edges = (
            calc.parent_dependencies
            if direction == "downstream"
            else calc.child_dependencies
        )
        for dep in edges:
            if dep.dependency_role in roles:
                return True
    return False


def _check_ts_entry_present(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Trivially passes inside the runner (None is handled by the evaluator)."""
    return EvidenceOutcome.passed


def _check_ts_parent_present(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when the TS entry resolves to its parent TS concept."""
    return _bool_outcome(
        ts_entry.transition_state_id is not None
        and ts_entry.transition_state is not None
    )


def _check_ts_reaction_entry_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when the parent TS resolves to a reaction_entry."""
    parent = ts_entry.transition_state
    if parent is None:
        return EvidenceOutcome.missing
    return _bool_outcome(
        parent.reaction_entry_id is not None and parent.reaction_entry is not None
    )


def _check_ts_chem_reaction_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when the parent reaction_entry resolves to a chem_reaction."""
    parent = ts_entry.transition_state
    if parent is None or parent.reaction_entry is None:
        return EvidenceOutcome.missing
    reaction_entry = parent.reaction_entry
    return _bool_outcome(
        reaction_entry.reaction_id is not None and reaction_entry.reaction is not None
    )


def _check_ts_status_recorded(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when ``status`` is set (NOT NULL by schema)."""
    return _bool_outcome(ts_entry.status is not None)


def _check_ts_status_not_rejected(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when status is anything other than ``rejected``.

    Rejected entries also trigger a hard fail in the evaluator; this check
    keeps the report explicit when the hard-fail branch fires.
    """
    if ts_entry.status is None:
        return EvidenceOutcome.missing
    return _bool_outcome(ts_entry.status is not TransitionStateEntryStatus.rejected)


def _check_ts_charge_present(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when ``charge`` is populated (NOT NULL by schema)."""
    return _bool_outcome(ts_entry.charge is not None)


def _check_ts_multiplicity_present(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when ``multiplicity`` is populated (NOT NULL by schema)."""
    return _bool_outcome(ts_entry.multiplicity is not None)


def _check_ts_multiplicity_valid(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when ``multiplicity >= 1`` (mirrors the CheckConstraint)."""
    if ts_entry.multiplicity is None:
        return EvidenceOutcome.missing
    return _bool_outcome(ts_entry.multiplicity >= 1)


def _check_ts_graph_or_smiles_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when SMILES or a mol blob is attached on the entry."""
    return _bool_outcome(
        bool(ts_entry.unmapped_smiles) or ts_entry.mol is not None
    )


def _check_ts_supporting_calculations_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one source calc is discoverable."""
    return _bool_outcome(len(_ts_source_calculations(ts_entry)) >= 1)


def _check_ts_optimization_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when an opt calc is in the source set."""
    return _bool_outcome(_ts_has_calc_type(ts_entry, CalculationType.opt))


def _check_ts_frequency_present(ts_entry: TransitionStateEntry) -> EvidenceOutcome:
    """Return passed when a freq calc is in the source set."""
    return _bool_outcome(_ts_has_calc_type(ts_entry, CalculationType.freq))


def _check_ts_single_point_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when an sp calc is in the source set."""
    return _bool_outcome(_ts_has_calc_type(ts_entry, CalculationType.sp))


def _check_ts_irc_evidence_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when an irc calc is in the source set.

    Per spec §7, missing IRC is **not** a hard fail; this check only
    records its presence as additive evidence.
    """
    return _bool_outcome(_ts_has_calc_type(ts_entry, CalculationType.irc))


def _check_ts_path_search_evidence_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when a path_search or scan-parent calc is in the source set.

    Per spec §7, missing path-search is **not** a hard fail.
    """
    if _ts_has_calc_type(ts_entry, CalculationType.path_search):
        return EvidenceOutcome.passed
    # A scan parent (TS opt's child_dependencies role=scan_parent) also counts.
    if _ts_has_dependency_role_link(
        ts_entry,
        frozenset({CalculationDependencyRole.scan_parent}),
        direction="upstream",
    ):
        return EvidenceOutcome.passed
    return EvidenceOutcome.missing


def _check_ts_calculation_dependencies_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one dependency edge spans the source set."""
    return _bool_outcome(_ts_source_dependencies_present(ts_entry))


def _check_ts_source_calculation_lot_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when every source calc resolves to a level_of_theory.

    Per spec §4.3, this is "all-source" semantics: LoT-less source calcs
    are meaningless for TS evidence, so any missing LoT fails the check.
    """
    source = _ts_source_calculations(ts_entry)
    if not source:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.lot_id is not None for calc in source))


def _check_ts_source_calculation_software_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when every source calc resolves to a software_release."""
    source = _ts_source_calculations(ts_entry)
    if not source:
        return EvidenceOutcome.missing
    return _bool_outcome(all(calc.software_release_id is not None for calc in source))


def _check_ts_source_calculation_workflow_tool_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one source calc has a workflow_tool_release."""
    source = _ts_source_calculations(ts_entry)
    if not source:
        return EvidenceOutcome.missing
    return _bool_outcome(
        any(calc.workflow_tool_release_id is not None for calc in source)
    )


def _check_ts_source_calculation_artifacts_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one source calc has any calculation_artifact."""
    source = _ts_source_calculations(ts_entry)
    if not source:
        return EvidenceOutcome.missing
    return _bool_outcome(any(len(calc.artifacts) >= 1 for calc in source))


def _check_ts_source_calculation_has_non_hard_failed_evidence(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one source calc is not deterministically hard-failed.

    "Hard-failed" mirrors the calculation rubric's signals: quality=rejected
    or geometry-validation status=fail. The evaluator promotes the
    "all source calcs hard-failed" case to its own ``HardFailReason``.
    """
    source = _ts_source_calculations(ts_entry)
    if not source:
        return EvidenceOutcome.missing
    for calc in source:
        if calc.quality is CalculationQuality.rejected:
            continue
        gv = calc.geometry_validation
        if gv is not None and gv.validation_status is ValidationStatus.fail:
            continue
        return EvidenceOutcome.passed
    return EvidenceOutcome.missing


_TS_GEOMETRY_VALIDATION_TYPES: frozenset[CalculationType] = frozenset(
    {
        CalculationType.opt,
        CalculationType.irc,
        CalculationType.path_search,
    }
)


def _check_ts_geometry_validation_present_for_source_calculations(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when at least one geometry-bearing source calc has validation."""
    eligible = [
        calc
        for calc in _ts_source_calculations(ts_entry)
        if calc.type in _TS_GEOMETRY_VALIDATION_TYPES
    ]
    if not eligible:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(any(calc.geometry_validation is not None for calc in eligible))


def _check_ts_geometry_validation_not_failed_for_source_calculations(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return warning when any source calc carries a ``warning`` geometry status.

    ``fail`` is promoted to a hard fail by the evaluator before this runs;
    in that case the runner reports ``not_applicable``.
    """
    validations = [
        calc.geometry_validation
        for calc in _ts_source_calculations(ts_entry)
        if calc.geometry_validation is not None
    ]
    if not validations:
        return EvidenceOutcome.not_applicable
    if any(v.validation_status is ValidationStatus.fail for v in validations):
        return EvidenceOutcome.not_applicable
    if any(v.validation_status is ValidationStatus.warning for v in validations):
        return EvidenceOutcome.warning
    return EvidenceOutcome.passed


def _check_ts_imaginary_frequency_count_recorded(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when the representative freq result records ``n_imag``."""
    freq = _ts_representative_freq_result(ts_entry)
    if freq is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(freq.n_imag is not None)


def _check_ts_single_imaginary_frequency_for_ts(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when the representative freq result has exactly one imaginary mode.

    Status-aware policy is handled at the evaluator hard-fail layer: this
    runner returns ``missing`` for ``n_imag != 1`` regardless of status; the
    evaluator promotes the optimized/validated + ``n_imag in {0, >1}`` cases
    to hard fails after the check has run.
    """
    freq = _ts_representative_freq_result(ts_entry)
    if freq is None:
        return EvidenceOutcome.not_applicable
    if freq.n_imag is None:
        return EvidenceOutcome.missing
    return _bool_outcome(freq.n_imag == 1)


def _check_ts_imaginary_frequency_value_present(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Return passed when the representative freq result records ``imag_freq_cm1``."""
    freq = _ts_representative_freq_result(ts_entry)
    if freq is None:
        return EvidenceOutcome.not_applicable
    return _bool_outcome(freq.imag_freq_cm1 is not None)


def _check_ts_review_not_rejected_or_deprecated_if_applicable(
    ts_entry: TransitionStateEntry,
) -> EvidenceOutcome:
    """Skip curator status checks until TS-entry-level review lookup is wired.

    Mirrors the equivalent ``_check_*_not_rejected_or_deprecated_if_applicable``
    runners on the thermo/statmech/transport rubrics. Wiring the
    ``record_review`` lookup is intentionally deferred to the read-API
    integration slice (per spec §10 / §14).
    """
    return EvidenceOutcome.not_applicable


COMPUTED_TRANSITION_STATE_V1: EvidenceRubric = EvidenceRubric(
    name="computed_transition_state",
    version=1,
    record_type="transition_state_entry",
    checks=(
        EvidenceCheckSpec(
            name="transition_state_entry_present",
            kind=EvidenceCheckKind.required,
            explain="The transition_state_entry record under evaluation is loaded.",
            runner=_check_ts_entry_present,
        ),
        EvidenceCheckSpec(
            name="transition_state_parent_present",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry must resolve to its parent transition_state.",
            runner=_check_ts_parent_present,
        ),
        EvidenceCheckSpec(
            name="reaction_entry_present",
            kind=EvidenceCheckKind.required,
            explain="Parent transition_state must resolve to a reaction_entry.",
            runner=_check_ts_reaction_entry_present,
        ),
        EvidenceCheckSpec(
            name="chem_reaction_present",
            kind=EvidenceCheckKind.optional,
            explain="Parent reaction_entry should resolve to a chem_reaction.",
            runner=_check_ts_chem_reaction_present,
        ),
        EvidenceCheckSpec(
            name="ts_status_recorded",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry.status must be set.",
            runner=_check_ts_status_recorded,
        ),
        EvidenceCheckSpec(
            name="ts_status_not_rejected",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry.status must not be rejected.",
            runner=_check_ts_status_not_rejected,
        ),
        EvidenceCheckSpec(
            name="charge_present",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry.charge must be set.",
            runner=_check_ts_charge_present,
        ),
        EvidenceCheckSpec(
            name="multiplicity_present",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry.multiplicity must be set.",
            runner=_check_ts_multiplicity_present,
        ),
        EvidenceCheckSpec(
            name="multiplicity_valid",
            kind=EvidenceCheckKind.required,
            explain="transition_state_entry.multiplicity must be >= 1.",
            runner=_check_ts_multiplicity_valid,
        ),
        EvidenceCheckSpec(
            name="ts_graph_or_smiles_present",
            kind=EvidenceCheckKind.optional,
            explain="A SMILES or mol blob should be attached to the TS entry.",
            runner=_check_ts_graph_or_smiles_present,
        ),
        EvidenceCheckSpec(
            name="supporting_calculations_present",
            kind=EvidenceCheckKind.required,
            weight=2,
            explain="At least one calculation should support this TS entry.",
            runner=_check_ts_supporting_calculations_present,
        ),
        EvidenceCheckSpec(
            name="ts_optimization_present",
            kind=EvidenceCheckKind.optional,
            explain="A TS-optimization calculation should be in the source set.",
            runner=_check_ts_optimization_present,
        ),
        EvidenceCheckSpec(
            name="ts_frequency_present",
            kind=EvidenceCheckKind.optional,
            explain="A frequency calculation should be in the source set.",
            runner=_check_ts_frequency_present,
        ),
        EvidenceCheckSpec(
            name="ts_single_point_present",
            kind=EvidenceCheckKind.optional,
            explain="A single-point calculation should be in the source set.",
            runner=_check_ts_single_point_present,
        ),
        EvidenceCheckSpec(
            name="irc_evidence_present",
            kind=EvidenceCheckKind.optional,
            explain="IRC evidence should be linked when available (additive only).",
            runner=_check_ts_irc_evidence_present,
        ),
        EvidenceCheckSpec(
            name="path_search_evidence_present",
            kind=EvidenceCheckKind.optional,
            explain="Path-search (NEB/GSM/scan parent) evidence should be linked when available.",
            runner=_check_ts_path_search_evidence_present,
        ),
        EvidenceCheckSpec(
            name="calculation_dependencies_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one calculation_dependency edge should document the source-set DAG.",
            runner=_check_ts_calculation_dependencies_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_lot_present",
            kind=EvidenceCheckKind.required,
            explain="Every source calculation must resolve to a level_of_theory.",
            runner=_check_ts_source_calculation_lot_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_software_present",
            kind=EvidenceCheckKind.optional,
            explain="Every source calculation should declare a software_release.",
            runner=_check_ts_source_calculation_software_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_workflow_tool_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one source calculation should declare a workflow_tool_release.",
            runner=_check_ts_source_calculation_workflow_tool_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_artifacts_present",
            kind=EvidenceCheckKind.optional,
            explain="At least one source calculation should retain an artifact.",
            runner=_check_ts_source_calculation_artifacts_present,
        ),
        EvidenceCheckSpec(
            name="source_calculation_has_non_hard_failed_evidence",
            kind=EvidenceCheckKind.required,
            weight=2,
            explain="At least one source calculation must avoid deterministic hard-fail signals.",
            runner=_check_ts_source_calculation_has_non_hard_failed_evidence,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_present_for_source_calculations",
            kind=EvidenceCheckKind.optional,
            explain="At least one opt/irc/path_search source calc should carry geometry validation.",
            runner=_check_ts_geometry_validation_present_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="geometry_validation_not_failed_for_source_calculations",
            kind=EvidenceCheckKind.warning,
            explain="Source calculation geometry validation is warning (advisory).",
            runner=_check_ts_geometry_validation_not_failed_for_source_calculations,
        ),
        EvidenceCheckSpec(
            name="imaginary_frequency_count_recorded",
            kind=EvidenceCheckKind.optional,
            explain="Representative freq result should record n_imag.",
            runner=_check_ts_imaginary_frequency_count_recorded,
        ),
        EvidenceCheckSpec(
            name="single_imaginary_frequency_for_ts",
            kind=EvidenceCheckKind.required,
            explain="Representative freq result should have exactly one imaginary mode.",
            runner=_check_ts_single_imaginary_frequency_for_ts,
        ),
        EvidenceCheckSpec(
            name="imaginary_frequency_value_present",
            kind=EvidenceCheckKind.optional,
            explain="Representative freq result should record the imaginary-mode value (cm-1).",
            runner=_check_ts_imaginary_frequency_value_present,
        ),
        EvidenceCheckSpec(
            name="review_not_rejected_or_deprecated_if_applicable",
            kind=EvidenceCheckKind.required,
            explain="TS-entry review/deprecation checks apply once record_review lookup is wired.",
            runner=_check_ts_review_not_rejected_or_deprecated_if_applicable,
        ),
    ),
)


RUBRIC_REGISTRY: dict[str, EvidenceRubric] = {
    "calculation": COMPUTED_CALCULATION_V1,
    "kinetics": COMPUTED_KINETICS_V1,
    "statmech": COMPUTED_STATMECH_V1,
    "thermo": COMPUTED_THERMO_V1,
    "transition_state_entry": COMPUTED_TRANSITION_STATE_V1,
    "transport": COMPUTED_TRANSPORT_V1,
}
"""Lookup of the latest active rubric per record-type discriminator.

Today the registry holds one entry. As rubrics for kinetics / thermo /
statmech / transport / experimental land, they should be added here
keyed by the same record-type discriminator the read serializer uses.
Multiple rubric versions can coexist; the registry stores the current
default per type.
"""


def get_rubric_for_record_type(record_type: str) -> Optional[EvidenceRubric]:
    """Return the active rubric for a record-type discriminator, or None.

    Returning ``None`` (rather than raising) makes "no rubric defined"
    a survivable shape — the read serializer can omit the
    ``evidence`` block for record types that do not yet have a rubric,
    rather than the API failing.
    """
    return RUBRIC_REGISTRY.get(record_type)
