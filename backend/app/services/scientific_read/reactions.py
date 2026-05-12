"""Service implementation for /api/v1/scientific/reactions/search.

See docs/specs/read_api_mvp.md §Endpoint 2.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    ReactionRole,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionState
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.schemas.reads.scientific_reactions import (
    ReactionAvailability,
    ReactionDirectionQuery,
    ReactionParticipantSummary,
    ReactionScientificRecord,
    ReactionSearchRequest,
    RequestEcho,
    ScientificReactionSearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.handles import (
    NO_MATCH,
    reconcile_reaction_entry_pair,
    reconcile_reaction_pair,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "kinetics",
    "transition_states",
    "species",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

_DEFAULT_SORT_ECHO = "review_rank,has_kinetics,has_transition_state,created_at,id"


def search_reactions(
    session: Session, request: ReactionSearchRequest
) -> ScientificReactionSearchResponse:
    """Discover reaction entries by reactants/products with availability + trust.

    Direction matching is query-time semantics — the schema does not store a
    per-entry direction. ``either`` matches in either orientation; ``forward``
    requires query reactants → stored reactants and query products → stored
    products; ``reverse`` requires the swap. ``exact`` is **not** supported in
    v0 (rejected with 422 ``unsupported_direction``).

    Default sort (per L3): ``review_rank ASC, has_kinetics DESC,
    has_transition_state DESC, created_at DESC, id DESC``. Client-supplied
    ``sort=`` is rejected.

    :param session: SQLAlchemy session.
    :param request: Parsed request model.
    :returns: ``ScientificReactionSearchResponse`` Pydantic model.
    :raises ValueError: 422 for sort/include/pagination/direction validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/reactions/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # Phase C: explicit reaction/reaction_entry refs are valid identifier
    # sources. Resolve them first so we can short-circuit (NO_MATCH) and
    # so we know whether the caller supplied any identifier at all.
    reaction_pair = reconcile_reaction_pair(
        session, id_value=None, ref_value=request.reaction_ref
    )
    reaction_entry_pair = reconcile_reaction_entry_pair(
        session, id_value=None, ref_value=request.reaction_entry_ref
    )
    if reaction_pair is NO_MATCH or reaction_entry_pair is NO_MATCH:
        return _empty_response(request, includes, offset, limit)
    reaction_ref_id: int | None = reaction_pair  # type: ignore[assignment]
    reaction_entry_ref_id: int | None = reaction_entry_pair  # type: ignore[assignment]

    has_participant_identifier = bool(request.reactants or request.products)
    if (
        not has_participant_identifier
        and reaction_ref_id is None
        and reaction_entry_ref_id is None
    ):
        # F6: a request that supplies neither a chemistry filter nor an
        # explicit ref is the anonymous-enumeration shape. Reject with
        # a stable code so clients can branch on it.
        raise ValueError(
            "missing_reaction_search_filter: at least one of "
            "{reactants, products, reaction_ref, reaction_entry_ref} "
            "is required to scope a reaction search."
        )

    # Resolve reactant / product SMILES to species IDs.
    reactant_species_ids = _resolve_smiles_to_species_ids(
        session, request.reactants
    )
    product_species_ids = _resolve_smiles_to_species_ids(
        session, request.products
    )

    if request.reactants and len(reactant_species_ids) != len(request.reactants):
        return _empty_response(request, includes, offset, limit)
    if request.products and len(product_species_ids) != len(request.products):
        return _empty_response(request, includes, offset, limit)

    if has_participant_identifier:
        # Find reaction entries whose participants match the requested set
        # in the appropriate orientation(s).
        candidate_entry_ids = _find_matching_reaction_entry_ids(
            session,
            reactant_species_ids=reactant_species_ids,
            product_species_ids=product_species_ids,
            direction=request.direction,
        )
        # Phase C: narrow the participant-derived candidate set with
        # the explicit ref filters in SQL, so we still pay only one
        # round-trip.
        if reaction_entry_ref_id is not None:
            candidate_entry_ids = [
                eid for eid in candidate_entry_ids if eid == reaction_entry_ref_id
            ]
        if reaction_ref_id is not None and candidate_entry_ids:
            keep = set(
                session.scalars(
                    select(ReactionEntry.id).where(
                        ReactionEntry.id.in_(candidate_entry_ids),
                        ReactionEntry.reaction_id == reaction_ref_id,
                    )
                ).all()
            )
            candidate_entry_ids = [eid for eid in candidate_entry_ids if eid in keep]
    else:
        # F6: no participant filter → build the candidate set from the
        # explicit ref filters using SQL ``WHERE`` rather than scanning
        # every reaction_entry id into memory.
        ref_filters = []
        if reaction_entry_ref_id is not None:
            ref_filters.append(ReactionEntry.id == reaction_entry_ref_id)
        if reaction_ref_id is not None:
            ref_filters.append(ReactionEntry.reaction_id == reaction_ref_id)
        # ``ref_filters`` is non-empty here because the earlier
        # missing_reaction_search_filter check guarantees at least one
        # of these refs is set when no participant filter is supplied.
        candidate_entry_ids = list(
            session.scalars(select(ReactionEntry.id).where(*ref_filters)).all()
        )

    if not candidate_entry_ids:
        return _empty_response(request, includes, offset, limit)

    # Bulk-load reaction entries with their parent ChemReaction.
    entries = session.scalars(
        select(ReactionEntry).where(ReactionEntry.id.in_(candidate_entry_ids))
    ).all()
    chem_reactions = {
        cr.id: cr
        for cr in session.scalars(
            select(ChemReaction).where(
                ChemReaction.id.in_({e.reaction_id for e in entries})
            )
        ).all()
    }

    # Filter by family if requested.
    if request.family is not None:
        family = session.scalar(
            select(ReactionFamily).where(ReactionFamily.name == request.family)
        )
        if family is None:
            return _empty_response(request, includes, offset, limit)
        entries = [
            e
            for e in entries
            if chem_reactions[e.reaction_id].reaction_family_id == family.id
        ]

    # Bulk fetch review badges for the entries.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.reaction_entry,
        record_ids=[e.id for e in entries],
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    entries = [e for e in entries if badges[e.id].status in visible]

    if not entries:
        return _empty_response(request, includes, offset, limit)

    # Bulk fetch participants and availability for surviving entries.
    participants_by_entry = _load_participants(session, [e.id for e in entries])
    species_by_entry = _entry_species_ids(session, [e.id for e in entries])
    all_participant_species_entry_ids = {
        se
        for participants in participants_by_entry.values()
        for se, _, _ in participants
    }
    smiles_by_entry_species = _resolve_participant_smiles(
        session, all_participant_species_entry_ids
    )
    refs_by_entry_species = _resolve_participant_refs(
        session, all_participant_species_entry_ids
    )
    availability_by_entry = _compute_availability(session, [e.id for e in entries])

    # Determine matched_direction for each entry.
    matched_direction_by_entry: dict[int, ReactionDirectionQuery] = {}
    for e in entries:
        matched_direction_by_entry[e.id] = _matched_direction(
            entry_species=species_by_entry[e.id],
            reactant_species_ids=reactant_species_ids,
            product_species_ids=product_species_ids,
            requested=request.direction,
        )

    family_name_by_id: dict[int, str] = {}
    family_ids = {
        chem_reactions[e.reaction_id].reaction_family_id
        for e in entries
        if chem_reactions[e.reaction_id].reaction_family_id is not None
    }
    if family_ids:
        family_name_by_id = {
            f.id: f.name
            for f in session.scalars(
                select(ReactionFamily).where(ReactionFamily.id.in_(family_ids))
            ).all()
        }

    records: list[ReactionScientificRecord] = []
    for e in entries:
        chem = chem_reactions[e.reaction_id]
        participants = participants_by_entry[e.id]
        reactants = [
            ReactionParticipantSummary(
                species_entry_id=se,
                species_entry_ref=refs_by_entry_species.get(se, ""),
                smiles=smiles_by_entry_species.get(se, ""),
                participant_index=idx,
            )
            for se, role, idx in sorted(participants, key=lambda p: (p[1].value, p[2]))
            if role == ReactionRole.reactant
        ]
        products = [
            ReactionParticipantSummary(
                species_entry_id=se,
                species_entry_ref=refs_by_entry_species.get(se, ""),
                smiles=smiles_by_entry_species.get(se, ""),
                participant_index=idx,
            )
            for se, role, idx in sorted(participants, key=lambda p: (p[1].value, p[2]))
            if role == ReactionRole.product
        ]
        equation = _format_equation(reactants, products, chem.reversible)
        records.append(
            ReactionScientificRecord(
                reaction_id=chem.id,
                reaction_ref=chem.public_ref,
                reaction_entry_id=e.id,
                reaction_entry_ref=e.public_ref,
                equation=equation,
                matched_direction=matched_direction_by_entry[e.id],
                reversible=chem.reversible,
                family=(
                    family_name_by_id.get(chem.reaction_family_id)
                    if chem.reaction_family_id is not None
                    else None
                ),
                review=badges[e.id],
                reactants=reactants,
                products=products,
                availability=availability_by_entry[e.id],
            )
        )

    summary = review_summary(badges[e.id] for e in entries)

    # Sort: review_rank ASC, has_kinetics DESC, has_transition_state DESC, created_at DESC, id DESC
    entry_created_at = {e.id: e.created_at for e in entries}

    def sort_key(rec: ReactionScientificRecord) -> tuple:
        return (
            REVIEW_RANK[rec.review.status],
            -int(rec.availability.has_kinetics),
            -int(rec.availability.has_transition_state),
            -entry_created_at[rec.reaction_entry_id].timestamp(),
            -rec.reaction_entry_id,
        )

    records.sort(key=sort_key)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned_records = records[:1]
    else:
        returned_records = records[offset : offset + limit]

    return ScientificReactionSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned_records,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned_records),
            total=pre_collapse_total,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_smiles_to_species_ids(
    session: Session, smiles_list: list[str]
) -> list[int]:
    if not smiles_list:
        return []
    rows = session.execute(
        select(Species.smiles, Species.id).where(Species.smiles.in_(smiles_list))
    ).all()
    by_smiles: dict[str, int] = {row.smiles: row.id for row in rows}
    return [by_smiles[s] for s in smiles_list if s in by_smiles]


