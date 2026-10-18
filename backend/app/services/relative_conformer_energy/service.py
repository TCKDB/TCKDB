"""Relative conformer energies: scan the compared groups, assess their determinations, compute the differences.

Service layer only: no HTTP, no persistence. The structure assessment (``assess_entry_structures``) is reused
unchanged; this adds the population filter in front of it and the pure differencing behind it.

Flow: refuse what this release does not evaluate (before the first query), open one read-only REPEATABLE READ
snapshot, scan the compared conformer groups (a hidden group is not in the world), assess their basin
determinations, compute, and assemble the replayable manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.read_snapshot import begin_read_snapshot
from app.services.relative_conformer_energy.bounds import check_supported
from app.services.relative_conformer_energy.compare import ComparisonRequest, compare_results
from app.services.relative_conformer_energy.compute import compute_relative_energies
from app.services.relative_conformer_energy.manifest import build_comparison_manifest, build_manifest
from app.services.relative_conformer_energy.models import RelativeEnergyRequest, RelativeEnergyResult
from app.services.relative_conformer_energy.population import Population, effective_statuses, scan_population
from app.services.scientific_read.profile import current_read_profile
from app.services.structure_selection.loader import load_subject
from app.services.structure_selection.models import (
    CoverageRequirement,
    Grain,
    Intent,
    Quantity,
    StructureAssessmentResult,
    StructureRequest,
    ValidationClaim,
)
from app.services.structure_selection.service import assess_entry_structures


@dataclass(frozen=True)
class RelativeEnergyRun:
    """Everything one request produced: the population, the assessment it rests on, the answer and its manifest."""

    request: RelativeEnergyRequest
    structure_request: StructureRequest
    population: Population
    assessment: StructureAssessmentResult
    result: RelativeEnergyResult
    manifest: dict[str, Any]


def to_structure_request(request: RelativeEnergyRequest, determination_refs: tuple[str, ...]) -> StructureRequest:
    """The structure assessment's request for a relative-energy one.

    A validated minimum at the conformer grain, for the requested energy, over exactly the determinations of the
    compared groups. Coverage is ``known_values`` here: the caller's coverage requirement is applied to the groups
    in the computation, not to the determinations. No rule takes part.
    """
    return StructureRequest(
        grain=Grain.conformer,
        intent=Intent.validated_minimum,
        quantity=Quantity(request.energy_kind.value),
        coverage=CoverageRequirement.known_values,
        validation_claim=ValidationClaim.local_minimum,
        min_review_status=request.min_review_status,
        permitted_quality=request.permitted_quality,
        member_refs=determination_refs or None,
        recipe=request.recipe,
        require_stable_reference=request.require_stable_reference,
        apply_rules=False,
        repeat_policy=request.repeat_policy,
    )


def assess_and_compute(
    session: Session,
    *,
    entry_id: int,
    request: RelativeEnergyRequest,
    population: Population,
    isolation: str,
    require_snapshot: bool,
) -> tuple[StructureRequest, StructureAssessmentResult, RelativeEnergyResult]:
    """Assess the population's determinations for one request and compute its answer (the session's snapshot is open)."""
    structure_request = to_structure_request(request, population.determination_refs)
    if population.determination_refs:
        assessment = assess_entry_structures(
            session, entry_id=entry_id, request=structure_request, require_snapshot=require_snapshot
        )
    else:
        # Nothing to assess: every compared group reports absent. The subject and statuses are still recorded.
        assessment = StructureAssessmentResult(
            request=structure_request,
            subject=load_subject(session, Grain.conformer, entry_id),
            effective_statuses=tuple(sorted(effective_statuses(request), key=lambda s: s.value)),
            total_units=0,
            visible_units=0,
            excluded_by_review=(),
            excluded_count=0,
            nested_rows=0,
            snapshot_isolation=isolation,
            calculations=(),
            determinations=(),
            assessments=(),
        )
    result = compute_relative_energies(
        request,
        structure_request,
        group_refs=population.group_refs,
        excluded_groups=population.excluded_groups,
        floor_excluded_refs=population.floor_excluded_refs,
        terminal_excluded_refs=population.terminal_excluded_refs,
        determinations=assessment.determinations,
        assessments=assessment.assessments,
        calculations={c.calculation_ref: c for c in assessment.calculations},
    )
    return structure_request, assessment, result


def current_profile() -> dict[str, Any]:
    resolved = current_read_profile()
    return {
        "profile": resolved.profile.value,
        "review_floor": resolved.review_floor.value if resolved.review_floor is not None else None,
    }


def run_relative_energies(
    session: Session, *, entry_id: int, request: RelativeEnergyRequest, require_snapshot: bool = True
) -> RelativeEnergyRun:
    """Relative conformer energies of one species entry against the reference the request names.

    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the population
        and everything loaded for it are one consistent state (``SnapshotNotConsistentError`` otherwise). Only a
        caller that cannot open one (a test inside one outer transaction) passes ``False``.
    :raises CodedValueError: ``relative_conformer_energy_unsupported`` (an energy kind or certificate not evaluated;
        raised before anything is read), ``relative_conformer_energy_population_too_large``, or a structure
        assessment bound; nothing is answered from a subset.
    :raises NotFoundError: an unknown or hidden entry, reference or member (not told apart).
    """
    check_supported(request)
    isolation = begin_read_snapshot(session, require=require_snapshot)
    population = scan_population(session, entry_id=entry_id, request=request)
    structure_request, assessment, result = assess_and_compute(
        session,
        entry_id=entry_id,
        request=request,
        population=population,
        isolation=isolation,
        require_snapshot=require_snapshot,
    )
    manifest = build_manifest(request, population, assessment, result, profile=current_profile())
    return RelativeEnergyRun(
        request=request,
        structure_request=structure_request,
        population=population,
        assessment=assessment,
        result=result,
        manifest=manifest,
    )


@dataclass(frozen=True)
class ComparisonRun:
    """Everything one comparison produced: the two runs over one population, the comparison and its manifest."""

    request: ComparisonRequest
    population: Population
    runs: dict[str, RelativeEnergyRun]
    comparison: dict[str, Any]
    manifest: dict[str, Any]


def run_comparison(
    session: Session, *, entry_id: int, request: ComparisonRequest, require_snapshot: bool = True
) -> ComparisonRun:
    """The descriptive comparison of two stored protocols: the relative-energy computation twice, in one snapshot.

    Both runs read the one population in the one snapshot; their members are paired by conformer ref. Nothing is
    chosen, ranked or called better. See :mod:`~app.services.relative_conformer_energy.compare`.

    :raises CodedValueError: ``relative_conformer_energy_unsupported`` (raised before anything is read), the population
        bound, or a structure assessment bound.
    :raises NotFoundError: an unknown or hidden entry, reference or member.
    """
    check_supported(request.candidate)
    isolation = begin_read_snapshot(session, require=require_snapshot)
    population = scan_population(session, entry_id=entry_id, request=request.candidate)
    profile = current_profile()
    runs: dict[str, RelativeEnergyRun] = {}
    for name, one in (("candidate", request.candidate), ("reference", request.reference)):
        structure_request, assessment, result = assess_and_compute(
            session,
            entry_id=entry_id,
            request=one,
            population=population,
            isolation=isolation,
            require_snapshot=require_snapshot,
        )
        runs[name] = RelativeEnergyRun(
            request=one,
            structure_request=structure_request,
            population=population,
            assessment=assessment,
            result=result,
            manifest=build_manifest(one, population, assessment, result, profile=profile),
        )
    comparison = compare_results(
        runs["candidate"].result.to_dict(),
        runs["reference"].result.to_dict(),
        pairing=request.pairing,
        coverage_requirement=request.candidate.coverage,
        reference_conformer_ref=request.candidate.reference_conformer_ref,
    )
    manifest = build_comparison_manifest(
        request,
        {name: r.manifest for name, r in runs.items()},
        comparison,
        profile=profile,
        snapshot_isolation=isolation,
        bounds=runs["candidate"].structure_request.bounds,
    )
    return ComparisonRun(request=request, population=population, runs=runs, comparison=comparison, manifest=manifest)
