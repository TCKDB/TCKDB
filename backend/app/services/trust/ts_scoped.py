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

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    StructureDeterminationTargetKind,
    StructureSourceRole,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
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


def not_assessed_on_lists_context(declared_curvature_refs: frozenset[str] = frozenset()) -> TransitionStateTrustContext:
    """The context for a surface that lists entries: no assessment, issues no statement.

    ``declared_curvature_refs`` are the calculations the entry's declarations pin in role ``curvature``, read for the
    whole page in one statement by :func:`declared_curvature_pins`, so the frequency-presence checks (and so the badge
    counts) agree with the entry's own page. The default, no pins, is the unscoped answer and is for tests of the rubric.
    """
    return TransitionStateTrustContext(
        assessment=TransitionStateScopedAssessment(
            status=TransitionStateScopedAssessmentStatus.not_assessed_on_lists, task=BADGE_TASK
        ),
        declared_curvature_refs=declared_curvature_refs,
    )


@dataclass(frozen=True)
class DetailScope:
    """The read is one transition-state entry's own page: the structure assessment runs."""


@dataclass(frozen=True)
class ListScope:
    """The read lists entries: no assessment, but the page's declared curvature pins (one grouped statement)."""

    pins: Mapping[int, frozenset[str]] = field(default_factory=dict)


TrustScope = DetailScope | ListScope


def declared_curvature_pins(session: Session, entry_ids: list[int]) -> ListScope:
    """The calculations each entry's declared targets pin in role ``curvature``, for every entry in ONE statement.

    The list surfaces' counterpart of what the detail read learns from the assessment. It is not the assessment: it
    reads which result a declaration points at, never whether the target qualifies.
    """
    from app.services.scientific_read.common import visible_statuses

    # The detail read's exclusion rule, in the same statement: a determination whose owning entry is rejected, deprecated
    # or below the profile floor is not part of that entry's world, so it pins nothing. An entry with no review row is
    # ``not_reviewed``.
    allowed = visible_statuses(min_review_status=None, include_rejected=False, include_deprecated=False)
    pins: dict[int, set[str]] = {}
    if entry_ids:
        rows = session.execute(
            select(StructureDetermination.transition_state_entry_id, Calculation.public_ref)
            .select_from(StructureDeterminationSource)
            .join(StructureDetermination, StructureDeterminationSource.determination_id == StructureDetermination.id)
            .join(Calculation, StructureDeterminationSource.calculation_id == Calculation.id)
            .outerjoin(
                RecordReview,
                (RecordReview.record_type == SubmissionRecordType.transition_state_entry)
                & (RecordReview.record_id == StructureDetermination.transition_state_entry_id),
            )
            .where(
                func.coalesce(RecordReview.status, RecordReviewStatus.not_reviewed).in_(allowed),
                StructureDetermination.transition_state_entry_id.in_(entry_ids),
                StructureDetermination.target_kind.in_(_DECLARED_KINDS),
                StructureDeterminationSource.role == StructureSourceRole.curvature,
            )
        ).all()
        for entry_id, ref in rows:
            pins.setdefault(entry_id, set()).add(ref)
    return ListScope({k: frozenset(v) for k, v in pins.items()})


def context_for(session: Session, entry: TransitionStateEntry, scope: TrustScope) -> TransitionStateTrustContext:
    """The rubric's context under *scope*: the assessment on a detail read, the page's pins on a list."""
    if isinstance(scope, DetailScope):
        return scoped_ts_context(session, entry)
    return not_assessed_on_lists_context(scope.pins.get(entry.id, frozenset()))


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


__all__ = [
    "BADGE_TASK",
    "DetailScope",
    "ListScope",
    "TrustScope",
    "context_for",
    "declared_curvature_pins",
    "not_assessed_on_lists_context",
    "scoped_ts_context",
]