def _find_matching_reaction_entry_ids(
    session: Session,
    *,
    reactant_species_ids: list[int],
    product_species_ids: list[int],
    direction: ReactionDirectionQuery,
) -> list[int]:
    """Find reaction_entry IDs whose structure participants match the query.

    Match semantics use multisets (counts) of species_id per role. ``exact``
    direction is rejected upstream of this helper.
    """
    if direction == ReactionDirectionQuery.forward:
        orientations = [(reactant_species_ids, product_species_ids)]
    elif direction == ReactionDirectionQuery.reverse:
        orientations = [(product_species_ids, reactant_species_ids)]
    elif direction == ReactionDirectionQuery.either:
        orientations = [
            (reactant_species_ids, product_species_ids),
            (product_species_ids, reactant_species_ids),
        ]
    else:  # pragma: no cover — Pydantic validation prevents this
        raise ValueError(
            "unsupported_direction: direction=exact is not supported in v0."
        )

    # Pull all participants for entries that touch any of the supplied
    # species ids; then test the multiset-match in Python. For v0 dataset
    # sizes this is acceptable; scaling to large indices should add a
    # canonical participant-hash column.
    all_relevant_species = set(reactant_species_ids) | set(product_species_ids)
    if not all_relevant_species:
        return []

    candidate_entries = session.scalars(
        select(ReactionEntryStructureParticipant.reaction_entry_id)
        .join(SpeciesEntry, SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id)
        .where(SpeciesEntry.species_id.in_(all_relevant_species))
        .distinct()
    ).all()
    if not candidate_entries:
        return []

    # Load participants for candidates, joined to species_id.
    rows = session.execute(
        select(
            ReactionEntryStructureParticipant.reaction_entry_id,
            ReactionEntryStructureParticipant.role,
            SpeciesEntry.species_id,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id.in_(candidate_entries)
        )
    ).all()

    grouped: dict[int, dict[ReactionRole, list[int]]] = defaultdict(
        lambda: {ReactionRole.reactant: [], ReactionRole.product: []}
    )
    for entry_id, role, species_id in rows:
        grouped[entry_id][role].append(species_id)

    matching: list[int] = []
    for entry_id, sides in grouped.items():
        for left, right in orientations:
            if sorted(sides[ReactionRole.reactant]) == sorted(left) and sorted(
                sides[ReactionRole.product]
            ) == sorted(right):
                matching.append(entry_id)
                break
    return matching


