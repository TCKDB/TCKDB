"""Network selection: assess, group, apply the registry, decide, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements or release choices: the function
reads, decides and returns a result whose ``manifest`` is the replayable decision record. Browse order and every
existing read are untouched.

Flow: assess every visible candidate (:func:`~app.services.network_selection.service.assess_network`, which opens
the snapshot first, refuses an over-bound population and reads one consistent state), hand the eligible nodes to the
engine with the registry, build the manifest, and refuse (with its own coded 422) a manifest over the snapshot-size
bound. Selection reads supplied products and never derives, re-anchors, evaluates, splices or reverses one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.network_selection.bounds import check_snapshot_size
from app.services.network_selection.engine import NetworkDecision, build_candidates, decide
from app.services.network_selection.manifest import build_manifest, canonical_bytes
from app.services.network_selection.models import NetworkAssessmentResult, NetworkRequest
from app.services.network_selection.service import assess_network
from app.services.selection_kernel import Outcome


@dataclass(frozen=True)
class NetworkSelection:
    """The selection service's result.

    ``manifest`` is the replayable decision manifest (JSON-ready, public references only); the other fields are
    views onto it for callers that do not want to dig.
    """

    outcome: Outcome
    selected_ref: str | None
    selection_basis: str | None
    assessment: NetworkAssessmentResult
    decision: NetworkDecision
    manifest: dict[str, Any]


def select_network(
    session: Session,
    *,
    request: NetworkRequest,
    require_snapshot: bool = True,
) -> NetworkSelection:
    """Assess every visible candidate of a network for the request and decide among the eligible.

    :param require_snapshot: See :func:`assess_network`.
    :raises NotFoundError: unknown network.
    :raises CodedValueError: a bound exceeded (nothing is assessed or chosen), a request naming what the network
        lacks, or a manifest over the snapshot-size bound.
    """
    assessment = assess_network(session, request=request, require_snapshot=require_snapshot)
    candidates = build_candidates(
        request, assessment.solves, assessment.determination_assessments, assessment.bundle_assessments
    )
    decision = decide(candidates, request=request)
    manifest = build_manifest(assessment, decision)
    check_snapshot_size(request.bounds, len(canonical_bytes(manifest)))
    return NetworkSelection(
        outcome=decision.outcome,
        selected_ref=decision.selected_ref,
        selection_basis=decision.selection_basis,
        assessment=assessment,
        decision=decision,
        manifest=manifest,
    )
