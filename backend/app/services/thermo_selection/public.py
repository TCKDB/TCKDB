"""The public face of H298 selection: request mapping, response building, visibility.

``select_h298`` (service.py) decides and records; this module is the thin layer the HTTP
route, and nothing else, goes through. It does four things:

1. maps the public request (policy, result mode, public refs) onto the service's request and
   registry, with no database id accepted from the caller;
2. builds the response from the decision manifest, so the response and the downloadable
   manifest cannot disagree;
3. applies the visibility rule for records outside the review floor (below);
4. redacts the manifest under a read profile that imposes a floor.

Visibility rule for ``excluded_by_review``
------------------------------------------
Records that fail the effective review floor are listed by public ref only when the read
profile has no floor of its own (``exploratory``). Under ``curated`` the floor is ``approved``:
a record below it is one the caller could not have reached through any other curated read
(a detail handle for it answers 404), so naming it here would be a leak. Under a profile floor
the list is empty, ``excluded_by_review_withheld`` is true, and the manifest's count of all
rows for the entry is replaced by the visible count so the difference cannot be recovered
from the arithmetic. The redaction touches only ``population``; the decision, which is what
replay recomputes, is never altered.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus, ThermoTargetKind
from app.db.models.species import ConformerGroup
from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.schemas.reads.scientific_thermo_selection import (
    QUANTITY_H298,
    REFERENCE_TEMPERATURE_K,
    W_THERMO_SELECTION_POPULATION_TOO_LARGE,
    ThermoSelectionCandidate,
    ThermoSelectionDisclosures,
    ThermoSelectionEdge,
    ThermoSelectionExcluded,
    ThermoSelectionMode,
    ThermoSelectionOutcome,
    ThermoSelectionPick,
    ThermoSelectionPolicy,
    ThermoSelectionPolicyInfo,
    ThermoSelectionReason,
    ThermoSelectionRelations,
    ThermoSelectionRequest,
    ThermoSelectionRequestEcho,
    ThermoSelectionResponse,
    ThermoSelectionReview,
    ThermoSelectionTargetEcho,
)
from app.services.scientific_read.handles import (
    handle_type_mismatch_error,
    is_ref_handle,
    prefix_for,
    resolve_conformer_group_handle,
    resolve_species_entry_handle,
)
from app.services.scientific_read.profile import current_read_profile
from app.services.thermo_selection.models import MAX_CANDIDATES, H298Request, H298Selection, Outcome
from app.services.thermo_selection.service import select_h298

#: ``Session.info`` key a TEST harness sets, explicitly, to be read under whatever isolation it already has.
SNAPSHOT_OPT_OUT = "tckdb_read_snapshot_opt_out"


def ref_only_handle(session: Session, model_cls: type, handle: str, *, kind_label: str, resolver) -> int:
    """Resolve a public ref to a row id, refusing an integer id outright.

    The scientific detail routes still accept integers for history's sake; this one does not,
    so a deposit-time integer can never be the way into a selection.
    """
    if not is_ref_handle(handle):
        raise ValueError(f"invalid_handle: {handle!r} is not a <prefix>_<body> public ref")
    expected = prefix_for(model_cls)
    prefix = handle.split("_", 1)[0]
    if prefix != expected:
        raise handle_type_mismatch_error(kind_label, expected, prefix, noun="handle")
    return resolver(session, handle)


def resolve_species_entry_ref(session: Session, ref: str) -> int:
    from app.db.models.species import SpeciesEntry

    return ref_only_handle(
        session, SpeciesEntry, ref, kind_label="species_entry", resolver=resolve_species_entry_handle
    )


def resolve_group_ref(session: Session, ref: str) -> int:
    return ref_only_handle(
        session, ConformerGroup, ref, kind_label="conformer_group", resolver=resolve_conformer_group_handle
    )


def _admin_policy(policy: ThermoSelectionPolicy) -> SelectionPolicy:
    if policy is ThermoSelectionPolicy.method_preferred:
        return SelectionPolicy.default
    return SelectionPolicy(policy.value)


def run_selection(
    session: Session, *, species_entry_id: int, body: ThermoSelectionRequest
) -> tuple[H298Selection, str | None]:
    """Run the service for a public request. Returns the selection and the conformer-group ref, if any.

    ``method_preferred`` applies the shipped registry. The administrative policies apply no rule at
    all: they order by review status and recency exactly as the browse endpoints do, and say so.
    The population cap is the service's fixed 500; it is not a request field.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is
    not one (``get_db``'s, say, if a route were wired to it by mistake) raises ``SnapshotNotConsistentError`` instead
    of quietly answering under READ COMMITTED. Only a session that carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is
    read as it is; that exists for the test harness, which holds one outer transaction that cannot become a
    snapshot, and nothing in the application sets it.
    """
    group_id = resolve_group_ref(session, body.target.conformer_group_ref) if body.target.conformer_group_ref else None
    request = H298Request(
        target_kind=body.target.kind,
        conformer_group_id=group_id,
        min_review_status=body.min_review_status,
        admin_policy=_admin_policy(body.policy),
    )
    rules = None if body.policy is ThermoSelectionPolicy.method_preferred else ()
    selection = select_h298(
        session,
        species_entry_id=species_entry_id,
        request=request,
        rules=rules,
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )
    if selection.outcome is Outcome.bounded_search_exceeded:
        # The service counted only records at or above the effective floor, so this number is about the
        # caller's visible population and says nothing about records the profile hides.
        visible = selection.manifest["population"]["visible_candidates"]
        raise CodedValueError(
            W_THERMO_SELECTION_POPULATION_TOO_LARGE,
            f"{visible} visible thermo records exceed the limit of {MAX_CANDIDATES} for one selection; "
            "nothing was assessed. Narrow the population with min_review_status or the curated profile.",
            context={"limit": MAX_CANDIDATES, "visible_candidates": visible},
        )
    return selection, body.target.conformer_group_ref


def profile_has_floor() -> bool:
    return current_read_profile().review_floor is not None


def redact_manifest(manifest: dict[str, Any], *, withhold_excluded: bool) -> dict[str, Any]:
    """A copy of the manifest safe to hand to this caller. Never alters ``decision`` or ``candidates``."""
    out = copy.deepcopy(manifest)
    # The candidate cap is the server's, not a field of the request; it is not part of what is downloaded.
    out["request"].pop("max_candidates", None)
    # The read profile the manifest was made under, as every scientific response echoes it. Replay reads
    # only the administrative policy, so these keys cannot change a decision.
    out["request"].update(current_read_profile().echo())
    if withhold_excluded:
        population = out["population"]
        population["thermo_rows_for_entry"] = population["visible_candidates"]
        population["excluded_by_review"] = []
        population["excluded_by_review_withheld"] = True
    else:
        out["population"]["excluded_by_review_withheld"] = False
    return out


def _effective_floor(manifest: dict[str, Any], fallback: RecordReviewStatus | None) -> tuple[RecordReviewStatus, list[RecordReviewStatus]]:
    statuses = sorted(
        (RecordReviewStatus(v) for v in manifest["request"]["effective_review_statuses"]),
        key=lambda s: REVIEW_RANK[s],
    )
    floor = statuses[-1] if statuses else (fallback or RecordReviewStatus.approved)
    return floor, statuses


def _pick(manifest: dict[str, Any], mode: ThermoSelectionMode) -> ThermoSelectionPick | None:
    decision = manifest["decision"]
    if decision is None:
        return None
    outcome = decision["outcome"]
    if outcome in (Outcome.policy_preferred.value, Outcome.sole_eligible_candidate.value):
        return ThermoSelectionPick(
            thermo_ref=decision["selected_ref"], basis=outcome, administrative=False, explanation=decision["basis"]
        )
    first = decision["administrative_first"]
    if outcome == Outcome.incomparable_alternatives.value and mode is ThermoSelectionMode.first and first:
        return ThermoSelectionPick(
            thermo_ref=first["thermo_ref"], basis="administrative_first", administrative=True,
            explanation=first["basis"],
        )
    return None


def build_response(
    selection: H298Selection,
    *,
    body: ThermoSelectionRequest,
    species_entry_ref: str,
    manifest: dict[str, Any],
) -> ThermoSelectionResponse:
    """The typed response, built from the (already redacted) manifest. Public refs only."""
    decision = manifest["decision"]
    floor, statuses = _effective_floor(manifest, body.min_review_status)
    population = manifest["population"]
    disclosures = manifest.get("disclosures", {})
    candidates = [
        ThermoSelectionCandidate(
            thermo_ref=c["thermo_ref"],
            review_status=c["review_status"],
            created_at=c["created_at"],
            scientific_origin=c["scientific_origin"],
            phase=c["phase"],
            enthalpy_reference_kind=c["enthalpy_reference_kind"],
            target_kind=c["target_kind"],
            target_group_ref=c["target_group_ref"],
            protocol_state=c["protocol_state"],
            protocol=c["protocol"],
            linked_recipe_keys=c["linked_recipe_keys"],
            applicability=c["assessment"]["applicability"],
            reasons=[ThermoSelectionReason(**r) for r in c["assessment"]["reasons"]],
            answer_representation=c["assessment"]["answer_representation"],
            value_kj_mol=c["assessment"]["value_kj_mol"],
            representations=c["assessment"]["representations"],
            blocking=c["assessment"]["blocking"],
            advisory=c["assessment"]["advisory"],
            eligible=c["eligible"],
        )
        for c in manifest["candidates"]
    ]
    relations = ThermoSelectionRelations()
    if decision is not None:
        relations = ThermoSelectionRelations(
            edges=[
                ThermoSelectionEdge(**e) for e in decision["edges"]
            ],
            overridden_edges=decision["overridden_edges"],
            opposing_pairs=decision["opposing_pairs"],
            cycles=decision["cycles"],
        )
    target = manifest["request"]["target"]
    return ThermoSelectionResponse(
        request=ThermoSelectionRequestEcho(
            species_entry_ref=species_entry_ref,
            quantity=QUANTITY_H298,
            temperature_k=REFERENCE_TEMPERATURE_K,
            phase="gas",
            target=ThermoSelectionTargetEcho(
                kind=ThermoTargetKind(target["kind"]), conformer_group_ref=target["conformer_group_ref"]
            ),
            policy=body.policy,
            result_mode=body.result_mode,
            min_review_status=body.min_review_status,
        ),
        review=ThermoSelectionReview(effective_floor=floor, effective_statuses=statuses),
        policy=ThermoSelectionPolicyInfo(
            name=manifest["policy"]["name"],
            version=manifest["policy"]["version"],
            rules=[] if decision is None else decision["rules"],
        ),
        outcome=ThermoSelectionOutcome(manifest["outcome"]),
        basis=_basis(selection, decision),
        selection=_pick(manifest, body.result_mode),
        fronts=[] if decision is None else decision["fronts"],
        administrative_order=[] if decision is None else decision["administrative_order"],
        candidates=candidates,
        relations=relations,
        rule_matches=[] if decision is None else decision["rule_matches"],
        disclosures=ThermoSelectionDisclosures(
            unresolved_refs=disclosures.get("unresolved_refs", []),
            unsupported_refs=disclosures.get("unsupported_refs", []),
            visible_candidates=population["visible_candidates"],
            excluded_by_review=[ThermoSelectionExcluded(**e) for e in population["excluded_by_review"]],
            excluded_by_review_withheld=population["excluded_by_review_withheld"],
            notes=list(selection.notes),
        ),
    )


def _basis(selection: H298Selection, decision: dict[str, Any] | None) -> str:
    if decision is not None:
        return decision["basis"]
    return selection.notes[0] if selection.notes else "the search was bounded"


__all__ = [
    "SNAPSHOT_OPT_OUT",
    "build_response",
    "profile_has_floor",
    "redact_manifest",
    "resolve_group_ref",
    "resolve_species_entry_ref",
    "run_selection",
]