def _load_participants(
    session: Session, entry_ids: list[int]
) -> dict[int, list[tuple[int, ReactionRole, int]]]:
    """Return per-entry list of (species_entry_id, role, participant_index) tuples."""
    rows = session.execute(
        select(
            ReactionEntryStructureParticipant.reaction_entry_id,
            ReactionEntryStructureParticipant.species_entry_id,
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
        ).where(
            ReactionEntryStructureParticipant.reaction_entry_id.in_(entry_ids)
        )
    ).all()
    grouped: dict[int, list[tuple[int, ReactionRole, int]]] = {
        eid: [] for eid in entry_ids
    }
    for entry_id, species_entry_id, role, idx in rows:
        grouped[entry_id].append((species_entry_id, role, idx))
    return grouped


def _resolve_participant_smiles(
    session: Session, species_entry_ids: set[int]
) -> dict[int, str]:
    """Map species_entry_id → canonical SMILES via the parent Species row."""
    if not species_entry_ids:
        return {}
    rows = session.execute(
        select(SpeciesEntry.id, Species.smiles)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id.in_(species_entry_ids))
    ).all()
    return {se_id: smiles for se_id, smiles in rows}


def _resolve_participant_refs(
    session: Session, species_entry_ids: set[int]
) -> dict[int, str]:
    """Map species_entry_id → public_ref (Phase B)."""
    if not species_entry_ids:
        return {}
    rows = session.execute(
        select(SpeciesEntry.id, SpeciesEntry.public_ref).where(
            SpeciesEntry.id.in_(species_entry_ids)
        )
    ).all()
    return {se_id: ref for se_id, ref in rows}


