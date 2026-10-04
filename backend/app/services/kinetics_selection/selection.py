"""Kinetics selection: assess, group, apply the registry, decide, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements: the function reads, decides
and returns a result whose ``manifest`` is the replayable decision record. Browse order and every existing
read are untouched.

Flow: assess every visible record (:func:`~app.services.kinetics_selection.service.assess_reaction_entry_kinetics`,
which refuses an over-cap population and reads in one snapshot), group the eligible ones by determination, hand
the groups to the engine with the registry, and build the manifest. Selection reads supplied rates and never
derives, re-anchors or evaluates one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.kinetics_selection.engine import KineticsDecision, build_candidates, decide
from app.services.kinetics_selection.manifest import build_manifest
from app.services.kinetics_selection.models import KineticsAssessmentResult, KineticsRequest
from app.services.kinetics_selection.rules import KineticsRule, default_rules
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
    rules: Sequence[KineticsRule] | None = None,
    require_snapshot: bool = True,
) -> KineticsSelection:
    """Assess every visible kinetics record of a reaction entry for the request and decide among the eligible.

    :param rules: The registry to apply; defaults to the rules shipped with this release (none of them active).
        Tests pass additional rules to build edges, conflicts and cycles.
    :param require_snapshot: See :func:`assess_reaction_entry_kinetics`.
    :raises NotFoundError: unknown reaction entry.
    :raises CodedValueError: ``kinetics_selection_population_too_large``; nothing is assessed.
    """
    registry = tuple(default_rules() if rules is None else rules)
    assessment = assess_reaction_entry_kinetics(
        session, reaction_entry_id=reaction_entry_id, request=request, require_snapshot=require_snapshot
    )
    by_ref = {c.kinetics_ref: c for c in assessment.candidates}
    eligible = [by_ref[a.kinetics_ref] for a in assessment.assessments if a.physically_eligible]
    candidates = build_candidates(eligible, admin_policy=request.admin_policy)
    decision = decide(candidates, subject=assessment.subject, request=request, rules=registry)
    manifest = build_manifest(assessment, decision)
    return KineticsSelection(
        outcome=decision.outcome,
        selected_determination_ref=decision.selected_determination_ref,
        representation_refs=decision.representation_refs,
        assessment=assessment,
        decision=decision,
        manifest=manifest,
    )
