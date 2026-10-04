"""The public face of network selection: request mapping, reference checks, response building, visibility.

``select_network`` (selection.py) decides and records; this module is the thin layer the HTTP routes, and nothing
else, go through. It:

1. maps the public request onto the service's request and registry, with no database id accepted from the caller
   and the network named only by a public ref;
2. checks every public ref the request names (the network, each bath species) through the ordinary path-handle
   resolver, so a typo is a 404 and not a silent "nothing applies", and so a ref the read profile hides answers
   exactly as it does anywhere else (the reference model is checked by the service itself);
3. builds the response from the decision manifest, so the response and the downloadable manifest cannot disagree;
4. applies the visibility rule for solves outside the review floor, and re-seals the manifest digest after any
   redaction so the downloaded document still verifies and replays.

Visibility rule for ``excluded_by_review``: a solve that fails the effective floor is listed (and counted) only when
the read profile has no floor of its own. Under a profile floor the list is empty, the count is zero and
``excluded_by_review_withheld`` is true, so the field cannot reveal that such a solve exists. Redaction touches only
``population``; the decision, which is what replay recomputes, is never altered.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import NetworkObservable

from app.db.models.common import RecordReviewStatus
from app.db.models.network import Network
from app.db.models.species import SpeciesEntry
from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.schemas.reads.scientific_network_selection import (
    NetworkSelectionDisclosures,
    NetworkSelectionMember,
    NetworkSelectionMode,
    NetworkSelectionOutcome,
    NetworkSelectionPick,
    NetworkSelectionPolicy,
    NetworkSelectionPolicyInfo,
    NetworkSelectionRelations,
    NetworkSelectionRequest,
    NetworkSelectionRequestEcho,
    NetworkSelectionResponse,
    NetworkSelectionReview,
    NetworkSelectionScope,
)
from app.services.network_selection.manifest import manifest_digest
from app.services.network_selection.models import (
    BathRequest,
    NetworkRequest,
    OutputRequest,
    PartitionRequest,
    Scope,
)
from app.services.network_selection.selection import NetworkSelection, select_network
from app.services.scientific_read.handles import is_ref_handle, resolve_path_handle
from app.services.scientific_read.profile import current_read_profile
from app.services.selection_kernel import Outcome

#: ``Session.info`` key a TEST harness sets, explicitly, to be read under whatever isolation it already has.
SNAPSHOT_OPT_OUT = "tckdb_read_snapshot_opt_out"


def _ref_only(session: Session, model_cls: type, handle: str, *, kind_label: str) -> int:
    """Resolve a public ref to a row id, refusing an integer id outright (``invalid_handle``)."""
    if not is_ref_handle(handle):
        raise ValueError(f"invalid_handle: {handle!r} is not a <prefix>_<body> public ref")
    return resolve_path_handle(session, model_cls, handle, kind_label=kind_label)


def resolve_network_ref(session: Session, ref: str) -> int:
    return _ref_only(session, Network, ref, kind_label="network")


def check_request_refs(session: Session, body: NetworkSelectionRequest) -> None:
    """Every species ref the request names must exist and be visible to the caller (404 otherwise)."""
    for component in body.bath.components:
        _ref_only(session, SpeciesEntry, component.species_ref, kind_label="species_entry")


def _admin_policy(policy: NetworkSelectionPolicy) -> SelectionPolicy:
    if policy is NetworkSelectionPolicy.method_preferred:
        return SelectionPolicy.default
    return SelectionPolicy(policy.value)


def to_service_request(body: NetworkSelectionRequest, *, network_ref: str) -> NetworkRequest:
    """The service's request for a public one. The bounds are the server's (``BOUNDS_V1``), never the caller's."""
    from tckdb_schemas.network_declarations import NetworkRegimeKind

    components = body.bath.components
    fractions = tuple(c.mole_fraction for c in components if c.mole_fraction is not None)
    return NetworkRequest(
        network_ref=network_ref,
        scope=Scope(body.scope.value),
        channel_key=body.channel_key,
        observable=NetworkObservable(body.observable.value) if body.observable else None,
        outputs=tuple(OutputRequest(o.channel_key, NetworkObservable(o.observable.value)) for o in body.outputs),
        coefficient_basis=body.coefficient_basis,
        degeneracy_applied=body.degeneracy_applied,
        temperature_min_k=body.temperature_min_k,
        temperature_max_k=body.temperature_max_k,
        pressure_min_bar=body.pressure_min_bar,
        pressure_max_bar=body.pressure_max_bar,
        bath=BathRequest(tuple(c.species_ref for c in components), fractions or None),
        partition=PartitionRequest(
            tuple(body.partition.retained),
            tuple(body.partition.eliminated),
            tuple(tuple(lump) for lump in body.partition.lumps),
        ),
        boundaries=tuple((b.state, b.kind) for b in body.boundaries),
        regime_kind=NetworkRegimeKind(body.regime.kind.value),
        initial_state_hashes=tuple(body.regime.initial_state_hashes),
        source_composition_hash=body.source_composition_hash,
        sink_composition_hash=body.sink_composition_hash,
        objective=body.objective,
        reference_model_ref=body.reference_model_ref,
        reference_outputs=body.reference_outputs,
        min_review_status=body.min_review_status,
        admin_policy=_admin_policy(body.policy),
        first_only=body.mode is NetworkSelectionMode.first,
        # The route, not the engine, decides whether rules apply: only ``method_preferred`` consults any.
        apply_rules=body.policy is NetworkSelectionPolicy.method_preferred,
    )


