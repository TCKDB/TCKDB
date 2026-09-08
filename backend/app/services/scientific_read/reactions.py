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
    ReactionParticipant,
)
from app.db.models.reaction_atom_map import ReactionAtomMap
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionState
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CollapseMode,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_reactions import (
    ReactionAvailability,
    ReactionDirectionQuery,
    ReactionMatchMode,
    ReactionParticipantSummary,
    ReactionsBrowseRequest,
    ReactionScientificRecord,
    ReactionSearchRequest,
    RequestEcho,
    ScientificReactionSearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    molecular_formula_expr,
    reject_client_sort,
    review_summary,
    slice_for_pagination,
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
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
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
    products; ``reverse`` requires the swap. ``direction=exact`` is **not**
    supported in v0 (rejected with 422 ``unsupported_direction``).

    ``match`` is the orthogonal axis and defaults to ``contains``: every
    queried species must appear in that role, and a side the caller left
    empty constrains nothing. So ``reactants=NN`` answers "what consumes
    hydrazine?" rather than "what turns hydrazine into nothing?" — the
    latter has no answer and used to be the question this endpoint asked.
    ``match=exact`` restores multiset equality on both sides for callers who
    want precisely one equation and do not hold its ``reaction_ref``.
    Containment is set-based, not multiset; see
    :class:`~app.schemas.reads.scientific_reactions.ReactionMatchMode`.

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
            match=request.match,
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
    formulas_by_entry_species = _resolve_participant_formulas(
        session, all_participant_species_entry_ids
    )
    refs_by_entry_species = _resolve_participant_refs(
        session, all_participant_species_entry_ids
    )
    labels_by_entry_species = _resolve_participant_labels(
        session, all_participant_species_entry_ids
    )
    species_id_by_entry_species = _resolve_participant_species_ids(
        session, all_participant_species_entry_ids
    )
    stoichiometry_by_key = _resolve_reaction_participant_stoichiometry(
        session, {chem_reactions[e.reaction_id].id for e in entries}
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
            match=request.match,
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

    records = _materialize_and_sort_reaction_records(
        entries=entries,
        chem_reactions=chem_reactions,
        participants_by_entry=participants_by_entry,
        refs_by_entry_species=refs_by_entry_species,
        labels_by_entry_species=labels_by_entry_species,
        smiles_by_entry_species=smiles_by_entry_species,
        formulas_by_entry_species=formulas_by_entry_species,
        species_id_by_entry_species=species_id_by_entry_species,
        stoichiometry_by_key=stoichiometry_by_key,
        badges=badges,
        availability_by_entry=availability_by_entry,
        matched_direction_by_entry=matched_direction_by_entry,
        family_name_by_id=family_name_by_id,
    )

    summary = review_summary(badges[e.id] for e in entries)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    returned_records = slice_for_pagination(
        records,
        offset=offset,
        limit=limit,
        collapse_first=collapse_first,
    )

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
            collapse_first=collapse_first,
        ),
    )


