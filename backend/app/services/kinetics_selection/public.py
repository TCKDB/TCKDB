"""The public face of kinetics selection: request mapping, reference checks, response building, visibility.

``select_reaction_entry_kinetics`` (selection.py) decides and records; this module is the thin layer the HTTP
routes, and nothing else, go through. It does five things:

1. maps the public request onto the service's request and registry, with no database id accepted from the caller;
2. checks every public ref the request names (the reaction entry, a transition state entry, a network, each
   collider species) through the ordinary path-handle resolver, so a typo is a 404 and not a silent "nothing
   applies", and so a ref the caller's read profile hides answers exactly as it does anywhere else;
3. builds the response from the decision manifest, so the response and the downloadable manifest cannot disagree;
4. applies the visibility rule for records outside the review floor (below);
5. redacts the manifest under a read profile that imposes a floor.

Visibility rule for ``excluded_by_review``
------------------------------------------
Records that fail the effective review floor are listed by public ref (and counted) only when the read profile has
no floor of its own (``exploratory``). Under ``curated`` the floor is ``approved``: a record below it is one the
caller could not have reached through any other curated read, so naming or counting it here would be a leak. Under a
profile floor the list is empty, the count is zero, ``excluded_by_review_withheld`` is true, and the manifest's count
of all rows for the entry is replaced by the visible count so the difference cannot be recovered from the arithmetic.
The redaction touches only ``population``; the decision, which is what replay recomputes, is never altered.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.common import KineticsDirection, RecordReviewStatus
from app.db.models.network import Network
from app.db.models.reaction import ReactionEntry
from app.db.models.species import Species
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.schemas.reads.scientific_kinetics_selection import (
    KineticsSelectionCandidate,
    KineticsSelectionColliderComponent,
    KineticsSelectionColliderEcho,
    KineticsSelectionDetermination,
    KineticsSelectionDisclosures,
    KineticsSelectionEdge,
    KineticsSelectionExcluded,
    KineticsSelectionMode,
    KineticsSelectionOutcome,
    KineticsSelectionPick,
    KineticsSelectionPolicy,
    KineticsSelectionPolicyInfo,
    KineticsSelectionPressureEcho,
    KineticsSelectionReason,
    KineticsSelectionRelations,
    KineticsSelectionRequest,
    KineticsSelectionRequestEcho,
    KineticsSelectionResponse,
    KineticsSelectionReview,
    KineticsSelectionTargetEcho,
)
from app.services.kinetics_selection.models import (
    MAX_CANDIDATES,
    ColliderRequest,
    KineticsRequest,
    PressureKind,
    PressureRequest,
    TargetRequest,
)
from app.services.kinetics_selection.selection import KineticsSelection, select_reaction_entry_kinetics
from app.services.scientific_read.handles import is_ref_handle, resolve_path_handle
from app.services.scientific_read.profile import current_read_profile
from app.services.selection_kernel import Outcome


def _ref_only(session: Session, model_cls: type, handle: str, *, kind_label: str) -> int:
    """Resolve a public ref to a row id, refusing an integer id outright.

    The scientific detail routes still accept integers for history's sake; selection does not, so a deposit-time
    integer can never be the way into it (``invalid_handle``). A ref of another kind is ``handle_type_mismatch`` and a
    ref the read profile hides is the same 404 as an unknown one, both from the ordinary path-handle resolver.
    """
    if not is_ref_handle(handle):
        raise ValueError(f"invalid_handle: {handle!r} is not a <prefix>_<body> public ref")
    return resolve_path_handle(session, model_cls, handle, kind_label=kind_label)


def resolve_reaction_entry_ref(session: Session, ref: str) -> int:
    return _ref_only(session, ReactionEntry, ref, kind_label="reaction_entry")


def check_request_refs(session: Session, body: KineticsSelectionRequest) -> None:
    """Every public ref the request names must exist and be visible to the caller (404 otherwise)."""
    target = body.target
    if target.transition_state_entry_ref is not None:
        _ref_only(session, TransitionStateEntry, target.transition_state_entry_ref, kind_label="transition_state_entry")
    if target.network_ref is not None:
        _ref_only(session, Network, target.network_ref, kind_label="network")
    if body.collider is not None:
        for component in body.collider.components:
            _ref_only(session, Species, component.species_ref, kind_label="species")


def _admin_policy(policy: KineticsSelectionPolicy) -> SelectionPolicy:
    if policy is KineticsSelectionPolicy.method_preferred:
        return SelectionPolicy.default
    return SelectionPolicy(policy.value)


def to_service_request(body: KineticsSelectionRequest) -> KineticsRequest:
    """The service's request for a public one. The population cap is fixed server-side."""
    collider = None
    if body.collider is not None:
        components = body.collider.components
        # The request schema guarantees every component of a mixture has a fraction and a single collider has none.
        fractions = tuple(c.mole_fraction for c in components if c.mole_fraction is not None)
        collider = ColliderRequest(tuple(c.species_ref for c in components), fractions or None)
    return KineticsRequest(
        direction=KineticsDirection(body.direction.value),
        target=TargetRequest(
            kind=body.target.kind,
            transition_state_entry_ref=body.target.transition_state_entry_ref,
            network_ref=body.target.network_ref,
            channel_key=body.target.channel_key,
        ),
        coefficient_basis=body.coefficient_basis,
        temperature_min_k=body.temperature_min_k,
        temperature_max_k=body.temperature_max_k,
        pressure=PressureRequest(PressureKind(body.pressure.kind.value), body.pressure.min_bar, body.pressure.max_bar),
        collider=collider,
        min_review_status=body.min_review_status,
        admin_policy=_admin_policy(body.policy),
        max_candidates=MAX_CANDIDATES,
        apply_rules=body.policy is KineticsSelectionPolicy.method_preferred,
    )


