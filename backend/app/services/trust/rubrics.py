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
    ValidationStatus,
)
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


RUBRIC_REGISTRY: dict[str, EvidenceRubric] = {
    "calculation": COMPUTED_CALCULATION_V1,
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