def _compute_availability(
    session: Session, entry_ids: list[int]
) -> dict[int, ReactionAvailability]:
    if not entry_ids:
        return {}

    kinetics_count_by_entry: dict[int, int] = {eid: 0 for eid in entry_ids}
    for entry_id, count in session.execute(
        select(Kinetics.reaction_entry_id, func.count(Kinetics.id))
        .where(Kinetics.reaction_entry_id.in_(entry_ids))
        .group_by(Kinetics.reaction_entry_id)
    ).all():
        kinetics_count_by_entry[entry_id] = count

    has_ts_set = set(
        session.scalars(
            select(TransitionState.reaction_entry_id)
            .where(TransitionState.reaction_entry_id.in_(entry_ids))
            .distinct()
        ).all()
    )

    # has_path_search: any calculation of type path_search reachable via a
    # transition state attached to this reaction entry.
    has_path_search_set: set[int] = set()
    if has_ts_set:
        from app.db.models.transition_state import TransitionStateEntry

        rows = session.execute(
            select(TransitionState.reaction_entry_id)
            .join(TransitionStateEntry, TransitionStateEntry.transition_state_id == TransitionState.id)
            .join(Calculation, Calculation.transition_state_entry_id == TransitionStateEntry.id)
            .where(
                TransitionState.reaction_entry_id.in_(entry_ids),
                Calculation.type == CalculationType.path_search,
            )
            .distinct()
        ).all()
        has_path_search_set = {row[0] for row in rows}

    return {
        eid: ReactionAvailability(
            has_kinetics=kinetics_count_by_entry.get(eid, 0) > 0,
            has_transition_state=eid in has_ts_set,
            has_path_search=eid in has_path_search_set,
            kinetics_count=kinetics_count_by_entry.get(eid, 0),
        )
        for eid in entry_ids
    }


def _matched_direction(
    *,
    entry_species: dict[ReactionRole, list[int]],
    reactant_species_ids: list[int],
    product_species_ids: list[int],
    requested: ReactionDirectionQuery,
) -> ReactionDirectionQuery:
    """Determine which orientation of the entry the query actually matched.

    For ``forward`` and ``reverse`` the requested orientation is the matched
    orientation. For ``either``, prefer ``forward`` when query reactants line
    up with stored reactants; otherwise ``reverse``.
    """
    if requested == ReactionDirectionQuery.forward:
        return ReactionDirectionQuery.forward
    if requested == ReactionDirectionQuery.reverse:
        return ReactionDirectionQuery.reverse

    stored_reactants = sorted(entry_species[ReactionRole.reactant])
    stored_products = sorted(entry_species[ReactionRole.product])
    if (
        sorted(reactant_species_ids) == stored_reactants
        and sorted(product_species_ids) == stored_products
    ):
        return ReactionDirectionQuery.forward
    return ReactionDirectionQuery.reverse


def _entry_species_ids(
    session: Session, entry_ids: list[int]
) -> dict[int, dict[ReactionRole, list[int]]]:
    """Per-entry species_id lists keyed by role."""
    if not entry_ids:
        return {}
    rows = session.execute(
        select(
            ReactionEntryStructureParticipant.reaction_entry_id,
            ReactionEntryStructureParticipant.role,
            SpeciesEntry.species_id,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id.in_(entry_ids)
        )
    ).all()
    grouped: dict[int, dict[ReactionRole, list[int]]] = {
        eid: {ReactionRole.reactant: [], ReactionRole.product: []}
        for eid in entry_ids
    }
    for entry_id, role, species_id in rows:
        grouped[entry_id][role].append(species_id)
    return grouped


def _format_equation(
    reactants: list[ReactionParticipantSummary],
    products: list[ReactionParticipantSummary],
    reversible: bool,
) -> str:
    arrow = "<=>" if reversible else "->"
    left = " + ".join(p.smiles for p in reactants)
    right = " + ".join(p.smiles for p in products)
    return f"{left} {arrow} {right}"


def _filter_echo(request: ReactionSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.reactants:
        echo["reactants"] = list(request.reactants)
    if request.products:
        echo["products"] = list(request.products)
    echo["direction"] = request.direction.value
    if request.family is not None:
        echo["family"] = request.family
    if request.reaction_ref is not None:
        echo["reaction_ref"] = request.reaction_ref
    if request.reaction_entry_ref is not None:
        echo["reaction_entry_ref"] = request.reaction_entry_ref
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: ReactionSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificReactionSearchResponse:
    return ScientificReactionSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
