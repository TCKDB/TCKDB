"""Service implementation for /api/v1/scientific/species/search.

See docs/specs/read_api_mvp.md §Endpoint 1 for the contract.

Where the work happens
----------------------
A species record is ranked by its *best visible entry*, so review
visibility and ranking are properties of the ``species_entry`` set, not of
``species``. Both are expressed in SQL (see
:mod:`app.services.scientific_read.sql_review`) over one candidate-species
subquery, and only the page is materialized: the per-entry badges,
availability counts and ``include=`` section id lists are fetched for the
species on the page, never for every match.

The previous shape loaded every matching species, every one of their
entries, and a review badge for each, before it could sort or slice. That
is not just wasted work at catalog scale — the entry and badge loads render
one bind parameter per id, and PostgreSQL's wire protocol caps a statement
at 65,535 parameters, so a broad enough identifier match returned ``503
database_unavailable`` instead of a page. ``backend/docs/benchmarks/README.md``
has the measurement.
"""

from __future__ import annotations

from sqlalchemy import Text, and_, case, func, select
from sqlalchemy.orm import Session

from app.api.error_contract import reject_unsupported_filters
from app.db.models.calculation import Calculation
from app.db.models.common import (
    SubmissionRecordType,
)
from app.db.models.species import (
    ConformerGroup,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_species import (
    RequestEcho,
    ScientificSpeciesBrowseResponse,
    ScientificSpeciesSearchResponse,
    SpeciesBrowseRequest,
    SpeciesEntryAvailability,
    SpeciesEntryScientificRecord,
    SpeciesEntrySectionIds,
    SpeciesFilterRequest,
    SpeciesScientificRecord,
    SpeciesSearchRequest,
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
    reconcile_species_entry_pair,
    reconcile_species_pair,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
)
from app.services.scientific_read.sql_review import (
    join_review,
    review_rank_expr,
    review_status_expr,
    summary_from_sql,
    visible_review_filter,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "thermo",
    "statmech",
    "transport",
    "conformers",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

#: /species/browse's own include vocabulary -- deliberately missing
#: ``thermo``/``statmech``/``transport``/``conformers``.
#:
#: Those four tokens gate a :class:`~app.schemas.reads.scientific_species.
#: SpeciesEntrySectionIds` block whose payload is a bare integer-id array
#: (``{"ids": [...]}``). ``is_internal_id_key()`` in
#: ``app/services/scientific_read/internal_ids.py`` only matches keys
#: ending in ``_id``/``_ids`` or on its literal deny-list, so a field
#: named plain ``ids`` sails through the Phase D strip untouched -- on
#: ``/species/search`` that is a pre-existing, unfixed gap (out of scope
#: here; a caller must already have an identifier to reach it), but on an
#: identifier-free, unauthenticated, whole-corpus listing the same gap
#: turns ``GET /species/browse?include=all&limit=200`` into a way to
#: harvest every thermo/statmech/transport/conformer-group primary key in
#: the archive anonymously, in one request. See
#: ``docs/specs/public_identifier_policy.md`` (sequential PKs rejected
#: because they "leak the total count of objects ... and roughly the
#: upload schedule") and ``internal_ids_visibility_policy.md`` (bare
#: integer-id arrays are meant to be hidden by default).
#:
#: Dropping the tokens rather than adding ``"ids"`` to the Phase D
#: deny-list: the deny-list is global, so widening it is a separate,
#: wider-blast-radius change this endpoint should not force. Availability
#: already tells a browse caller whether a section exists
#: (``SpeciesEntryAvailability.has_thermo`` etc., plus
#: ``calculation_count``); a caller who wants the actual thermo/statmech/
#: transport/conformer rows navigates by ``species_entry_ref``, which is
#: the handle this surface hands out, not by an id an anonymous listing
#: should never have handed them in the first place.
_BROWSE_LEGAL_INCLUDE_TOKENS: set[str] = {"review", "internal_ids", "all"}

_DEFAULT_SORT_ECHO = "review_rank,has_entries,created_at,id"

#: The rank a species with no *visible* entry sorts at. Deliberately worse
#: than every real review rank, so "has entries the caller may see" beats
#: "has none" without needing a second sort key ahead of it.
_NO_VISIBLE_ENTRY_RANK = max(REVIEW_RANK.values()) + 1


def _formula_expr():
    """The species' molecular formula, derived in SQL by the RDKit cartridge.

    ``species`` has no stored formula column. ``mol_formula()`` over
    ``mol_from_smiles(sp.smiles)`` yields Hill notation (e.g. ``H2O``,
    ``C3H6``), with a trailing charge suffix for ions (``HO-``, ``H4N+``,
    ``Fe+2``). Radicals carry no marker: ``[CH3]`` is ``CH3``. Isotopes are
    not distinguished — the cartridge's default ``mol_formula()`` ignores
    isotope labels, so heavy water reports as ``H2O``, the same as light
    water. ``mol_from_smiles()`` returns SQL NULL for any row whose SMILES
    fails to parse, so such a row yields a NULL formula rather than raising.

    **One expression, two uses, deliberately.** This is both the ``formula=``
    filter predicate and the ``formula`` field served on every record. A
    caller who searched by formula therefore reads back the very string that
    was matched, because it is the same expression over the same column — not
    a second formula implementation that happens to agree today. The
    functional index ``ix_species_formula_lookup`` is written over this
    expression too, which is what makes the filter cheap.

    Depending on the cartridge here costs nothing new: the base Alembic
    revision ``60b67e360daf`` runs ``CREATE EXTENSION IF NOT EXISTS rdkit``
    before any table exists, so a database this code can talk to has it.
    Deriving the string in Python instead would reach for the ``rdkit``
    wheel, which ``pyproject.toml`` declares as an opt-in ``[rdkit]`` extra.
    """
    return func.mol_formula(func.mol_from_smiles(Species.smiles)).cast(Text)


def search_species(
    session: Session, request: SpeciesSearchRequest
) -> ScientificSpeciesSearchResponse:
    """Discover species by chemical identity, with per-entry trust + availability.

    Filters multiple identifiers with AND semantics; inconsistent identifiers
    return an empty result set rather than raising. Default sort is the L3
    ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``. Client-
    supplied ``sort=`` is rejected (v0). ``rejected`` and ``deprecated``
    review states are excluded by default.

    :param session: SQLAlchemy session bound to the read DB.
    :param request: Parsed request model.
    :returns: ``ScientificSpeciesSearchResponse`` Pydantic model.
    :raises ValueError: 422 for sort/pagination/include validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # InChI is not persisted, so accepting it would violate the advertised
    # AND semantics whenever another identifier is supplied.
    reject_unsupported_filters(
        {"inchi": request.inchi},
        endpoint="/scientific/species/search",
    )

    # Phase C: ref-by-handle filters are valid identifier sources. Resolve
    # them first so we know whether the caller supplied a real identifier
    # and, if so, what id(s) to scope by.
    species_pair = reconcile_species_pair(
        session,
        id_value=None,
        ref_value=request.species_ref,
    )
    species_entry_pair = reconcile_species_entry_pair(
        session,
        id_value=None,
        ref_value=request.species_entry_ref,
    )
    if species_pair is NO_MATCH or species_entry_pair is NO_MATCH:
        return _empty_response(request, includes, offset, limit)
    species_ref_id: int | None = species_pair  # type: ignore[assignment]
    species_entry_ref_id: int | None = species_entry_pair  # type: ignore[assignment]

    has_chem_identifier = any(
        v is not None
        for v in (
            request.smiles,
            request.inchi,
            request.inchi_key,
            request.formula,
        )
    )
    if not has_chem_identifier and species_ref_id is None and species_entry_ref_id is None:
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula, species_ref, species_entry_ref} is required."
        )

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    # The identity filter, as a subquery rather than a materialized id list —
    # the entry, count and ranking queries below all re-derive it in SQL, so
    # a match of any size stays a match rather than becoming bind parameters.
    candidates = _candidate_species_stmt(
        request,
        species_ref_id=species_ref_id,
        species_entry_ref_id=species_entry_ref_id,
    ).subquery("candidate_species")

    # Pre-collapse total is the number of *species* matched, whether or not
    # any of their entries survived the trust gate: a species with none is
    # still a record, with an empty ``entries`` list.
    pre_collapse_total = (
        session.scalar(select(func.count()).select_from(candidates)) or 0
    )
    if pre_collapse_total == 0:
        return _empty_response(request, includes, offset, limit)

    # Pre-collapse review summary across every surviving entry, not just the
    # page's — one aggregate rather than one badge per candidate.
    summary = summary_from_sql(
        session,
        _visible_entry_rows(
            candidates,
            request,
            visible,
            species_entry_ref_id=species_entry_ref_id,
        ),
    )

    collapse_first = request.collapse.value == "first"
    page_species_ids = _rank_and_slice_species(
        session,
        candidates,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
        offset=0 if collapse_first else offset,
        limit=1 if collapse_first else limit,
    )
    if collapse_first:
        # ``collapse=first`` keeps the single best record and *then* applies
        # offset/limit to that one-element list, so any offset past it is
        # an empty page. Mirrored here rather than folded into the SQL.
        page_species_ids = page_species_ids[offset : offset + limit]

    returned_records = _build_page_records(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
        includes=includes,
    )

    pagination = build_pagination(
        offset=offset,
        limit=limit,
        returned=len(returned_records),
        total=pre_collapse_total,
        collapse_first=collapse_first,
    )

    request_filter = _filter_echo(request)
    return ScientificSpeciesSearchResponse(
        request=RequestEcho(
            filter=request_filter,
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned_records,
        pagination=pagination,
    )


def browse_species(
    session: Session, request: SpeciesBrowseRequest
) -> ScientificSpeciesBrowseResponse:
    """List species by secondary filter alone — no identifier required.

    See docs/specs/read_api_mvp.md §Endpoint 1 for the sibling
    ``/species/search`` contract this deliberately mirrors. The two
    surfaces exist because ``search_species`` rejects an identifier-free
    request (``missing_identifier``) by design: a caller who *knows* what
    they want should get an exact, ref-scoped answer, and relaxing that
    refusal for an ``?browse=true`` opt-in would make the same route mean
    two different things depending on a flag. A catalogue read is a
    different question — "what does this archive hold?" — with a
    different shape of answer (a bounded, paged listing of the whole
    corpus, not a lookup), so it gets its own path and its own request
    model (:class:`SpeciesBrowseRequest`, which structurally cannot carry
    an identifier).

    Everything downstream of "which candidate species" is shared with
    ``search_species`` verbatim: the same visibility gate
    (:func:`app.services.scientific_read.common.visible_statuses`), the
    same ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``
    order (:func:`_rank_and_slice_species`, whose ``id DESC`` tiebreak is
    what keeps pagination stable — see its docstring), the same bounded
    page size (:func:`app.services.scientific_read.common.validate_pagination`,
    default 50 / hard cap 200), and the same per-page record builder
    (:func:`_build_page_records`) — so a browse record and a search record
    are the same shape, metadata only: identity, refs, per-entry
    availability *counts* and review badges, never an artifact, geometry
    or calculation payload.

    Only the candidate set differs: :func:`_browse_candidate_species_stmt`
    has no identifier predicate at all (there is no identifier field on
    :class:`SpeciesBrowseRequest` to read one from), so with every
    optional filter absent it is every species in the corpus.

    :param session: SQLAlchemy session bound to the read DB.
    :param request: Parsed request model.
    :returns: ``ScientificSpeciesBrowseResponse`` Pydantic model.
    :raises ValueError: 422 for sort/pagination/include validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _BROWSE_LEGAL_INCLUDE_TOKENS,
        "/scientific/species/browse",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    candidates = _browse_candidate_species_stmt(request).subquery("candidate_species")

    pre_collapse_total = (
        session.scalar(select(func.count()).select_from(candidates)) or 0
    )
    if pre_collapse_total == 0:
        return _empty_browse_response(request, includes, offset, limit)

    summary = summary_from_sql(
        session,
        _visible_entry_rows(candidates, request, visible, species_entry_ref_id=None),
    )

    collapse_first = request.collapse.value == "first"
    page_species_ids = _rank_and_slice_species(
        session,
        candidates,
        request,
        visible,
        species_entry_ref_id=None,
        offset=0 if collapse_first else offset,
        limit=1 if collapse_first else limit,
    )
    if collapse_first:
        page_species_ids = page_species_ids[offset : offset + limit]

    returned_records = _build_page_records(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=None,
        includes=includes,
    )

    pagination = build_pagination(
        offset=offset,
        limit=limit,
        returned=len(returned_records),
        total=pre_collapse_total,
        collapse_first=collapse_first,
    )

    return ScientificSpeciesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned_records,
        pagination=pagination,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _candidate_species_stmt(
    request: SpeciesSearchRequest,
    *,
    species_ref_id: int | None = None,
    species_entry_ref_id: int | None = None,
):
    """The identity filter as a ``SELECT id, created_at`` over ``species``.

    Returns a statement, not rows: it is used as a subquery by the count,
    the summary and the ranking, so the caller never holds the matching id
    set in Python.
    """
    stmt = select(Species.id, Species.created_at)
    if request.smiles is not None:
        stmt = stmt.where(Species.smiles == request.smiles)
    if request.inchi_key is not None:
        stmt = stmt.where(Species.inchi_key == request.inchi_key)
    if request.charge is not None:
        stmt = stmt.where(Species.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(Species.multiplicity == request.multiplicity)
    if species_ref_id is not None:
        stmt = stmt.where(Species.id == species_ref_id)
    if species_entry_ref_id is not None:
        # Constrain the parent species to the one owning this entry.
        stmt = stmt.where(
            Species.id
            == select(SpeciesEntry.species_id)
            .where(SpeciesEntry.id == species_entry_ref_id)
            .scalar_subquery()
        )
    if request.formula is not None:
        # Derived per-query by the cartridge rather than stored: see
        # _formula_expr() for the notation, the isotope caveat and the
        # NULL-on-unparseable behavior (which excludes such a row here
        # rather than raising). Match is case-sensitive and exact; we only
        # strip incidental surrounding whitespace from client input.
        #
        # _build_page_records() serves the same expression as the record's
        # `formula`, so what a caller filtered on is what they read back.
        stmt = stmt.where(_formula_expr() == request.formula.strip())
    return stmt


def _browse_candidate_species_stmt(request: SpeciesBrowseRequest):
    """The browse candidate set as a ``SELECT id, created_at`` over ``species``.

    Same shape and same use (a subquery for the count, the summary and the
    ranking) as :func:`_candidate_species_stmt`, and deliberately not a
    call to it with ``None`` identifiers threaded through: that would let
    a future identifier field added to ``SpeciesSearchRequest`` silently
    reach browse through the shared function unless every call site were
    re-audited. Keeping the statement builders separate means the only
    way ``/species/browse`` gains an identifier predicate is a change
    written here, on a request type that has no identifier fields to read
    one from.

    With ``charge``, ``multiplicity`` and ``formula`` all absent (the
    default, empty browse request) this is every species in the corpus —
    which is the whole point of the endpoint.
    """
    stmt = select(Species.id, Species.created_at)
    if request.charge is not None:
        stmt = stmt.where(Species.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(Species.multiplicity == request.multiplicity)
    if request.formula is not None:
        # Same expression, same case-sensitive exact match, same NULL-on-
        # unparseable behavior as _candidate_species_stmt(); see
        # _formula_expr() for the full rationale.
        stmt = stmt.where(_formula_expr() == request.formula.strip())
    return stmt


def _entry_filter_predicates(
    request: SpeciesFilterRequest, species_entry_ref_id: int | None
) -> list:
    """The per-entry ``where`` terms, in one place for all three uses."""
    predicates = []
    if request.electronic_state_kind is not None:
        predicates.append(
            SpeciesEntry.electronic_state_kind == request.electronic_state_kind
        )
    if request.species_entry_kind is not None:
        predicates.append(SpeciesEntry.kind == request.species_entry_kind)
    if species_entry_ref_id is not None:
        predicates.append(SpeciesEntry.id == species_entry_ref_id)
    return predicates


def _join_entries_and_reviews(
    stmt,
    candidates,
    request: SpeciesFilterRequest,
    *,
    species_entry_ref_id: int | None,
    outer: bool,
):
    """Join candidate species → their entries → each entry's review row.

    The chain is deliberately **flat**: ``species`` joined to
    ``species_entry`` joined to ``record_review``, with the per-entry filters
    as join conditions, rather than a pre-filtered entry *subquery* joined to
    the species set.

    That is not a cosmetic preference. With the entry filters and the review
    LEFT JOIN wrapped in a subquery, the planner is free to compile
    ``species_entry.species_id = species.id`` into a join *filter* rather
    than an index condition — the review outer join and its ``status IS
    NULL`` branch block the pushdown — and then the inner side is re-scanned
    in full once per candidate species. That plan was observed on a
    statistics-free database (65,599 candidate species: over 15 minutes,
    ``Join Filter: (species_entry.species_id = species.id)`` above a
    full ``ix_species_entry_species_id`` scan). Keeping the chain flat leaves
    the join key on a plain indexed column where the planner can reach it.

    Statistics still matter more than shape — no formulation of this query
    survives a table that has tens of thousands of rows and no ``ANALYZE``.
    What the flat chain buys unconditionally is that the identity predicate
    is evaluated once per statement instead of twice, which is what the
    RDKit-backed ``formula=`` filter cares about: on the Stage 4 benchmark
    corpus the broad formula search is 21.7 ms flat against 28.3 ms wrapped.

    :param outer: ``True`` keeps species with no matching entry (the ranking
        needs them — they are still records); ``False`` drops them (the
        summary counts entries, not species).
    :returns: ``(statement, review_alias)``.
    """
    entry_on = and_(
        SpeciesEntry.species_id == candidates.c.id,
        *_entry_filter_predicates(request, species_entry_ref_id),
    )
    from_clause = (
        candidates.outerjoin(SpeciesEntry, entry_on)
        if outer
        else candidates.join(SpeciesEntry, entry_on)
    )
    return join_review(
        stmt.select_from(from_clause),
        SpeciesEntry.id,
        SubmissionRecordType.species_entry,
    )


def _rank_and_slice_species(
    session: Session,
    candidates,
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
    offset: int,
    limit: int,
) -> list[int]:
    """Order the candidate species and return one page of ids.

    ``review_rank ASC, has_entries DESC, created_at DESC, id DESC``, where
    ``review_rank`` is the best rank among a species' *visible* entries and
    :data:`_NO_VISIBLE_ENTRY_RANK` when it has none.

    Visibility is a ``CASE`` inside the aggregate rather than a ``WHERE``.
    A ``WHERE`` would drop species whose every entry is invisible, and those
    species are still records — with an empty ``entries`` list. ``MIN``
    ignores NULLs, so an invisible entry contributes nothing and a species
    with no visible entry aggregates to NULL, which the ``COALESCE`` turns
    into the sorts-last rank.

    The ``SpeciesEntry.id IS NOT NULL`` guard is load-bearing: without it the
    outer join's all-NULL row for an entry-less species would satisfy the
    "no review row means not_reviewed" branch of the visibility predicate and
    the species would rank as if it had a never-reviewed entry.

    ``id`` desc is the tiebreak that makes this a total order; without it,
    species sharing a ``created_at`` (every row inserted in one transaction
    does) would page non-deterministically.
    """
    stmt, review = _join_entries_and_reviews(
        select(candidates.c.id),
        candidates,
        request,
        species_entry_ref_id=species_entry_ref_id,
        outer=True,
    )
    visible_rank = case(
        (
            and_(
                SpeciesEntry.id.is_not(None),
                visible_review_filter(review, visible),
            ),
            review_rank_expr(review),
        ),
        else_=None,
    )
    best_rank = func.coalesce(func.min(visible_rank), _NO_VISIBLE_ENTRY_RANK)
    has_entries = func.count(visible_rank) > 0
    stmt = (
        stmt.group_by(candidates.c.id, candidates.c.created_at)
        .order_by(
            best_rank.asc(),
            has_entries.desc(),
            candidates.c.created_at.desc(),
            candidates.c.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _visible_entry_rows(
    candidates,
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
):
    """Subquery of every visible entry of every candidate species.

    Feeds the pre-pagination :func:`summary_from_sql` aggregate. Referenced
    exactly once, so PostgreSQL inlines it into the aggregate rather than
    treating it as an optimization fence.
    """
    stmt, review = _join_entries_and_reviews(
        select(SpeciesEntry.id),
        candidates,
        request,
        species_entry_ref_id=species_entry_ref_id,
        outer=False,
    )
    return (
        stmt.add_columns(review_status_expr(review).label("review_status"))
        .where(visible_review_filter(review, visible))
        .subquery("visible_entries")
    )


def _load_page_entries(
    session: Session,
    page_species_ids: list[int],
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
) -> list[SpeciesEntry]:
    """The visible entries of the page's species, in a stable order.

    Scoped by the page's species ids — at most ``limit`` of them — so this is
    the one place an ``IN`` list of ids is safe.
    """
    stmt = select(SpeciesEntry).where(
        SpeciesEntry.species_id.in_(page_species_ids),
        *_entry_filter_predicates(request, species_entry_ref_id),
    )
    stmt, review = join_review(
        stmt, SpeciesEntry.id, SubmissionRecordType.species_entry
    )
    return list(
        session.scalars(
            stmt.where(visible_review_filter(review, visible)).order_by(
                SpeciesEntry.id
            )
        )
    )


def _build_page_records(
    session: Session,
    page_species_ids: list[int],
    request: SpeciesFilterRequest,
    visible: set,
    *,
    species_entry_ref_id: int | None,
    includes: set[str],
) -> list[SpeciesScientificRecord]:
    """Materialize one page: species, their visible entries, and the extras.

    Every load below is bounded by the page — at most ``limit`` species and
    their entries — which is the whole point of ranking in SQL first.
    """
    if not page_species_ids:
        return []

    # The formula rides along with the species row rather than being fetched
    # separately: it is derived from a column of that same row, and the page
    # is already bounded, so the cartridge evaluates it at most `limit` times.
    species_rows = session.execute(
        select(Species, _formula_expr()).where(Species.id.in_(page_species_ids))
    ).all()
    species_by_id = {species.id: species for species, _ in species_rows}
    formula_by_id = {species.id: formula for species, formula in species_rows}
    entries = _load_page_entries(
        session,
        page_species_ids,
        request,
        visible,
        species_entry_ref_id=species_entry_ref_id,
    )
    entries_by_species: dict[int, list[SpeciesEntry]] = {
        species_id: [] for species_id in page_species_ids
    }
    for entry in entries:
        entries_by_species[entry.species_id].append(entry)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=[entry.id for entry in entries],
    )
    availability_per_entry = _compute_availability(session, entries)
    section_ids = _compute_section_ids(session, entries, includes)

    records: list[SpeciesScientificRecord] = []
    for species_id in page_species_ids:
        species = species_by_id[species_id]
        records.append(
            SpeciesScientificRecord(
                species_id=species.id,
                species_ref=species.public_ref,
                canonical_smiles=species.smiles,
                inchi_key=species.inchi_key,
                formula=formula_by_id[species_id],
                charge=species.charge,
                multiplicity=species.multiplicity,
                stereo_kind=species.stereo_kind,
                entries=[
                    _build_entry_record(
                        entry,
                        badges[entry.id],
                        availability_per_entry[entry.id],
                        section_ids.get(entry.id, {}),
                    )
                    for entry in entries_by_species[species_id]
                ],
            )
        )
    return records


def _compute_availability(
    session: Session, entries: list[SpeciesEntry]
) -> dict[int, SpeciesEntryAvailability]:
    """Bulk-compute availability flags + counts for a list of species entries."""
    if not entries:
        return {}
    entry_ids = [e.id for e in entries]

    thermo_ids = set(
        session.scalars(
            select(Thermo.species_entry_id).where(
                Thermo.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    statmech_ids = set(
        session.scalars(
            select(Statmech.species_entry_id).where(
                Statmech.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    transport_ids = set(
        session.scalars(
            select(Transport.species_entry_id).where(
                Transport.species_entry_id.in_(entry_ids)
            )
        ).all()
    )
    conformer_ids = set(
        session.scalars(
            select(ConformerGroup.species_entry_id).where(
                ConformerGroup.species_entry_id.in_(entry_ids)
            )
        ).all()
    )

    calc_counts: dict[int, int] = dict.fromkeys(entry_ids, 0)
    for entry_id, count in session.execute(
        select(Calculation.species_entry_id, func.count(Calculation.id))
        .where(Calculation.species_entry_id.in_(entry_ids))
        .group_by(Calculation.species_entry_id)
    ).all():
        calc_counts[entry_id] = count

    return {
        e.id: SpeciesEntryAvailability(
            has_thermo=e.id in thermo_ids,
            has_statmech=e.id in statmech_ids,
            has_transport=e.id in transport_ids,
            has_conformers=e.id in conformer_ids,
            calculation_count=calc_counts.get(e.id, 0),
        )
        for e in entries
    }


def _compute_section_ids(
    session: Session,
    entries: list[SpeciesEntry],
    includes: set[str],
) -> dict[int, dict[str, SpeciesEntrySectionIds]]:
    """Bulk-fetch ID lists per entry for any include= sections requested."""
    if not entries or not includes:
        return {}
    entry_ids = [e.id for e in entries]
    result: dict[int, dict[str, SpeciesEntrySectionIds]] = {
        eid: {} for eid in entry_ids
    }

    if "thermo" in includes:
        thermo_pairs = session.execute(
            select(Thermo.species_entry_id, Thermo.id).where(
                Thermo.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry: dict[int, list[int]] = {eid: [] for eid in entry_ids}
        for eid, tid in thermo_pairs:
            per_entry[eid].append(tid)
        for eid, ids in per_entry.items():
            result[eid]["thermo"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "statmech" in includes:
        pairs = session.execute(
            select(Statmech.species_entry_id, Statmech.id).where(
                Statmech.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, sid in pairs:
            per_entry[eid].append(sid)
        for eid, ids in per_entry.items():
            result[eid]["statmech"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "transport" in includes:
        pairs = session.execute(
            select(Transport.species_entry_id, Transport.id).where(
                Transport.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, tid in pairs:
            per_entry[eid].append(tid)
        for eid, ids in per_entry.items():
            result[eid]["transport"] = SpeciesEntrySectionIds(ids=sorted(ids))

    if "conformers" in includes:
        pairs = session.execute(
            select(ConformerGroup.species_entry_id, ConformerGroup.id).where(
                ConformerGroup.species_entry_id.in_(entry_ids)
            )
        ).all()
        per_entry = {eid: [] for eid in entry_ids}
        for eid, cid in pairs:
            per_entry[eid].append(cid)
        for eid, ids in per_entry.items():
            result[eid]["conformers"] = SpeciesEntrySectionIds(ids=sorted(ids))

    # include=review is a no-op at the data-shape level here — every entry
    # already carries a RecordReviewBadge by default. The token is accepted
    # for consistency with the L4 vocabulary.
    return result


def _build_entry_record(
    entry: SpeciesEntry,
    badge: RecordReviewBadge,
    availability: SpeciesEntryAvailability,
    section_ids: dict[str, SpeciesEntrySectionIds],
) -> SpeciesEntryScientificRecord:
    return SpeciesEntryScientificRecord(
        species_entry_id=entry.id,
        species_entry_ref=entry.public_ref,
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
        # The identity columns are read straight off the already-loaded
        # entity: no extra query, and no substitution for a NULL. See
        # SpeciesEntryScientificRecord for why they are in the default
        # projection rather than behind an include token.
        stereo_label=entry.stereo_label,
        electronic_state_label=entry.electronic_state_label,
        term_symbol=entry.term_symbol,
        isotope_key=entry.isotope_key,
        species_entry_label=species_entry_label_for(entry),
        review=badge,
        availability=availability,
        thermo_summary=section_ids.get("thermo"),
        statmech_summary=section_ids.get("statmech"),
        transport_summary=section_ids.get("transport"),
        conformers_summary=section_ids.get("conformers"),
    )


def _filter_echo(request: SpeciesSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    for field in (
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "species_ref",
        "species_entry_ref",
    ):
        value = getattr(request, field)
        if value is not None:
            echo[field] = value
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: SpeciesSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesSearchResponse:
    return ScientificSpeciesSearchResponse(
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


def _browse_filter_echo(request: SpeciesBrowseRequest) -> dict[str, object]:
    """:func:`_filter_echo`'s counterpart for browse.

    Not a call to ``_filter_echo`` with the identifier fields absent:
    :class:`SpeciesBrowseRequest` has no ``smiles`` / ``inchi`` /
    ``inchi_key`` / ``species_ref`` / ``species_entry_ref`` attributes to
    read, so ``getattr(request, "smiles")`` would raise rather than
    quietly return ``None``. Kept as its own small function — duplicating
    the shared-field half rather than threading a field list through a
    common helper — so a change to one endpoint's echo can never silently
    move the other's.
    """
    echo: dict[str, object] = {}
    for field in ("formula", "charge", "multiplicity"):
        value = getattr(request, field)
        if value is not None:
            echo[field] = value
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_browse_response(
    request: SpeciesBrowseRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesBrowseResponse:
    return ScientificSpeciesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
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
