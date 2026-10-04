"""Network assessment: snapshot, scan, load, assess.

Service layer only. No HTTP, no persistence. Browse order and every existing read are untouched. The
question here is only which determinations (channel scope) or declared product sets (bundle scope)
can answer the requested coefficient, and why each other one cannot.

Flow, and the order matters:

1. **The snapshot opens first**, before any public ref is resolved: the isolation level cannot change
   after a statement has run, so resolving the network ref first would leave the whole read outside a
   snapshot (or, with ``require_snapshot``, surface :class:`SnapshotNotConsistentError` as a 500 for a
   caller whose only mistake was an unknown ref).
2. The complete authorized population is scanned and counted against every bound; over any bound the
   decision is refused, never made on a prefix.
3. The population is loaded and normalised; the request's own locators are checked against the network.
4. Every candidate is assessed, with all reasons.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import W_NETWORK_DECLARATION_INVALID

from app.api.error_contract import CodedValueError
from app.services.network_selection.assessment import (
    assess_bundle_scope,
    assess_channel_scope,
    ungrouped_fits,
)
from app.services.network_selection.bounds import check_snapshot_size
from app.services.network_selection.loader import (
    excluded_listing,
    load_population,
    resolve_reference_model,
    scan_population,
)
from app.services.network_selection.models import (
    NetworkAssessmentResult,
    NetworkFacts,
    NetworkRequest,
    Scope,
    SolveFacts,
)
from app.services.read_snapshot import begin_read_snapshot
from app.services.selection_kernel import Applicability

#: What a result is scoped to, said once and uniformly: a preference is within the eligible population only.
SCOPE_NOTE = (
    "{unresolved} candidate(s) are unresolved and {unsupported} unsupported; they do not compete in this result "
    "and may be applicable if their missing facts were recorded or their form evaluated"
)


def _request_invalid(field: str, message: str) -> CodedValueError:
    return CodedValueError(
        W_NETWORK_DECLARATION_INVALID, message, context={"field": field}, message_prefix=False
    )


def check_request_against_network(request: NetworkRequest, network: NetworkFacts) -> None:
    """The request's locators must name this network's own states and channels.

    A composition hash or a channel key that is not the network's is a request mistake, refused rather than
    quietly yielding an empty population. Equal endpoints or equal keys in another network establish nothing.
    """
    hashes = {s.composition_hash for s in network.states}
    named = {
        "partition": [
            *request.partition.retained,
            *request.partition.eliminated,
            *(m for lump in request.partition.lumps for m in lump),
        ],
        "boundaries": [h for h, _ in request.boundaries],
        "initial_state_hashes": list(request.initial_state_hashes),
        "endpoints": [h for h in (request.source_composition_hash, request.sink_composition_hash) if h],
    }
    for where, names in named.items():
        unknown = sorted(n for n in names if n not in hashes)
        if unknown:
            raise _request_invalid(f"request.{where}", f"the request names state(s) the network does not have: {unknown}.")
    for output in request.required_outputs():
        if network.channel(output.channel_key) is None:
            raise _request_invalid(
                "request.channel_key" if request.scope is Scope.single_channel else "request.outputs",
                f"the request names channel '{output.channel_key}', which the network does not have.",
            )


def snapshot_size_bytes(
    request: NetworkRequest, network: NetworkFacts, solves: tuple[SolveFacts, ...]
) -> int:
    """Size of the canonical public form of what was captured (request, network, solves)."""
    payload: dict[str, Any] = {
        "request": request.to_dict(),
        "network": network.to_dict(),
        "solves": [s.to_dict() for s in solves],
    }
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def assess_network(
    session: Session, *, request: NetworkRequest, require_snapshot: bool = True
) -> NetworkAssessmentResult:
    """Assess every visible candidate of a network for the request.

    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the
        population and everything loaded for it are one consistent state (``SnapshotNotConsistentError``
        otherwise). A route opens a session for the purpose; only a caller that cannot (a test inside one
        outer transaction) passes ``False``, and the isolation level actually in force is reported either way.
    :raises NotFoundError: unknown network, or an unknown or hidden reference model.
    :raises CodedValueError: ``network_selection_population_too_large`` or ``..._snapshot_too_large`` (nothing
        is assessed), or ``network_declaration_invalid`` for a request that names states or channels the
        network does not have.
    """
    isolation = begin_read_snapshot(session, require=require_snapshot)
    scan = scan_population(session, request=request)
    resolve_reference_model(session, request)
    network, solves = load_population(session, scan, request)
    check_request_against_network(request, network)
    check_snapshot_size(request.bounds, snapshot_size_bytes(request, network, solves))

    determination_assessments = []
    bundle_assessments = []
    ungrouped: list[str] = []
    if request.scope is Scope.single_channel:
        determination_assessments, ungrouped = assess_channel_scope(solves, network, request)
    else:
        bundle_assessments, determination_assessments = assess_bundle_scope(solves, network, request)
        wanted = {o.channel_key for o in request.required_outputs()}
        for solve in solves:
            ungrouped.extend(ungrouped_fits(solve, wanted))
    judged = [*determination_assessments, *bundle_assessments]
    unresolved = tuple(
        getattr(a, "determination_ref", None) or a.node_ref for a in judged if a.applicability is Applicability.unresolved
    )
    unsupported = tuple(
        getattr(a, "determination_ref", None) or a.node_ref for a in judged if a.applicability is Applicability.unsupported
    )
    notes: list[str] = []
    if unresolved or unsupported:
        notes.append(SCOPE_NOTE.format(unresolved=len(unresolved), unsupported=len(unsupported)))
    if request.scope is not Scope.single_channel:
        without_sets = sum(1 for s in solves if not (s.target or {}).get("product_sets"))
        if without_sets:
            notes.append(f"{without_sets} solve(s) declare no product set and cannot answer a bundle request")
    return NetworkAssessmentResult(
        request=request,
        network=network,
        effective_statuses=tuple(sorted(scan.effective_statuses, key=lambda s: s.value)),
        counts=dict(scan.counts),
        excluded_by_review=excluded_listing(session, scan),
        excluded_count=len(scan.excluded),
        snapshot_isolation=isolation,
        solves=solves,
        determination_assessments=tuple(determination_assessments),
        bundle_assessments=tuple(bundle_assessments),
        ungrouped_fit_refs=tuple(ungrouped),
        unresolved_refs=unresolved,
        unsupported_refs=unsupported,
        notes=tuple(notes),
    )