def browse_reactions(
    session: Session, request: ReactionsBrowseRequest
) -> ScientificReactionSearchResponse:
    """List reaction entries with no filter required, for a catalogue page.

    See ``/scientific/reactions/browse``. This is the identifier-free
    catalogue read :func:`search_reactions` deliberately cannot serve:
    that function's ``missing_reaction_search_filter`` 422 keeps an
    accidental unbounded scan off a route whose other callers rely on it
    staying an exact/participant lookup. Relaxing that guard in place
    would make one route mean two different things depending on which
    query parameters happened to be present, so this is a sibling
    function and route instead -- the same relationship
    ``browse_transition_states`` has to ``search_transition_states``.

    Downstream of "which candidate reaction entries" this shares every
    helper with :func:`search_reactions` verbatim: SMILES resolution
    (:func:`_resolve_smiles_to_species_ids`), participant matching
    (:func:`_find_matching_reaction_entry_ids`), the bulk
    participant/availability loaders, and
    :func:`_materialize_and_sort_reaction_records` -- so a browse record
    and a search record are byte-identical in shape
    (:class:`ReactionScientificRecord`). ``has_kinetics`` /
    ``has_transition_state`` are query-time projections rather than
    stored columns, so they narrow the candidate set in Python once
    availability has been bulk-computed, the same way ``family`` narrows
    once ``ChemReaction`` rows are loaded.

    With neither ``reactant_smiles`` nor ``product_smiles`` supplied, the
    candidate set is every ``reaction_entry`` row in the corpus -- the gap
    this endpoint exists to close (``reactions/search`` has no unfiltered
    form).

    ``reactant_smiles`` / ``product_smiles`` are called with
    ``direction=ReactionDirectionQuery.either`` below (unconditionally, not
    caller-selectable), so despite their names neither list is restricted
    to the stored role it names -- but each list is still matched as
    **one group, against a single stored side, in a single orientation**,
    not species-by-species: a ``reactant_smiles`` group is tried whole
    against the stored reactants (forward) and whole against the stored
    products (reverse), which is exactly why a matched record can come
    back with ``matched_direction: "reverse"`` -- but a group mixing a
    genuine reactant with a genuine product of the same reaction matches
    in neither orientation, because no single stored side contains both.
    The parameter names describe which query bucket a SMILES was placed
    in, not which side of the stored equation it is individually
    guaranteed to land on.

    Partial resolution is a hard empty, not a degraded match: if any
    SMILES in either list fails to resolve to a species TCKDB has on
    file, the response is ``[]``. Silently matching only the SMILES that
    *did* resolve would answer a query the caller did not ask (fewer
    required species than they supplied) and serve it as if it were the
    one they did -- a wrong answer presented as a right one. Concretely:
    :func:`_resolve_smiles_to_species_ids` *preserves* duplicates and
    ordering -- it drops only the occurrences that fail to resolve, so
    its output is index/count-aligned with the raw request list -- which
    is what lets the length check below compare
    ``len(reactant_species_ids)`` directly against
    ``len(request.reactant_smiles)`` (no de-duplication needed first) and
    have the mismatch mean exactly "some requested SMILES occurrence did
    not resolve", with or without duplicates in the query. This mirrors
    the identical check :func:`search_reactions` already makes against
    ``request.reactants`` / ``request.products``.

    :param session: SQLAlchemy session.
    :param request: Parsed request model.
    :returns: ``ScientificReactionSearchResponse`` — same envelope shape
        ``search_reactions`` returns.
    :raises ValueError: 422 for pagination validation failures.
    """
    offset, limit = validate_pagination(request.offset, request.limit)

    reactant_species_ids = _resolve_smiles_to_species_ids(
        session, request.reactant_smiles
    )
    product_species_ids = _resolve_smiles_to_species_ids(
        session, request.product_smiles
    )
    # Correctness trap: with a LIST, "some ids resolved" is not "all ids
    # resolved". A reaction_entry can only ever contain species TCKDB has,
    # so any unresolved SMILES on a side makes that side unsatisfiable --
    # the whole response must be empty, not narrowed to the SMILES that did
    # resolve (which would silently drop a caller-required species from an
    # AND query and return a wrong answer that looks like a right one).
    # No de-duplication is needed first: `_resolve_smiles_to_species_ids`
    # preserves duplicates and skips only unresolved occurrences, so its
    # output length compared against the raw request list's length is
    # already exactly "did every occurrence resolve" -- the same check
    # `search_reactions` makes against `request.reactants` / `.products`.
    if request.reactant_smiles and len(reactant_species_ids) != len(
        request.reactant_smiles
    ):
        return _empty_browse_response(request, offset, limit)
    if request.product_smiles and len(product_species_ids) != len(
        request.product_smiles
    ):
        return _empty_browse_response(request, offset, limit)

    if reactant_species_ids or product_species_ids:
        candidate_entry_ids = _find_matching_reaction_entry_ids(
            session,
            reactant_species_ids=reactant_species_ids,
            product_species_ids=product_species_ids,
            direction=ReactionDirectionQuery.either,
            match=ReactionMatchMode.contains,
        )
    else:
        # No structure filter at all: every reaction_entry row is a
        # candidate -- the unfiltered listing ``reactions/search`` cannot
        # serve.
        candidate_entry_ids = list(
            session.scalars(select(ReactionEntry.id)).all()
        )

    if not candidate_entry_ids:
        return _empty_browse_response(request, offset, limit)

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

    if request.family is not None:
        family = session.scalar(
            select(ReactionFamily).where(ReactionFamily.name == request.family)
        )
        if family is None:
            return _empty_browse_response(request, offset, limit)
        entries = [
            e
            for e in entries
            if chem_reactions[e.reaction_id].reaction_family_id == family.id
        ]
        if not entries:
            return _empty_browse_response(request, offset, limit)

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
        return _empty_browse_response(request, offset, limit)

    availability_by_entry = _compute_availability(session, [e.id for e in entries])
    if request.has_kinetics is not None:
        entries = [
            e
            for e in entries
            if availability_by_entry[e.id].has_kinetics == request.has_kinetics
        ]
    if request.has_transition_state is not None:
        entries = [
            e
            for e in entries
            if availability_by_entry[e.id].has_transition_state
            == request.has_transition_state
        ]
    if not entries:
        return _empty_browse_response(request, offset, limit)

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
    formulas_by_entry_species = _resolve_participant_formulas(
        session, all_participant_species_entry_ids
    )
    refs_by_entry_species = _resolve_participant_refs(
        session, all_participant_species_entry_ids
    )
    labels_by_entry_species = _resolve_participant_labels(
        session, all_participant_species_entry_ids
    )
    species_id_by_entry_species = _resolve_participant_species_ids(
        session, all_participant_species_entry_ids
    )
    stoichiometry_by_key = _resolve_reaction_participant_stoichiometry(
        session, {chem_reactions[e.reaction_id].id for e in entries}
    )

    matched_direction_by_entry: dict[int, ReactionDirectionQuery] = {}
    for e in entries:
        matched_direction_by_entry[e.id] = _matched_direction(
            entry_species=species_by_entry[e.id],
            reactant_species_ids=reactant_species_ids,
            product_species_ids=product_species_ids,
            requested=ReactionDirectionQuery.either,
            match=ReactionMatchMode.contains,
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

    records = _materialize_and_sort_reaction_records(
        entries=entries,
        chem_reactions=chem_reactions,
        participants_by_entry=participants_by_entry,
        refs_by_entry_species=refs_by_entry_species,
        labels_by_entry_species=labels_by_entry_species,
        smiles_by_entry_species=smiles_by_entry_species,
        formulas_by_entry_species=formulas_by_entry_species,
        species_id_by_entry_species=species_id_by_entry_species,
        stoichiometry_by_key=stoichiometry_by_key,
        badges=badges,
        availability_by_entry=availability_by_entry,
        matched_direction_by_entry=matched_direction_by_entry,
        family_name_by_id=family_name_by_id,
    )

    summary = review_summary(badges[e.id] for e in entries)

    total = len(records)
    page_records = slice_for_pagination(
        records, offset=offset, limit=limit, collapse_first=False
    )

    return ScientificReactionSearchResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=CollapseMode.all,
            include=[],
        ),
        review_summary=summary,
        records=page_records,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(page_records),
            total=total,
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


