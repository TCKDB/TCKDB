"""The scoped structure assessment as an input to the transition-state trust projection (#723, rubric v5).

The trust rubric is a presence checklist and its runners are pure. The structure assessment is a separate, scoped
judgement of the saddle a depositor *declared* (a stored ``structure_determination``), and it needs queries. This
module runs it once, for one entry, and hands the rubric a :class:`TransitionStateTrustContext`.

Where it runs (owner decision O3). On a **single transition-state-entry detail read** and on the machine-review
evaluation of one entry. Never on a list: browse, search, the sibling entries of a detail read and the reaction
document carry :func:`not_assessed_on_lists_context`, which issues no statement. The same entry therefore renders the
same on every list page, and lists never slow down or fail on account of an assessment.

The question (owner decision O1). Fixed, evidence-only and echoed in the response (:data:`BADGE_TASK`), so anyone can
send it to ``POST /scientific/transition-state-entries/{ref}/evidence/select`` and get the same answer. The answer here
is **that endpoint's own answer**, taken from the same service and the same response builder; this module adds no
judgement.

What it never does. It never changes a label, a completeness number or ``is_certified`` (the rubric's only reader of
the assessment is a zero-weight warning). It caches nothing and shares nothing across profiles: it runs under the read
profile in force, so a declared target that rests on a calculation the curated profile hides reads as unresolved
exactly as it does on the endpoint (hidden == unknown == absent).

Imports of the structure-selection package are lazy: that package imports the read schemas, which import this package.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import StructureDeterminationTargetKind
from app.db.models.structure_determination import StructureDetermination
from app.db.models.transition_state import TransitionStateEntry
from app.services.trust.models import (
    TransitionStateScopedAssessment,
    TransitionStateScopedAssessmentOutcome,
    TransitionStateScopedAssessmentStatus,
    TransitionStateScopedAssessmentTask,
    TransitionStateScopedAssessmentUnit,
    TransitionStateTrustContext,
)

#: The declared-target kinds a transition-state entry owns (the unit population of the evidence-selection endpoint).
_DECLARED_KINDS = (StructureDeterminationTargetKind.saddle_point, StructureDeterminationTargetKind.geometry)

#: The fixed question, as the request body of the evidence-selection endpoint.
BADGE_TASK = TransitionStateScopedAssessmentTask()

_CURVATURE_ROLE = "curvature"


def not_assessed_on_lists_context() -> TransitionStateTrustContext:
    """The context for a surface that lists entries: no assessment, no pinned frequency result. Issues no statement."""
    return TransitionStateTrustContext(
        assessment=TransitionStateScopedAssessment(
            status=TransitionStateScopedAssessmentStatus.not_assessed_on_lists, task=BADGE_TASK
        ),
        declared_curvature_refs=frozenset(),
    )


def _no_declared_target(versions: dict[str, str]) -> TransitionStateTrustContext:
    return TransitionStateTrustContext(
        assessment=TransitionStateScopedAssessment(
            status=TransitionStateScopedAssessmentStatus.no_declared_target,
            task=BADGE_TASK,
            declared_targets=0,
            versions=versions,
        ),
        declared_curvature_refs=frozenset(),
    )


def _live_versions() -> dict[str, str]:
    from app.services.structure_selection import ASSESSMENT_VERSION, DECISION_VERSION, NORMALIZER_VERSION

    return {"assessment": ASSESSMENT_VERSION, "normalizer": NORMALIZER_VERSION, "decision": DECISION_VERSION}


def _assert_pinned_versions(live: dict[str, str]) -> None:
    from app.services.trust.rubrics import TS_SCOPED_STRUCTURE_VERSIONS

    if live != TS_SCOPED_STRUCTURE_VERSIONS:
        raise RuntimeError(
            f"structure-selection versions {live} differ from the {TS_SCOPED_STRUCTURE_VERSIONS} the transition-state "
            "rubric was written against; changing what the badge's warning means is a deliberate rubric bump."
        )


def scoped_ts_context(session: Session, entry: TransitionStateEntry) -> TransitionStateTrustContext:
    """Run the scoped assessment for one entry, under the read profile in force, and return the rubric's context.

    One statement when the entry declares nothing. When it declares a target, the structure-selection pipeline runs
    for the fixed :data:`BADGE_TASK`. A refusal on the pipeline's fixed bounds does not fail the read: the context
    carries ``not_assessed_over_bound`` and the refusal code, and nothing is inferred from a subset.
    """
    from app.api.error_contract import CodedValueError
    from app.schemas.reads.scientific_structure_selection import TransitionStateEvidenceSelectionRequest
    from app.services.structure_selection.public import build_response, to_service_request
    from app.services.structure_selection.selection import select_entry_structures

    live = _live_versions()
    _assert_pinned_versions(live)

    declared = session.scalar(
        select(func.count())
        .select_from(StructureDetermination)
        .where(
            StructureDetermination.transition_state_entry_id == entry.id,
            StructureDetermination.target_kind.in_(_DECLARED_KINDS),
        )
    )
    if not declared:
        return _no_declared_target(live)

    body = TransitionStateEvidenceSelectionRequest(**BADGE_TASK.model_dump())
    try:
        selection = select_entry_structures(
            session, entry_id=entry.id, request=to_service_request(body), require_snapshot=False
        )
    except CodedValueError as refusal:
        return TransitionStateTrustContext(
            assessment=TransitionStateScopedAssessment(
                status=TransitionStateScopedAssessmentStatus.not_assessed_over_bound,
                task=BADGE_TASK,
                refusal_code=refusal.code,
                versions=live,
            ),
            declared_curvature_refs=frozenset(),
        )
    response = build_response(selection, entry_ref=entry.public_ref)
    if not response.units:
        # Every determination was excluded by the profile floor or a terminal status: nothing is declared that the
        # caller may see. The endpoint answers ``no_candidates`` for the same body.
        return _no_declared_target(live)
    pins = frozenset(
        source.calculation_ref
        for determination in selection.assessment.determinations
        for source in determination.sources
        if source.role == _CURVATURE_ROLE and source.calculation_ref is not None
    )
    return TransitionStateTrustContext(
        assessment=TransitionStateScopedAssessment(
            status=TransitionStateScopedAssessmentStatus.assessed,
            task=BADGE_TASK,
            declared_targets=len(response.units),
            outcome=TransitionStateScopedAssessmentOutcome(response.outcome.value),
            qualifying_refs=list(response.selected_refs),
            contested_target_count=len(response.contested_targets),
            units=[
                TransitionStateScopedAssessmentUnit(
                    unit_ref=unit.unit_ref,
                    applicability=unit.applicability,
                    blocking=list(unit.blocking),
                    reasons=[reason.code for reason in unit.reasons],
                )
                for unit in response.units
            ],
            versions=live,
        ),
        declared_curvature_refs=pins,
    )


__all__ = ["BADGE_TASK", "not_assessed_on_lists_context", "scoped_ts_context"]
