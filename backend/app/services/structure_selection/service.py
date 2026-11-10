"""Structure assessment: scan, load, assess.

Service layer only. No HTTP, no persistence: the function reads and returns a result. Browse order and every
existing read are untouched. Ordering and outcomes (cohorts, repeats, fronts) are the stage built on this result;
here the question is only which units answer the request and what each finding says.

Flow: open one read-only REPEATABLE READ snapshot, scan the visible population (review floor first, rejected and
deprecated out), refuse above a bound with a coded 422 before anything is loaded, load and normalise it, assess
every unit.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.db.models.structure_determination import StructureDetermination
from app.schemas.reads.scientific_selection_common import ELIGIBILITY_CONFIRMED_FINDINGS, ELIGIBILITY_REJECTED_QUALITY
from app.services.read_snapshot import begin_read_snapshot
from app.services.scientific_read.ref_guard import withhold_selection_facts
from app.services.selection_kernel import Applicability
from app.services.selection_kernel.requested import Verdict, funnel, judge
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.loader import (
    MAX_EXCLUDED_LISTED,
    REASON_SOURCE_QUALITY,
    PopulationScan,
    load_population,
    scan_population,
)
from app.services.structure_selection.models import (
    DEFAULT_PERMITTED_QUALITY,
    Grain,
    StructureAssessment,
    StructureAssessmentResult,
    StructureRequest,
)


def _excluded_refs(session: Session, scan: PopulationScan) -> tuple[dict[str, str], ...]:
    """The first ``MAX_EXCLUDED_LISTED`` excluded units by id, named by public ref; the total is reported separately."""
    listed = scan.excluded[:MAX_EXCLUDED_LISTED]
    if not listed:
        return ()
    model = Calculation if scan.grain is Grain.calculation else StructureDetermination
    refs: dict[int, str] = dict(
        session.execute(select(model.id, model.public_ref).where(model.id.in_([i for i, _, _ in listed]))).tuples().all()
    )
    return tuple(
        {"unit_ref": refs[i], "review_status": status.value if status is not None else "", "reason": reason}
        for i, status, reason in listed
    )


#: Blocking codes that are a confirmed finding about a source (the ``confirmed_blocking_findings`` default). The other
#: blocking codes are failures of the claim the request makes (a contradicted curvature, an unconverged optimisation, ...)
#: and no eligibility setting lifts them.
FINDING_BLOCK_PREFIXES = ("finding_invalidates", "role_invalidated")


def quality_state(unit: Any, calculations: Any, request: StructureRequest) -> str | None:
    """How the ``rejected_calculation_quality`` default touches a unit: ``"lifted"``, ``"applied"`` or ``None``.

    At the calculation grain the unit is the calculation: a rejected one is in the population only because the request
    permitted it (``lifted``). At the conformer and transition-state grains a determination rests on the calculations it
    pins: one with rejected quality that the request permitted is ``lifted``; one the default kept out (its source reads
    as unusable for quality, which under the default permitted qualities can only mean rejected) is ``applied``.
    *calculations* maps a calculation ref to its normalised facts.
    """
    if request.grain is Grain.calculation:
        return "lifted" if unit.quality == CalculationQuality.rejected.value else None
    if CalculationQuality.rejected in request.quality_set:
        pinned = (calculations.get(s.calculation_ref) for s in unit.sources if s.calculation_ref is not None)
        return "lifted" if any(c is not None and c.quality == CalculationQuality.rejected.value for c in pinned) else None
    if request.quality_set == DEFAULT_PERMITTED_QUALITY and any(s.unavailable_reason == REASON_SOURCE_QUALITY for s in unit.sources):
        return "applied"
    return None


def verdict_of(assessment: StructureAssessment, gate: RecordReviewStatus | None, quality: str | None, request: StructureRequest) -> Verdict:
    """Whether a unit competes under the request's eligibility settings, and which defaults it failed or passed by override.

    ``gate`` is the review status the review defaults judge the unit by (a calculation's own, the weakest of a conformer
    observation's and its group's, or ``None``). ``quality`` is :func:`quality_state`: a unit that rests on a rejected
    calculation the request permitted passed that default by override, and one whose source the default kept out failed it. A confirmed finding the request lifted is a
    ``finding_overridden`` advisory; both are read from the assessment, so a replay that recomputes it recomputes this.
    """
    if quality == "applied" and assessment.applicability is Applicability.applicable:
        # The kept-out source is in a role the request does not need: the assessment is applicable without it, and the
        # decision selects a unit by its assessment, so the verdict must not block what the decision selects.
        quality = None
    findings = [b for b in assessment.blocking if b.startswith(FINDING_BLOCK_PREFIXES)]
    other = [b for b in assessment.blocking if not b.startswith(FINDING_BLOCK_PREFIXES)]
    lifted = any(a.startswith("finding_overridden") for a in assessment.advisory)
    verdict = judge(
        applicable=assessment.applicability is Applicability.applicable and not other,
        review_status=gate if gate is not None else RecordReviewStatus.approved,
        blocking=findings,
        eligibility=request.eligibility,
        extra_defaults=(
            *((ELIGIBILITY_CONFIRMED_FINDINGS,) if lifted else ()),
            *((ELIGIBILITY_REJECTED_QUALITY,) if quality == "applied" else ()),
        ),
    )
    if quality == "lifted":
        verdict = replace(verdict, passed_only_by_override=(*verdict.passed_only_by_override, ELIGIBILITY_REJECTED_QUALITY))
    return verdict


def verdict_record(verdict: Verdict, gate: RecordReviewStatus | None) -> dict:
    """What a manifest records for one unit: the status it was judged by, and the verdict."""
    return {
        "gate_review_status": gate.value if gate is not None else None,
        "blocked_by": list(verdict.blocked_by),
        "passed_only_by_override": list(verdict.passed_only_by_override),
        "competes": verdict.competes,
    }


def assess_entry_structures(
    session: Session, *, entry_id: int, request: StructureRequest, require_snapshot: bool = True
) -> StructureAssessmentResult:
    """Assess every visible unit of an entry for the request.

    :param entry_id: The species entry (calculation and conformer grains) or transition state entry.
    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the population
        and everything loaded for it are one consistent state (``SnapshotNotConsistentError`` otherwise). A route
        opens a session for the purpose; only a caller that cannot (a test inside one outer transaction) passes
        ``False``, and the isolation level actually in force is reported on the result either way.
    :raises NotFoundError: unknown or hidden entry, or an explicit member naming nothing authorized.
    :raises CodedValueError: a bound exceeded; nothing is assessed.
    """
    isolation = begin_read_snapshot(session, require=require_snapshot)
    scan = scan_population(session, entry_id=entry_id, request=request)
    excluded = _excluded_refs(session, scan)
    loaded = load_population(session, scan, request)
    # The loader to engine seam (guard PR-3): a hidden ref in the captured facts (an observation's conformer group, a
    # dependency or witness calculation outside the population, a validation row's calculation) becomes a placeholder
    # token BEFORE any unit is assessed, decided, hashed or written to the manifest.
    loaded.subject, loaded.calculations, loaded.determinations = withhold_selection_facts(
        session, (loaded.subject, loaded.calculations, loaded.determinations)
    )

    calculations_by_ref = loaded.calculations_by_ref
    units = (
        [loaded.calculations[i] for i in sorted(loaded.calculations)]
        if request.grain is Grain.calculation
        else [loaded.determinations[i] for i in sorted(loaded.determinations)]
    )
    assessments: list[StructureAssessment] = [
        assess_unit(u, request=request, subject=loaded.subject, calculations=calculations_by_ref) for u in units
    ]
    unresolved = tuple(a.unit_ref for a in assessments if a.applicability is Applicability.unresolved)
    unsupported = tuple(a.unit_ref for a in assessments if a.applicability is Applicability.unsupported)
    notes: list[str] = []
    if unresolved or unsupported:
        # Uniform across every outcome: a result is scoped to what is eligible now, and these units might compete if
        # their missing facts were recorded or their form evaluated.
        notes.append(
            f"{len(unresolved)} unit(s) are unresolved and {len(unsupported)} unsupported; they do not compete in this "
            "result and may be applicable if their missing facts were recorded or their form evaluated"
        )
    unit_by_ref: dict[str, Any] = {
        **{c.calculation_ref: c for c in loaded.calculations.values()},
        **{d.determination_ref: d for d in loaded.determinations.values()},
    }
    gate_by_ref: dict[str, RecordReviewStatus | None] = {}
    if request.grain is Grain.calculation:
        gate_by_ref = {ref: unit.review_status for ref, unit in unit_by_ref.items()}
    else:
        ref_by_id = {i: d.determination_ref for i, d in loaded.determinations.items()}
        gate_by_ref = {ref_by_id[i]: scan.gate_status.get(i) for i in ref_by_id}
    verdicts = {}
    verdict_objects = []
    for a in assessments:
        unit = unit_by_ref[a.unit_ref]
        v = verdict_of(a, gate_by_ref[a.unit_ref], quality_state(unit, loaded.calculations_by_ref, request), request)
        verdict_objects.append(v)
        verdicts[a.unit_ref] = verdict_record(v, gate_by_ref[a.unit_ref])
    return StructureAssessmentResult(
        request=request,
        subject=loaded.subject,
        effective_statuses=tuple(sorted(scan.effective_statuses, key=lambda s: s.value)),
        total_units=scan.total_units,
        visible_units=len(scan.unit_ids),
        excluded_by_review=excluded,
        excluded_count=len(scan.excluded),
        nested_rows=scan.nested_rows,
        snapshot_isolation=isolation,
        calculations=tuple(loaded.calculations[i] for i in sorted(loaded.calculations)),
        determinations=tuple(loaded.determinations[i] for i in sorted(loaded.determinations)),
        assessments=tuple(assessments),
        unresolved_refs=unresolved,
        unsupported_refs=unsupported,
        notes=tuple(notes),
        candidate_statuses=tuple(sorted(scan.candidate_statuses, key=lambda s: s.value)),
        verdicts=verdicts,
        funnel=funnel(
            considered=scan.considered,
            removed_by_filters=scan.removed_by_filters,
            removed_by_floor=scan.removed_by_floor,
            terminal_excluded=scan.terminal_excluded,
            verdicts=verdict_objects,
        ),
    )
