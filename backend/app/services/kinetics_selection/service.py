"""Kinetics assessment: load, assess, group.

Service layer only. No HTTP, no persistence: the function reads and returns a result. Browse order
and every existing read are untouched. Ordering and outcomes (rules, conflicts, fronts) are the
stage built on this result; here the question is only which records answer the requested rate
coefficient and which determinations they form.

Flow: scan the visible population (review floor first, rejected and deprecated out) and refuse above
the cap with a coded 422 before anything is loaded; load and normalise it; assess every record; group
the eligible ones by determination.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import ScientificOriginKind
from app.db.models.kinetics import Kinetics
from app.services.kinetics_selection.assessment import assess_candidate
from app.services.kinetics_selection.grouping import group_by_determination
from app.services.kinetics_selection.loader import PopulationScan, load_population, scan_population
from app.services.kinetics_selection.models import KineticsAssessment, KineticsAssessmentResult, KineticsRequest
from app.services.selection_kernel import Applicability
from app.services.trust import evaluate_loaded_kinetics


def _excluded_refs(session: Session, scan: PopulationScan) -> tuple[dict[str, str], ...]:
    if not scan.excluded:
        return ()
    refs: dict[int, str] = dict(
        session.execute(select(Kinetics.id, Kinetics.public_ref).where(Kinetics.id.in_([i for i, _, _ in scan.excluded])))
        .tuples()
        .all()
    )
    return tuple(
        {"kinetics_ref": refs[i], "review_status": status.value, "reason": reason} for i, status, reason in scan.excluded
    )


def assess_reaction_entry_kinetics(
    session: Session, *, reaction_entry_id: int, request: KineticsRequest
) -> KineticsAssessmentResult:
    """Assess every visible kinetics record of a reaction entry for the request and group the eligible ones.

    :raises NotFoundError: unknown reaction entry.
    :raises CodedValueError: ``kinetics_selection_population_too_large``; nothing is assessed.
    """
    scan = scan_population(session, reaction_entry_id=reaction_entry_id, request=request)
    excluded = _excluded_refs(session, scan)
    loaded = load_population(session, scan)

    assessments: list[KineticsAssessment] = []
    eligible = []
    for kinetics in loaded.rows:
        evidence = (
            evaluate_loaded_kinetics(kinetics) if kinetics.scientific_origin is ScientificOriginKind.computed else None
        )
        normalized = loaded.candidates[kinetics.id]
        assessment = assess_candidate(normalized, request=request, evidence=evidence)
        assessments.append(assessment)
        if assessment.physically_eligible:
            eligible.append(normalized)

    groups = group_by_determination(eligible, admin_policy=request.admin_policy)
    unresolved = tuple(a.kinetics_ref for a in assessments if a.applicability is Applicability.unresolved)
    unsupported = tuple(a.kinetics_ref for a in assessments if a.applicability is Applicability.unsupported)
    notes: list[str] = []
    if unresolved or unsupported:
        # Uniform across every outcome: a result is scoped to what is eligible now, and these records might
        # compete if their missing facts were recorded or their form were evaluated.
        notes.append(
            f"{len(unresolved)} candidate(s) are unresolved and {len(unsupported)} unsupported; they do not compete "
            "in this result and may be applicable if their missing facts were recorded or their form evaluated"
        )
    return KineticsAssessmentResult(
        request=request,
        effective_statuses=tuple(sorted(scan.effective_statuses, key=lambda s: s.value)),
        total_rows=scan.total_rows,
        visible_candidates=len(scan.population_ids),
        excluded_by_review=excluded,
        candidates=tuple(loaded.candidates[k.id] for k in loaded.rows),
        assessments=tuple(assessments),
        groups=groups,
        unresolved_refs=unresolved,
        unsupported_refs=unsupported,
        notes=tuple(notes),
    )
