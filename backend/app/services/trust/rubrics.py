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
    CalculationQuality,
    CalculationType,
    KineticsCalculationRole,
    KineticsModelKind,
    ReactionRole,
    ScientificOriginKind,
    ThermoCalculationRole,
    ValidationStatus,
)
from app.db.models.kinetics import Kinetics
from app.db.models.thermo import Thermo
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


def _thermo_has_any_representation(thermo: Thermo) -> bool:
    """Return True when scalar, NASA, or point evidence is populated."""
    return (
        _thermo_has_scalar_representation(thermo)
        or _thermo_has_nasa_representation(thermo)
        or _thermo_has_point_representation(thermo)
    )


def _thermo_range_is_present(thermo: Thermo) -> bool:
    """Return True when a top-level or NASA temperature range is populated."""
    if thermo.tmin_k is not None or thermo.tmax_k is not None:
        return True
    nasa = thermo.nasa
    if nasa is None:
        return False
    return nasa.t_low is not None or nasa.t_mid is not None or nasa.t_high is not None


def _thermo_range_is_valid(thermo: Thermo) -> bool:
    """Return True when all populated thermo temperature ranges are plausible."""
    if thermo.tmin_k is not None or thermo.tmax_k is not None:
        if thermo.tmin_k is None or thermo.tmax_k is None:
            return False
        if not (0 < thermo.tmin_k < thermo.tmax_k <= 10_000):
            return False

    nasa = thermo.nasa
    if nasa is None:
        return True
    if nasa.t_low is None and nasa.t_mid is None and nasa.t_high is None:
        return True
    if nasa.t_low is None or nasa.t_mid is None or nasa.t_high is None:
        return False
    return 0 < nasa.t_low < nasa.t_mid < nasa.t_high <= 10_000


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
    """Return passed when scalar, NASA, or tabulated point evidence exists."""
    return _bool_outcome(_thermo_has_any_representation(thermo))


def _check_temperature_range_present_if_applicable(thermo: Thermo) -> EvidenceOutcome:
    """Return passed for range-bearing thermo representations when bounds exist."""
    if thermo.nasa is None and thermo.tmin_k is None and thermo.tmax_k is None:
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
            explain="Thermo should expose scalar, NASA, or tabulated-point model evidence.",
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
            explain="Thermo must have scalar, NASA, or tabulated-point evidence.",
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


RUBRIC_REGISTRY: dict[str, EvidenceRubric] = {
    "calculation": COMPUTED_CALCULATION_V1,
    "kinetics": COMPUTED_KINETICS_V1,
    "thermo": COMPUTED_THERMO_V1,
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
