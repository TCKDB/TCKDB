"""Statmech selection: qualify, decide, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements: the function reads, decides and
returns a result whose ``manifest`` is the replayable decision record. Browse order and every existing read are
untouched.

Flow: qualify every visible candidate
(:func:`~app.services.statmech_selection.service.qualify_target`, which reads in one snapshot and refuses an
over-bound population), decide over the assessed candidates with the registry (none active in this release), build
the manifest (refused whole if it exceeds its bound). Selection reads stored facts and never derives, converts or
computes a partition function, a thermochemical value or a population.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.scientific_read.profile import current_read_profile
from app.services.statmech_selection.decision import StatmechDecision, decide_statmech
from app.services.statmech_selection.manifest import build_manifest
from app.services.statmech_selection.models import StatmechRequest
from app.services.statmech_selection.rules import StatmechRule, default_rules, validate_rules
from app.services.statmech_selection.service import StatmechQualification, qualify_target
from app.services.statmech_selection.vocabulary import Outcome


@dataclass(frozen=True)
class StatmechSelection:
    """The selection service's result: the outcome, the qualification it rests on, the decision and its manifest."""

    outcome: Outcome
    qualification: StatmechQualification
    decision: StatmechDecision
    manifest: dict[str, Any]


def select_statmech(
    session: Session,
    *,
    request: StatmechRequest,
    owner_id: int,
    rules: Sequence[StatmechRule] | None = None,
    require_snapshot: bool = True,
) -> StatmechSelection:
    """Qualify every visible candidate of a target for the request and decide among them.

    :param rules: The registry to apply; defaults to the rules shipped with this release (none). Tests pass synthetic
        rules to build edges, conflicts and cycles.
    :param require_snapshot: See :func:`~app.services.statmech_selection.service.qualify_target`.
    :raises NotFoundError: unknown or hidden target, or an explicit candidate naming nothing authorized.
    :raises CodedValueError: a bound exceeded (candidates, nested rows, traversal depth, manifest size) or a population
        this release does not evaluate; nothing is decided from a subset.
    """
    registry = tuple(default_rules() if rules is None else rules)
    validate_rules(registry)
    qualification = qualify_target(session, request=request, owner_id=owner_id, require_snapshot=require_snapshot)
    decision = decide_statmech(
        request=request,
        subject=qualification.subject,
        candidates=qualification.candidates,
        assessments=qualification.assessments,
        rules=registry,
    )
    resolved = current_read_profile()
    profile = {
        "profile": resolved.profile.value,
        "review_floor": resolved.review_floor.value if resolved.review_floor is not None else None,
    }
    manifest = build_manifest(qualification, decision, profile=profile)
    return StatmechSelection(outcome=decision.outcome, qualification=qualification, decision=decision, manifest=manifest)


__all__ = ["StatmechSelection", "select_statmech"]
