"""Statmech qualification: scan, load, assess, summarize.

Service layer only. No HTTP and no persistence: the function reads and returns a result. Browse order and every existing
read are untouched, and nothing here prefers one model over another (that is the decision stage).

Flow: open one read-only REPEATABLE READ snapshot, scan the visible population (review floor first, terminal statuses
out), refuse above a bound with a coded 422 before anything is loaded, load and normalise it, assess every candidate,
summarize. A request this release does not evaluate (a deferred task, a non-gas environment, ...) is answered
``unsupported_task`` after the target has been resolved and shown to be visible, with no candidate loaded and none
substituted.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration
from app.services.read_snapshot import begin_read_snapshot
from app.services.statmech_selection.assessment import (
    QualificationSummary,
    StatmechAssessment,
    assess_candidate,
    request_support,
    summarize,
)
from app.services.statmech_selection.facts import CandidateFacts, SubjectFacts
from app.services.statmech_selection.loader import (
    MAX_EXCLUDED_LISTED,
    load_population,
    load_subject,
    scan_population,
)
from app.services.statmech_selection.models import StatmechRequest
from app.services.structure_selection.models import NormalizedCalculation


@dataclass(frozen=True)
class StatmechQualification:
    """The qualification service's result: the normalised request, population counts, candidates, assessments."""

    request: StatmechRequest
    subject: SubjectFacts
    effective_statuses: tuple[RecordReviewStatus, ...]
    total_candidates: int
    visible_candidates: int
    excluded_by_review: tuple[dict[str, str], ...]
    excluded_count: int
    nested_rows: int
    snapshot_isolation: str
    candidates: tuple[CandidateFacts, ...]
    calculations: tuple[NormalizedCalculation, ...]
    assessments: tuple[StatmechAssessment, ...]
    summary: QualificationSummary


def _excluded(session: Session, scan_excluded: tuple[tuple[int, RecordReviewStatus | None, str], ...]) -> tuple[dict[str, str], ...]:
    listed = scan_excluded[:MAX_EXCLUDED_LISTED]
    if not listed:
        return ()
    ids = [i for i, _, _ in listed]
    refs = dict(session.execute(select(Statmech.id, Statmech.public_ref).where(Statmech.id.in_(ids))).tuples().all())
    # A candidate is named by its declaration when it has one (``smd_``), by its record otherwise (``sm_``): the same name
    # everywhere else in the answer.
    refs.update(
        session.execute(
            select(StatmechModelDeclaration.statmech_id, StatmechModelDeclaration.public_ref).where(
                StatmechModelDeclaration.statmech_id.in_(ids)
            )
        ).tuples().all()
    )
    return tuple(
        {"candidate_ref": refs[i], "review_status": status.value if status is not None else "", "reason": reason}
        for i, status, reason in listed
    )


def qualify_target(
    session: Session, *, request: StatmechRequest, owner_id: int, require_snapshot: bool = True
) -> StatmechQualification:
    """Assess every visible candidate of one target for the request.

    :param owner_id: The species entry or transition state entry the request's target names (resolved by the caller from
        its public ref).
    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the population and
        everything loaded for it are one consistent state (``SnapshotNotConsistentError`` otherwise). A route opens a
        session for the purpose; only a caller that cannot (a test inside one outer transaction) passes ``False``, and the
        isolation level actually in force is reported on the result either way.
    :raises NotFoundError: unknown or hidden target, or an explicit candidate naming nothing authorized.
    :raises CodedValueError: a bound exceeded or a population this release does not evaluate; nothing is assessed.
    """
    isolation = begin_read_snapshot(session, require=require_snapshot)
    scan = scan_population(session, request=request, owner_id=owner_id)
    excluded = _excluded(session, scan.excluded)
    ordered_statuses = tuple(sorted(scan.effective_statuses, key=lambda s: s.value))
    if request_support(request):
        subject = load_subject(session, scan.owner_kind, scan.owner_id)
        assessments: tuple[StatmechAssessment, ...] = ()
        candidates: tuple[CandidateFacts, ...] = ()
        calculations: tuple[NormalizedCalculation, ...] = ()
    else:
        loaded = load_population(session, scan, request)
        subject = loaded.subject
        candidates = tuple(loaded.candidates[i] for i in sorted(loaded.candidates))
        calculations = tuple(loaded.calculations[i] for i in sorted(loaded.calculations))
        by_ref = loaded.calculations_by_ref
        assessments = tuple(
            assess_candidate(
                c, request=request, subject=subject, calculations=by_ref, effective_statuses=scan.effective_statuses
            )
            for c in candidates
        )
    return StatmechQualification(
        request=request,
        subject=subject,
        effective_statuses=ordered_statuses,
        total_candidates=scan.total,
        visible_candidates=len(scan.statmech_ids),
        excluded_by_review=excluded,
        excluded_count=len(scan.excluded),
        nested_rows=scan.nested_rows,
        snapshot_isolation=isolation,
        candidates=candidates,
        calculations=calculations,
        assessments=assessments,
        summary=summarize(request, assessments),
    )