def _side_matches(
    stored: list[int], queried: list[int], match: ReactionMatchMode
) -> bool:
    """Test one role (reactants or products) of one orientation.

    ``exact`` is multiset equality — same species, same counts. ``contains``
    is set containment: every queried species appears in the stored side,
    counts ignored. An empty ``queried`` is contained in anything, which is
    what makes a one-sided ``contains`` query leave the other side free.
    """
    if match is ReactionMatchMode.exact:
        return sorted(stored) == sorted(queried)
    return set(queried).issubset(stored)


def _orientation_matches(
    *,
    stored_reactants: list[int],
    stored_products: list[int],
    queried_reactants: list[int],
    queried_products: list[int],
    match: ReactionMatchMode,
) -> bool:
    """Test both roles of a single orientation under ``match`` semantics."""
    return _side_matches(stored_reactants, queried_reactants, match) and _side_matches(
        stored_products, queried_products, match
    )


def _find_matching_reaction_entry_ids(
    session: Session,
    *,
    reactant_species_ids: list[int],
    product_species_ids: list[int],
    direction: ReactionDirectionQuery,
    match: ReactionMatchMode,
) -> list[int]:
    """Find reaction_entry IDs whose structure participants match the query.

    Two independent axes, and they compose:

    * ``direction`` picks which orientation(s) of the stored entry the query
      is tried against — ``forward``, ``reverse``, or (default) ``either``.
    * ``match`` picks how a side is compared once an orientation is chosen —
      ``contains`` (set containment per role, the default) or ``exact``
      (multiset equality on both roles).

    Under ``either`` + ``contains``, containment is tested in *both*
    orientations and an entry matching in either one is returned.

    ``direction=exact`` is rejected upstream of this helper; it is not the
    same thing as ``match=exact``, which is what "precisely this equation"
    now means.
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
            if _orientation_matches(
                stored_reactants=sides[ReactionRole.reactant],
                stored_products=sides[ReactionRole.product],
                queried_reactants=left,
                queried_products=right,
                match=match,
            ):
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
    return dict(rows)


def _resolve_participant_formulas(
    session: Session, species_entry_ids: set[int]
) -> dict[int, str | None]:
    """Map species_entry_id -> Hill-notation formula (RDKit cartridge).

    Mirrors ``ReactionFullSpeciesParticipant.formula`` -- same expression
    (:func:`app.services.scientific_read.common.molecular_formula_expr`),
    so a search row and a ``/full`` participant row agree.
    """
    if not species_entry_ids:
        return {}
    rows = session.execute(
        select(SpeciesEntry.id, molecular_formula_expr(Species.smiles))
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id.in_(species_entry_ids))
    ).all()
    return dict(rows)


def _resolve_participant_species_ids(
    session: Session, species_entry_ids: set[int]
) -> dict[int, int]:
    """Map species_entry_id -> parent species_id.

    Needed to key into ``chem_reaction.reaction_participant``, which is
    keyed by species_id, not species_entry_id.
    """
    if not species_entry_ids:
        return {}
    rows = session.execute(
        select(SpeciesEntry.id, SpeciesEntry.species_id).where(
            SpeciesEntry.id.in_(species_entry_ids)
        )
    ).all()
    return dict(rows)


def _resolve_reaction_participant_stoichiometry(
    session: Session, reaction_ids: set[int]
) -> dict[tuple[int, int, ReactionRole], int]:
    """Map (reaction_id, species_id, role) -> graph-identity stoichiometry.

    This is the reaction-level coefficient ("this equation consumes two
    of this species"), not a property of any one deposit's structure-
    participant row -- see ``ReactionParticipantSummary``'s docstring.
    """
    if not reaction_ids:
        return {}
    rows = session.execute(
        select(
            ReactionParticipant.reaction_id,
            ReactionParticipant.species_id,
            ReactionParticipant.role,
            ReactionParticipant.stoichiometry,
        ).where(ReactionParticipant.reaction_id.in_(reaction_ids))
    ).all()
    return {
        (reaction_id, species_id, role): stoichiometry
        for reaction_id, species_id, role, stoichiometry in rows
    }


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
    return dict(rows)


def _resolve_participant_labels(
    session: Session, species_entry_ids: set[int]
) -> dict[int, str | None]:
    """Map species_entry_id → the entry discriminator, or ``None``.

    A participant's ``smiles`` is the parent *species*' and is shared by
    every entry under it, so two participants that are different
    stereoisomers of one species are indistinguishable without this.
    """
    if not species_entry_ids:
        return {}
    rows = session.execute(
        select(
            SpeciesEntry.id,
            SpeciesEntry.stereo_label,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.isotope_key,
        ).where(SpeciesEntry.id.in_(species_entry_ids))
    ).all()
    return {row.id: species_entry_label_for(row) for row in rows}


def _compute_availability(
    session: Session, entry_ids: list[int]
) -> dict[int, ReactionAvailability]:
    if not entry_ids:
        return {}

    kinetics_count_by_entry: dict[int, int] = dict.fromkeys(entry_ids, 0)
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

    # has_atom_map: the reaction states which atom of its reactants and
    # products is which atom of its transition state (ADR 0011). Advertised
    # here so a search result already distinguishes a mapped reaction from an
    # unmapped one; an unmapped reaction is a true record, just an incomplete
    # one, and the flag is what makes the incompleteness visible.
    has_atom_map_set = set(
        session.scalars(
            select(ReactionAtomMap.reaction_entry_id)
            .where(ReactionAtomMap.reaction_entry_id.in_(entry_ids))
            .distinct()
        ).all()
    )

    return {
        eid: ReactionAvailability(
            has_kinetics=kinetics_count_by_entry.get(eid, 0) > 0,
            has_transition_state=eid in has_ts_set,
            has_path_search=eid in has_path_search_set,
            has_atom_map=eid in has_atom_map_set,
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
    match: ReactionMatchMode,
) -> ReactionDirectionQuery:
    """Determine which orientation of the entry the query actually matched.

    For ``forward`` and ``reverse`` the requested orientation is the matched
    orientation. For ``either``, prefer ``forward`` when the query matches the
    stored orientation *under the same* ``match`` semantics the matcher used;
    otherwise ``reverse``. Testing this with multiset equality while the
    matcher used containment would report ``reverse`` for every one-sided
    ``contains`` query, including the forward ones.
    """
    if requested == ReactionDirectionQuery.forward:
        return ReactionDirectionQuery.forward
    if requested == ReactionDirectionQuery.reverse:
        return ReactionDirectionQuery.reverse

    if _orientation_matches(
        stored_reactants=entry_species[ReactionRole.reactant],
        stored_products=entry_species[ReactionRole.product],
        queried_reactants=reactant_species_ids,
        queried_products=product_species_ids,
        match=match,
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


def _materialize_and_sort_reaction_records(
    *,
    entries: list[ReactionEntry],
    chem_reactions: dict[int, ChemReaction],
    participants_by_entry: dict[int, list[tuple[int, ReactionRole, int]]],
    refs_by_entry_species: dict[int, str],
    labels_by_entry_species: dict[int, str | None],
    smiles_by_entry_species: dict[int, str],
    formulas_by_entry_species: dict[int, str | None],
    species_id_by_entry_species: dict[int, int],
    stoichiometry_by_key: dict[tuple[int, int, ReactionRole], int],
    badges: dict[int, RecordReviewBadge],
    availability_by_entry: dict[int, ReactionAvailability],
    matched_direction_by_entry: dict[int, ReactionDirectionQuery],
    family_name_by_id: dict[int, str],
) -> list[ReactionScientificRecord]:
    """Project bulk-loaded per-entry data into sorted ``ReactionScientificRecord`` rows.

    Shared by :func:`search_reactions` and :func:`browse_reactions` so a
    search row and a browse row are byte-identical in shape -- only the
    candidate-set construction differs between the two callers; everything
    from "which entries survived the review filter" onward is this one
    function. Sort order is the D-series default: ``review_rank`` ASC,
    ``has_kinetics`` DESC, ``has_transition_state`` DESC, ``created_at``
    DESC, ``id`` DESC.
    """
    records: list[ReactionScientificRecord] = []
    for e in entries:
        chem = chem_reactions[e.reaction_id]
        participants = participants_by_entry[e.id]
        reactants = [
            ReactionParticipantSummary(
                species_entry_id=se,
                species_entry_ref=refs_by_entry_species.get(se, ""),
                species_entry_label=labels_by_entry_species.get(se),
                smiles=smiles_by_entry_species.get(se, ""),
                formula=formulas_by_entry_species.get(se),
                stoichiometry=stoichiometry_by_key.get(
                    (chem.id, species_id_by_entry_species.get(se), role), 1
                ),
                participant_index=idx,
            )
            for se, role, idx in sorted(participants, key=lambda p: (p[1].value, p[2]))
            if role == ReactionRole.reactant
        ]
        products = [
            ReactionParticipantSummary(
                species_entry_id=se,
                species_entry_ref=refs_by_entry_species.get(se, ""),
                species_entry_label=labels_by_entry_species.get(se),
                smiles=smiles_by_entry_species.get(se, ""),
                formula=formulas_by_entry_species.get(se),
                stoichiometry=stoichiometry_by_key.get(
                    (chem.id, species_id_by_entry_species.get(se), role), 1
                ),
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
    return records


def _filter_echo(request: ReactionSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.reactants:
        echo["reactants"] = list(request.reactants)
    if request.products:
        echo["products"] = list(request.products)
    echo["direction"] = request.direction.value
    echo["match"] = request.match.value
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


def _browse_filter_echo(request: ReactionsBrowseRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.reactant_smiles:
        echo["reactant_smiles"] = list(request.reactant_smiles)
    if request.product_smiles:
        echo["product_smiles"] = list(request.product_smiles)
    if request.family is not None:
        echo["family"] = request.family
    if request.has_kinetics is not None:
        echo["has_kinetics"] = request.has_kinetics
    if request.has_transition_state is not None:
        echo["has_transition_state"] = request.has_transition_state
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_browse_response(
    request: ReactionsBrowseRequest,
    offset: int,
    limit: int,
) -> ScientificReactionSearchResponse:
    return ScientificReactionSearchResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=CollapseMode.all,
            include=[],
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