def run_selection(session: Session, *, reaction_entry_id: int, body: KineticsSelectionRequest) -> KineticsSelection:
    """Run the service for a public request.

    ``method_preferred`` applies the shipped registry (every rule in this release is inactive, which the response
    says). The administrative policies apply no rule at all: they order by review status and recency exactly as the
    browse endpoints do, and say so. The population cap is the service's fixed 500 (a coded 422 raised before
    anything is assessed); it counts only records visible to the caller.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is
    not one (``get_db``'s, say, if a route were wired to it by mistake) raises ``SnapshotNotConsistentError`` instead
    of quietly answering under READ COMMITTED. Only a session that carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is
    read as it is, with the isolation actually in force reported in the manifest; that exists for the test harness,
    which holds one outer transaction that cannot become a snapshot, and nothing in the application sets it.
    """
    return select_reaction_entry_kinetics(
        session,
        reaction_entry_id=reaction_entry_id,
        request=to_service_request(body),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


#: ``Session.info`` key a TEST harness sets, explicitly, to be read under whatever isolation it already has.
SNAPSHOT_OPT_OUT = "tckdb_read_snapshot_opt_out"


def profile_has_floor() -> bool:
    return current_read_profile().review_floor is not None


def redact_manifest(manifest: dict[str, Any], *, withhold_excluded: bool) -> dict[str, Any]:
    """A copy of the manifest safe to hand to this caller. Never alters ``decision`` or ``candidates``."""
    out = copy.deepcopy(manifest)
    # The candidate cap is the server's, not a field of the request; it is not part of what is downloaded.
    out["request"].pop("max_candidates", None)
    # The read profile the manifest was made under, as every scientific response echoes it. Replay reads only the
    # administrative policy, so these keys cannot change a decision.
    out["request"].update(current_read_profile().echo())
    population = out["population"]
    if withhold_excluded:
        population["kinetics_rows_for_entry"] = population["visible_candidates"]
        population["excluded_count"] = 0
        population["excluded_by_review"] = []
        population["excluded_by_review_withheld"] = True
    else:
        population["excluded_by_review_withheld"] = False
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


def _pick(manifest: dict[str, Any], mode: KineticsSelectionMode) -> KineticsSelectionPick | None:
    decision = manifest["decision"]
    outcome = decision["outcome"]
    if outcome in (Outcome.policy_preferred.value, Outcome.sole_eligible_candidate.value):
        return KineticsSelectionPick(
            determination_ref=decision["selected_determination_ref"],
            kinetics_refs=list(decision["representation_refs"]),
            basis=outcome,
            administrative=False,
            explanation=decision["basis"],
        )
    first = decision["administrative_first"]
    if outcome == Outcome.incomparable_alternatives.value and mode is KineticsSelectionMode.first and first:
        refs: list[str] = next(
            (d["representation_refs"] for d in manifest["determinations"] if d["determination_ref"] == first["determination_ref"]),
            [],
        )
        return KineticsSelectionPick(
            determination_ref=first["determination_ref"],
            kinetics_refs=list(refs),
            basis="administrative_first",
            administrative=True,
            explanation=first["basis"],
        )
    return None


def build_response(
    selection: KineticsSelection,
    *,
    body: KineticsSelectionRequest,
    reaction_entry_ref: str,
    manifest: dict[str, Any],
) -> KineticsSelectionResponse:
    """The typed response, built from the (already redacted) manifest. Public refs only."""
    decision = manifest["decision"]
    floor, statuses = _effective_floor(manifest, body.min_review_status)
    population = manifest["population"]
    disclosures = manifest.get("disclosures", {})
    candidates = [
        KineticsSelectionCandidate(
            kinetics_ref=c["kinetics_ref"],
            review_status=c["review_status"],
            created_at=c["created_at"],
            scientific_origin=c["scientific_origin"],
            model_kind=c["model_kind"],
            direction=c["direction"],
            determination_ref=c["determination"]["determination_ref"] if c["determination"] else None,
            representation_role=c["representation_role"],
            applicability_state=c["applicability_state"],
            applicability_declaration=c["applicability"],
            protocol_state=c["protocol_state"],
            protocol=c["protocol"],
            applicability=c["assessment"]["applicability"],
            reasons=[KineticsSelectionReason(**r) for r in c["assessment"]["reasons"]],
            blocking=c["assessment"]["blocking"],
            advisory=c["assessment"]["advisory"],
            eligible=c["eligible"],
        )
        for c in manifest["candidates"]
    ]
    relations = KineticsSelectionRelations(
        edges=[KineticsSelectionEdge(**e) for e in decision["edges"]],
        overridden_edges=decision["overridden_edges"],
        opposing_pairs=decision["opposing_pairs"],
        cycles=decision["cycles"],
        unused_edges=decision["unused_edges"],
    )
    request = manifest["request"]
    collider = request["collider"]
    return KineticsSelectionResponse(
        request=KineticsSelectionRequestEcho(
            reaction_entry_ref=reaction_entry_ref,
            direction=body.direction,
            target=KineticsSelectionTargetEcho(**request["target"]),
            coefficient_basis=body.coefficient_basis,
            temperature_min_k=request["temperature_min_k"],
            temperature_max_k=request["temperature_max_k"],
            pressure=KineticsSelectionPressureEcho(**request["pressure"]),
            collider=(
                None
                if collider is None
                else KineticsSelectionColliderEcho(
                    components=[
                        KineticsSelectionColliderComponent(
                            species_ref=ref,
                            mole_fraction=None if collider["mole_fractions"] is None else collider["mole_fractions"][i],
                        )
                        for i, ref in enumerate(collider["species_refs"])
                    ]
                )
            ),
            policy=body.policy,
            mode=body.mode,
            min_review_status=body.min_review_status,
        ),
        review=KineticsSelectionReview(effective_floor=floor, effective_statuses=statuses),
        policy=KineticsSelectionPolicyInfo(
            name=manifest["policy"]["name"],
            version=manifest["policy"]["version"],
            rules_applied=decision["rules_applied"],
            rules=decision["rules"],
        ),
        outcome=KineticsSelectionOutcome(manifest["outcome"]),
        basis=decision["basis"],
        selection=_pick(manifest, body.mode),
        fronts=decision["fronts"],
        administrative_order=decision["administrative_order"],
        determinations=[
            KineticsSelectionDetermination(**d) for d in manifest["determinations"]
        ],
        candidates=candidates,
        relations=relations,
        rule_matches=decision["rule_matches"],
        pair_checks=decision["pair_checks"],
        disclosures=KineticsSelectionDisclosures(
            unresolved_refs=disclosures.get("unresolved_refs", []),
            unsupported_refs=disclosures.get("unsupported_refs", []),
            visible_candidates=population["visible_candidates"],
            excluded_by_review=[KineticsSelectionExcluded(**e) for e in population["excluded_by_review"]],
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
    "resolve_reaction_entry_ref",
    "run_selection",
    "to_service_request",
]
