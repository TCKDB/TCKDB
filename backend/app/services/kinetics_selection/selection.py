"""Kinetics selection: assess, group, order, decide, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements: the function reads, decides
and returns a result whose ``manifest`` is the replayable decision record. Browse order and every existing
read are untouched.

Flow: assess every visible record (:func:`~app.services.kinetics_selection.service.assess_reaction_entry_kinetics`,
which refuses an over-cap population and reads in one snapshot), group the eligible ones by determination, hand
the groups to the engine, and build the manifest. Selection reads supplied rates and never
derives, re-anchors or evaluates one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.kinetics_selection.engine import KineticsDecision, build_candidates, decide
from app.services.kinetics_selection.manifest import build_manifest
from app.services.kinetics_selection.models import KineticsAssessmentResult, KineticsRequest
from app.services.kinetics_selection.service import assess_reaction_entry_kinetics
from app.services.selection_kernel import Outcome


@dataclass(frozen=True)
class KineticsSelection:
    """The selection service's result.

    ``manifest`` is the replayable decision manifest (JSON-ready, public refs only); the other fields are views
    onto it for callers that do not want to dig.
    """

    outcome: Outcome
    selected_determination_ref: str | None
    representation_refs: tuple[str, ...]
    assessment: KineticsAssessmentResult
    decision: KineticsDecision
    manifest: dict[str, Any]


def select_reaction_entry_kinetics(
    session: Session,
    *,
    reaction_entry_id: int,
    request: KineticsRequest,
    require_snapshot: bool = True,
) -> KineticsSelection:
    """Assess every visible kinetics record of a reaction entry for the request and decide among the eligible.

    :param require_snapshot: See :func:`assess_reaction_entry_kinetics`.
    :raises NotFoundError: unknown reaction entry.
    :raises CodedValueError: ``kinetics_selection_population_too_large``; nothing is assessed.
    """
    assessment = assess_reaction_entry_kinetics(
        session, reaction_entry_id=reaction_entry_id, request=request, require_snapshot=require_snapshot
    )
    by_ref = {c.kinetics_ref: c for c in assessment.candidates}
    eligible = [by_ref[a.kinetics_ref] for a in assessment.assessments if a.physically_eligible]
    candidates = build_candidates(eligible, admin_policy=request.admin_policy)
    decision = decide(candidates, admin_policy=request.admin_policy)
    manifest = build_manifest(assessment, decision)
    return KineticsSelection(
        outcome=decision.outcome,
        selected_determination_ref=decision.selected_determination_ref,
        representation_refs=decision.representation_refs,
        assessment=assessment,
        decision=decision,
        manifest=manifest,
    )
