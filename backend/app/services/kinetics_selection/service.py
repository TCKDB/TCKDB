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

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import ScientificOriginKind
from app.db.models.kinetics import Kinetics
from app.services.kinetics_selection.assessment import assess_candidate
from app.services.kinetics_selection.grouping import group_by_determination
from app.services.kinetics_selection.loader import (
    MAX_EXCLUDED_LISTED,
    PopulationScan,
    load_population,
    scan_population,
)
from app.services.kinetics_selection.models import (
    KineticsAssessment,
    KineticsAssessmentResult,
    KineticsRequest,
    NormalizedKinetics,
    Reason,
)
from app.services.read_snapshot import begin_read_snapshot
from app.services.selection_kernel import Applicability
from app.services.trust import evaluate_loaded_kinetics


def _excluded_refs(session: Session, scan: PopulationScan) -> tuple[dict[str, str], ...]:
    """The first ``MAX_EXCLUDED_LISTED`` excluded rows by id, named by public ref; the total is reported separately."""
    listed = scan.excluded[:MAX_EXCLUDED_LISTED]
    if not listed:
        return ()
    refs: dict[int, str] = dict(
        session.execute(select(Kinetics.id, Kinetics.public_ref).where(Kinetics.id.in_([i for i, _, _ in listed])))
        .tuples()
        .all()
    )
    return tuple(
        {"kinetics_ref": refs[i], "review_status": status.value, "reason": reason} for i, status, reason in listed
    )


SOLVE_REASON = "determination_spans_network_solves"


def _separate_solves(
    candidates: dict[str, NormalizedKinetics], assessments: list[KineticsAssessment]
) -> list[KineticsAssessment]:
    """A determination whose eligible records come from different network solves is not one determination.

    Alternate fits of one master-equation result share its solve; fits from different solves are different
    products of different calculations whatever key they were deposited under, so they must not be grouped as
    alternates. Such a determination is refused here, with a reason, and none of its records competes.
    """
    solves: dict[str, set[str]] = {}
    for a in assessments:
        c = candidates[a.kinetics_ref]
        if a.physically_eligible and c.determination is not None and c.network_solve_ref is not None:
            solves.setdefault(c.determination.determination_ref, set()).add(c.network_solve_ref)
    mixed = {det for det, refs in solves.items() if len(refs) > 1}
    if not mixed:
        return assessments
    out = []
    for a in assessments:
        c = candidates[a.kinetics_ref]
        if a.physically_eligible and c.determination is not None and c.determination.determination_ref in mixed:
            a = replace(
                a,
                applicability=Applicability.incompatible,
                reasons=(*a.reasons, Reason(SOLVE_REASON, Applicability.incompatible)),
            )
        out.append(a)
    return out


def assess_reaction_entry_kinetics(
    session: Session, *, reaction_entry_id: int, request: KineticsRequest, require_snapshot: bool = True
) -> KineticsAssessmentResult:
    """Assess every visible kinetics record of a reaction entry for the request and group the eligible ones.

    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the population
        and everything loaded for it are one consistent state (``SnapshotNotConsistentError`` otherwise). A route
        opens a session for the purpose; only a caller that cannot (a test inside one outer transaction) passes
        ``False``, and the isolation level actually in force is reported on the result either way.
    :raises NotFoundError: unknown reaction entry.
    :raises CodedValueError: ``kinetics_selection_population_too_large``; nothing is assessed.
    """
    isolation = begin_read_snapshot(session, require=require_snapshot)
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

    assessments = _separate_solves(loaded.candidates_by_ref, assessments)
    eligible = [loaded.candidates_by_ref[a.kinetics_ref] for a in assessments if a.physically_eligible]
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
        excluded_count=len(scan.excluded),
        snapshot_isolation=isolation,
        candidates=tuple(loaded.candidates[k.id] for k in loaded.rows),
        assessments=tuple(assessments),
        groups=groups,
        unresolved_refs=unresolved,
        unsupported_refs=unsupported,
        notes=tuple(notes),
    )
