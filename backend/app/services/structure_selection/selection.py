"""Structure selection: assess, decide, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements: the function reads, decides and
returns a result whose ``manifest`` is the replayable decision record. Browse order and every existing read are
untouched.

Flow: assess every visible unit
(:func:`~app.services.structure_selection.service.assess_entry_structures`, which reads in one snapshot and refuses
an over-bound population), decide over the assessed units with the registry, build the manifest (refused whole if
it exceeds its bound). Selection reads stored numbers and never derives, converts or re-anchors one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.scientific_read.profile import current_read_profile
from app.services.structure_selection.decision import StructureDecision, decide_structures
from app.services.structure_selection.manifest import build_manifest
from app.services.structure_selection.models import (
    Grain,
    StructureAssessmentResult,
    StructureOutcome,
    StructureRequest,
)
from app.services.structure_selection.rules import StructureRule, default_rules, validate_rules
from app.services.structure_selection.service import assess_entry_structures


@dataclass(frozen=True)
class StructureSelection:
    """The selection service's result: the outcome, the assessment it rests on, the decision and its manifest."""

    outcome: StructureOutcome
    selected_refs: tuple[str, ...]
    assessment: StructureAssessmentResult
    decision: StructureDecision
    manifest: dict[str, Any]


def select_entry_structures(
    session: Session,
    *,
    entry_id: int,
    request: StructureRequest,
    rules: Sequence[StructureRule] | None = None,
    require_snapshot: bool = True,
) -> StructureSelection:
    """Assess every visible unit of an entry for the request and decide among them.

    :param rules: The registry to apply; defaults to the rules shipped with this release (none of them active).
        Tests pass additional rules to build edges, conflicts and cycles.
    :param require_snapshot: See :func:`assess_entry_structures`.
    :raises NotFoundError: unknown or hidden entry, or an explicit member naming nothing authorized.
    :raises CodedValueError: a bound exceeded (candidates, nested rows, traversal depth, manifest size); nothing is
        decided from a subset.
    """
    registry = tuple(default_rules() if rules is None else rules)
    validate_rules(registry)
    assessment = assess_entry_structures(session, entry_id=entry_id, request=request, require_snapshot=require_snapshot)
    units = assessment.calculations if request.grain is Grain.calculation else assessment.determinations
    calculations = {c.calculation_ref: c for c in assessment.calculations}
    decision = decide_structures(
        request=request,
        subject=assessment.subject,
        units=units,
        assessments=assessment.assessments,
        calculations=calculations,
        rules=registry,
    )
    resolved = current_read_profile()
    profile = {
        "profile": resolved.profile.value,
        "review_floor": resolved.review_floor.value if resolved.review_floor is not None else None,
    }
    manifest = build_manifest(assessment, decision, profile=profile, rules=registry)
    return StructureSelection(
        outcome=decision.outcome,
        selected_refs=decision.selected_refs,
        assessment=assessment,
        decision=decision,
        manifest=manifest,
    )