def run_selection(session: Session, *, network_ref: str, body: NetworkSelectionRequest) -> NetworkSelection:
    """Run the service for a public request.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is not
    one raises ``SnapshotNotConsistentError`` instead of quietly answering under READ COMMITTED. Only a session that
    carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is read as it is; that exists for the test harness, which holds one
    outer transaction that cannot become a snapshot, and nothing in the application sets it.
    """
    return select_network(
        session,
        request=to_service_request(body, network_ref=network_ref),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


def profile_has_floor() -> bool:
    return current_read_profile().review_floor is not None


def redact_manifest(manifest: dict[str, Any], *, withhold_excluded: bool) -> dict[str, Any]:
    """A copy of the manifest safe to hand to this caller, with its digest re-sealed.

    Never alters ``decision``, ``solves`` or ``assessments``. The request gains the read profile it was made under (as
    every scientific response echoes it), which replay ignores.
    """
    out = copy.deepcopy(manifest)
    out["request"].update(current_read_profile().echo())
    population = out["population"]
    if withhold_excluded:
        population["excluded_count"] = 0
        population["excluded_by_review"] = []
        population["excluded_by_review_withheld"] = True
    else:
        population["excluded_by_review_withheld"] = False
    out["digest"] = {"algorithm": "sha256", "value": manifest_digest(out)}
    return out


def _effective_floor(
    manifest: dict[str, Any], fallback: RecordReviewStatus | None
) -> tuple[RecordReviewStatus, list[RecordReviewStatus]]:
    statuses = sorted(
        (RecordReviewStatus(v) for v in manifest["request"]["effective_review_statuses"]),
        key=lambda s: REVIEW_RANK[s],
    )
    floor = statuses[-1] if statuses else (fallback or RecordReviewStatus.approved)
    return floor, statuses


def _members_of(manifest: dict[str, Any], node_ref: str) -> tuple[str, list[NetworkSelectionMember]]:
    """The solve and the members (determination, eligible fits) of a node of the decision."""
    assessments = manifest["assessments"]
    if manifest["request"]["scope"] == Scope.single_channel.value:
        row = next(a for a in assessments["determinations"] if a["determination_ref"] == node_ref)
        return row["solve_ref"], [
            NetworkSelectionMember(
                determination_ref=row["determination_ref"],
                channel_key=row["channel_key"],
                kinetics_refs=list(row["eligible_fit_refs"]),
            )
        ]
    node = next(b for b in assessments["bundles"] if b["node_ref"] == node_ref)
    channel = {a["determination_ref"]: a["channel_key"] for a in assessments["determinations"]}
    return node["solve_ref"], [
        NetworkSelectionMember(determination_ref=ref, channel_key=channel.get(ref), kinetics_refs=list(fits))
        for ref, fits in node["member_fit_refs"]
    ]


def _pick(manifest: dict[str, Any], mode: NetworkSelectionMode) -> NetworkSelectionPick | None:
    decision = manifest["decision"]
    outcome = decision["outcome"]
    scope = NetworkSelectionScope(manifest["request"]["scope"])
    if outcome in (Outcome.policy_preferred.value, Outcome.sole_eligible_candidate.value):
        node_ref = decision["selected_ref"]
        basis, administrative, explanation = outcome, False, decision["basis"]
    else:
        first = decision["administrative_first"]
        if not (outcome == Outcome.incomparable_alternatives.value and mode is NetworkSelectionMode.first and first):
            return None
        node_ref, basis, administrative, explanation = first["node_ref"], "administrative_first", True, first["basis"]
    solve_ref, members = _members_of(manifest, node_ref)
    return NetworkSelectionPick(
        node_ref=node_ref,
        scope=scope,
        solve_ref=solve_ref,
        members=members,
        basis=basis,  # type: ignore[arg-type]
        administrative=administrative,
        explanation=explanation,
    )


def build_response(
    selection: NetworkSelection, *, body: NetworkSelectionRequest, manifest: dict[str, Any]
) -> NetworkSelectionResponse:
    """The typed response, built from the (already redacted) manifest. Public refs only."""
    decision = manifest["decision"]
    floor, statuses = _effective_floor(manifest, body.min_review_status)
    population = manifest["population"]
    disclosures = manifest.get("disclosures", {})
    return NetworkSelectionResponse(
        request=NetworkSelectionRequestEcho.model_validate(manifest["request"]),
        review=NetworkSelectionReview(effective_floor=floor, effective_statuses=statuses),
        policy=NetworkSelectionPolicyInfo(
            name=manifest["policy"]["name"],
            version=manifest["policy"]["version"],
            bounds_version=manifest["policy"]["bounds_version"],
            rules_applied=decision["rules_applied"],
            rules=decision["rules"],
        ),
        outcome=NetworkSelectionOutcome(manifest["outcome"]),
        basis=decision["basis"],
        selection=_pick(manifest, body.mode),
        fronts=decision["fronts"],
        administrative_order=decision["administrative_order"],
        determinations=manifest["assessments"]["determinations"],
        bundles=manifest["assessments"]["bundles"],
        relations=NetworkSelectionRelations(
            edges=decision["edges"],
            overridden_edges=decision["overridden_edges"],
            opposing_pairs=decision["opposing_pairs"],
            cycles=decision["cycles"],
            unused_edges=decision["unused_edges"],
        ),
        representations=decision["representations"],
        rule_matches=decision["rule_matches"],
        pair_checks=decision["pair_checks"],
        disclosures=NetworkSelectionDisclosures(
            unresolved_refs=disclosures.get("unresolved_refs", []),
            unsupported_refs=disclosures.get("unsupported_refs", []),
            ungrouped_fit_refs=manifest["assessments"]["ungrouped_fit_refs"],
            population=population["counts"],
            excluded_by_review=population["excluded_by_review"],
            excluded_count=population["excluded_count"],
            excluded_by_review_withheld=population["excluded_by_review_withheld"],
            notes=list(disclosures.get("notes", [])),
        ),
    )


__all__ = [
    "SNAPSHOT_OPT_OUT",
    "build_response",
    "check_request_refs",
    "profile_has_floor",
    "redact_manifest",
    "resolve_network_ref",
    "run_selection",
    "to_service_request",
]
