"""Service implementation for /api/v1/scientific/species/structure-search.

Public RDKit-backed structure search over species entries. The service is
the first consumer of the PostgreSQL RDKit cartridge in the read layer —
substructure matching uses the cartridge's ``mol @> qmol`` operator and
similarity uses ``tanimoto_sml(morganbv_fp(...), morganbv_fp(...))`` over
Morgan-bit fingerprints.

Query parsing and InChIKey canonicalization happen on the Python side
via RDKit; the canonicalized SMILES (or SMARTS / InChIKey) is then bound
into a parameterized SQL statement that performs the actual filter and
score computation database-side. We never load every species row into
Python — that would defeat the cartridge.

Substructure and similarity queries read from the stored
``species_entry.mol`` cartridge column. The write path
(``app/services/species_resolution.py``) canonicalizes SMILES into
``mol`` on insert, and the
``d4e5f6a7b8c9_add_species_entry_mol_gist_index`` migration creates a
GiST index on the column and backfills any pre-existing NULL rows.
Rows whose ``mol`` is NULL (e.g. a SMILES the cartridge cannot parse)
are excluded from results — a single seq-scan-per-row fallback would
defeat the index. Exact mode keeps the indexed ``species.inchi_key``
path; it does not need the cartridge.

Visibility filtering (default-hidden ``rejected``/``deprecated``),
deterministic ordering by ``review_rank`` (and ``similarity_score`` in
similarity mode), and pagination are all pushed into SQL — a pathological
substructure query (e.g. ``[#6]`` matching nearly every row) returns at
most ``limit`` rows to Python. The exact post-filter ``total`` is
computed by a separate aggregate query that derives both the total and
the per-status review summary in one scan.

See ``backend/docs/specs/scientific_structure_search.md`` for the full
contract.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry.stereo import unrepresented_stereo_kinds
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reads.scientific_common import (
    OrderEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
    browse_order,
)
from app.schemas.reads.scientific_structure_search import (
    DEFAULT_SIMILARITY_THRESHOLD,
    RequestEcho,
    ScientificSpeciesStructureSearchRecord,
    ScientificSpeciesStructureSearchRequest,
    ScientificSpeciesStructureSearchResponse,
    StructureMatchSummary,
    StructureQueryKind,
    StructureSearchMode,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.hidden_by_default import hidden_by_default_from_counts, record_hidden_by_default
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.keyset import InvalidCursorError, Page, Paging, check_key_values
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
)
from app.services.scientific_read.sql_review import review_rank_case_sql
from app.services.scientific_read.structure_query import (
    enforce_mode_query_compatibility,
    entry_stereo_descriptor_of_query,
    inchi_key_from_query,
    parse_inchi_to_canonical_smiles,
    parse_smarts,
    parse_smiles_to_canonical,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {"review", "internal_ids", "all"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


_DEFAULT_SORT_BY_MODE: dict[StructureSearchMode, OrderEcho] = {
    StructureSearchMode.substructure: browse_order("review_rank", "species_entry_id"),
    StructureSearchMode.similarity: browse_order("similarity_score", "review_rank", "species_entry_id"),
    StructureSearchMode.exact: browse_order("review_rank", "species_entry_id"),
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def search_species_by_structure(
    session: Session,
    request: ScientificSpeciesStructureSearchRequest,
) -> ScientificSpeciesStructureSearchResponse:
    """Run an RDKit-backed structure search at species-entry grain.

    :raises ValueError: 422 for sort, pagination, include, missing /
        multiple structure queries, mode-query mismatch, invalid
        structure, similarity-threshold violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    paging = Paging("/scientific/species/structure-search", request, offset=offset, limit=limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species/structure-search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    query_kind, query_value = _select_structure_query(request)
    enforce_mode_query_compatibility(request.mode, query_kind)
    threshold = _resolve_similarity_threshold(request)

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    # Dispatch to the per-mode SQL builder. Each builder runs:
    #   1. an aggregate query that returns ``{status: count}`` over the
    #      whole post-filter candidate set (used for ``total`` + the
    #      review summary, never materializes per-row data), and
    #   2. an ordered ``LIMIT/OFFSET``-bounded row query that returns at
    #      most ``limit`` rows already in the response order.
    if request.mode is StructureSearchMode.substructure:
        status_counts, page = _run_substructure_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
            visible=visible,
            paging=paging,
        )
    elif request.mode is StructureSearchMode.similarity:
        status_counts, page = _run_similarity_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
            threshold=threshold,
            visible=visible,
            paging=paging,
        )
    else:  # exact
        status_counts, page = _run_exact_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
            isomeric=request.isomeric,
            visible=visible,
            paging=paging,
        )

    visible_values = {status.value for status in visible}
    record_hidden_by_default(
        hidden_by_default_from_counts(
            {RecordReviewStatus(k): n for k, n in status_counts.items()},
            include_rejected=request.include_rejected,
            include_deprecated=request.include_deprecated,
            min_review_status=request.min_review_status,
            counted="species_entries",
        )
    )
    status_counts = {k: n for k, n in status_counts.items() if k in visible_values}
    summary = _summary_from_counts(status_counts)
    total = summary.total

    if not page.items:
        return _empty_response(
            request,
            includes,
            offset,
            limit,
            threshold,
            summary=summary,
            total=total,
        )

    entry_ids = [row.species_entry_id for row in page.items]

    # Badge load is page-scoped — the aggregate above already populated
    # the cross-page review summary, so we only need badges for the
    # rows we're actually returning.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=entry_ids,
    )

    entries = _load_entries(session, entry_ids)
    species_by_id = _load_species(
        session, [e.species_id for e in entries.values()]
    )

    records: list[ScientificSpeciesStructureSearchRecord] = []
    for row in page.items:
        eid = row.species_entry_id
        score = float(row.similarity_score) if getattr(row, "similarity_score", None) is not None else None
        entry = entries.get(eid)
        if entry is None:  # pragma: no cover — race with delete
            continue
        species = species_by_id.get(entry.species_id)
        if species is None:  # pragma: no cover — FK guarantees presence
            continue
        records.append(
            _build_record(
                entry=entry,
                species=species,
                badge=badges[eid],
                mode=request.mode,
                similarity_score=score,
                query_kind=query_kind,
                query_value=query_value,
            )
        )

    return ScientificSpeciesStructureSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request, threshold=threshold),
            mode=request.mode,
            sort=_DEFAULT_SORT_BY_MODE[request.mode],
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total, page=page
        ),
        **page.envelope(),
    )


# ---------------------------------------------------------------------------
# Query selection & validation
# ---------------------------------------------------------------------------


def _select_structure_query(
    request: ScientificSpeciesStructureSearchRequest,
) -> tuple[StructureQueryKind, str]:
    """Pick the single structure query field and return (kind, value).

    Raises ValueError → 422 if zero or more than one query field is
    supplied.
    """
    supplied: list[tuple[StructureQueryKind, str]] = []
    if request.query_smiles is not None:
        supplied.append((StructureQueryKind.smiles, request.query_smiles))
    if request.query_smarts is not None:
        supplied.append((StructureQueryKind.smarts, request.query_smarts))
    if request.query_inchi is not None:
        supplied.append((StructureQueryKind.inchi, request.query_inchi))
    if request.query_inchi_key is not None:
        supplied.append(
            (StructureQueryKind.inchi_key, request.query_inchi_key)
        )

    if not supplied:
        raise ValueError(
            "missing_structure_query: exactly one of {query_smiles, "
            "query_smarts, query_inchi, query_inchi_key} must be supplied."
        )
    if len(supplied) > 1:
        names = sorted(k.value for k, _ in supplied)
        # An *ambiguity*, which is one of the three shapes
        # ``Shape.relationship`` in ``app.api.code_catalogue`` names: the
        # request supplied more than one structure query and the code says
        # which of none. ``context['supplied']`` carries the field names --
        # the depositor's own query keys, so no row id is involved -- and
        # the query *values* are deliberately not echoed: a SMARTS pattern
        # is the caller's, but repeating it adds nothing the field name
        # does not and grows the body without bound.
        #
        # ``message_prefix=True``, so ``detail`` is byte-identical to the
        # plain ``ValueError`` this replaced; the catalogue entry moves to
        # ``Surface.coded_exception`` because ``code`` is now declared on
        # the exception rather than promoted out of the sentence.
        raise CodedValueError(
            "multiple_structure_queries",
            "exactly one structure query "
            f"field is allowed; got {names!r}.",
            context={"supplied": [f"query_{name}" for name in names]},
        )
    return supplied[0]


def _resolve_similarity_threshold(
    request: ScientificSpeciesStructureSearchRequest,
) -> float:
    """Resolve the effective similarity threshold for a request.

    For non-similarity modes the value is unused; we still return the
    default so the request echo stays consistent.
    """
    if request.similarity_threshold is not None:
        return request.similarity_threshold
    return DEFAULT_SIMILARITY_THRESHOLD


# ---------------------------------------------------------------------------
# Per-mode SQL execution
# ---------------------------------------------------------------------------


# Stored cartridge column. Reading from ``se.mol`` directly lets the
# GiST index (``ix_species_entry_mol_gist``) drive substructure /
# similarity queries instead of running ``mol_from_smiles(sp.smiles)``
# per row at request time. The write path (``species_resolution.py``)
# populates the column on insert; the d4e5f6a7b8c9 migration backfilled
# any pre-existing NULL rows and created the index. NULL ``se.mol``
# rows (e.g. unparseable SMILES) are excluded from matches in the
# ``WHERE`` clause below — a row-level fallback would defeat the index.
_STORED_MOL_EXPR = "se.mol"


# Visibility join — pulled into both the aggregate and the row query so
# the SQL builder stays DRY. ``COALESCE`` defaults missing review rows to
# ``not_reviewed`` (matching the Python ``fetch_review_badges`` default)
# so an entry with no review row participates in the visibility filter
# uniformly. The cast to ``text`` lets us bind ``visible`` as a string
# array via ``ANY(:visible_statuses)`` without juggling PG enum types.
_REVIEW_JOIN = (
    "LEFT JOIN record_review AS rr "
    "ON rr.record_type = 'species_entry'::submission_record_type "
    "AND rr.record_id = se.id"
)
_REVIEW_STATUS_EXPR = (
    "COALESCE(rr.status::text, 'not_reviewed')"
)
# review_rank ASC, generated from REVIEW_RANK in scientific_common.py (the one definition of the display rank).
_REVIEW_RANK_EXPR = review_rank_case_sql(_REVIEW_STATUS_EXPR)


def _visible_status_values(
    visible: set[RecordReviewStatus],
) -> list[str]:
    return [s.value for s in visible]


def _aggregate_status_counts(
    session: Session,
    *,
    match_expr: str,
    params: dict[str, Any],
) -> dict[str, int]:
    """Run ``GROUP BY review_status`` aggregate over the matched set.

    Returns ``{status_value: count}`` for every status that has at least
    one matching row, visible or not: the caller keeps the visible statuses
    for ``total`` and the review summary, and reports the statuses the
    default hid (``hidden_by_default``) from the same single statement.

    The query touches each matching row server-side exactly once but
    never returns per-row data — the wire result is at most five rows
    (one per ``RecordReviewStatus`` value), independent of how broad the
    cartridge match is.
    """
    sql = text(
        f"""
        SELECT
            {_REVIEW_STATUS_EXPR} AS review_status,
            COUNT(*) AS cnt
        FROM species_entry AS se
        JOIN species AS sp ON sp.id = se.species_id
        {_REVIEW_JOIN}
        WHERE {match_expr}
        GROUP BY {_REVIEW_STATUS_EXPR}
        """
    )
    rows = session.execute(sql, params).all()
    return {row.review_status: int(row.cnt) for row in rows}


def _visible_total(counts: dict[str, int], visible: set[RecordReviewStatus]) -> int:
    values = {status.value for status in visible}
    return sum(n for status, n in counts.items() if status in values)


def _summary_from_counts(counts: dict[str, int]) -> ReviewStatusSummary:
    """Project the SQL aggregate into the public review summary shape."""
    summary = ReviewStatusSummary(
        approved=counts.get(RecordReviewStatus.approved.value, 0),
        under_review=counts.get(RecordReviewStatus.under_review.value, 0),
        not_reviewed=counts.get(RecordReviewStatus.not_reviewed.value, 0),
        deprecated=counts.get(RecordReviewStatus.deprecated.value, 0),
        rejected=counts.get(RecordReviewStatus.rejected.value, 0),
    )
    summary.total = (
        summary.approved
        + summary.under_review
        + summary.not_reviewed
        + summary.deprecated
        + summary.rejected
    )
    return summary


def _bound(paging: Paging, match_expr: str, params: dict[str, Any]) -> str:
    """Keep live rows only; under a cursor, add "existed when the traversal began" to the match (and its parameter).

    A species or entry that was merged into another keeps its row (DR-0038) and forwards; the search returns
    the survivor, which holds the results, never the kept row beside it.
    """
    match_expr = (
        f"({match_expr}) AND se.merged_into_species_entry_id IS NULL AND sp.merged_into_species_id IS NULL"
    )
    if paging.resume is None:
        return match_expr
    params["watermark_id"] = paging.resume.watermark.max_id
    return f"({match_expr}) AND se.id <= :watermark_id"


def _keyset_page(
    session: Session,
    paging: Paging,
    *,
    match_expr: str,
    params: dict[str, Any],
    select_extra: str,
    keys: list[tuple[str, str, str]],
    total: int,
) -> Page:
    """One page of the matched set, ordered by ``keys`` and cut after the cursor's position.

    ``keys`` is ``(sql expression, "asc"|"desc", result column)`` in sort order, ending in the unique entry id. The
    "strictly after" predicate is the lexicographic expansion ``keyset_predicate`` builds for SQLAlchemy columns,
    written out as text because these queries are raw SQL.
    """
    base_from = f"FROM species_entry AS se JOIN species AS sp ON sp.id = se.species_id {_REVIEW_JOIN}"
    visible_clause = f"{_REVIEW_STATUS_EXPR} = ANY(:visible_statuses)"
    if paging.resume is None:
        max_id = session.execute(
            text(f"SELECT max(se.id) {base_from} WHERE {match_expr} AND {visible_clause}"), params
        ).scalar()
        paging.begin(max_id)
    where = [f"({match_expr})", visible_clause]
    bind = dict(params)
    if paging.resume is not None:
        last = paging.resume.last_values
        check_key_values(last, [float for _expr, _direction, _column in keys])  # rank, score and id are all numbers
        branches = []
        for i, (expr, direction, _column) in enumerate(keys):
            terms = [f"{keys[j][0]} = :cursor_k{j}" for j in range(i)]
            terms.append(f"{expr} {'>' if direction == 'asc' else '<'} :cursor_k{i}")
            branches.append("(" + " AND ".join(terms) + ")")
        where.append("(" + " OR ".join(branches) + ")")
        bind.update({f"cursor_k{j}": value for j, value in enumerate(last)})
    order = ", ".join(f"{expr} {direction.upper()}" for expr, direction, _column in keys)
    offset_sql = "" if paging.resume is not None else " OFFSET :row_offset"
    sql = text(
        f"""
        SELECT
            se.id AS species_entry_id,
            sp.id AS species_id,
            {select_extra}
            {_REVIEW_STATUS_EXPR} AS review_status,
            {_REVIEW_RANK_EXPR} AS review_rank
        {base_from}
        WHERE {" AND ".join(where)}
        ORDER BY {order}
        LIMIT :row_limit{offset_sql}
        """
    )
    bind["row_limit"] = paging.limit + 1
    if paging.resume is None:
        bind["row_offset"] = paging.offset
    if paging.resume is None:
        rows = list(session.execute(sql, bind).all())
    else:
        try:
            with session.begin_nested():
                rows = list(session.execute(sql, bind).all())
        except DBAPIError as exc:
            raise InvalidCursorError("invalid_cursor: the cursor payload is malformed.") from exc
    more = len(rows) > paging.limit
    rows = rows[: paging.limit]
    last_values = [getattr(rows[-1], column) for _e, _d, column in keys] if rows else None
    return paging.finish(rows, more=more, last_values=last_values, total=total)


def _run_substructure_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
    visible: set[RecordReviewStatus],
    paging: Paging,
) -> tuple[dict[str, int], Page]:
    """Bounded substructure search.

    Returns ``(status_counts, page_rows)``. ``page_rows`` is
    ``LIMIT/OFFSET``-bounded and already ordered
    (``review_rank ASC, species_entry_id DESC``). ``status_counts``
    spans the full post-filter set and is used to derive the exact total
    and review summary. The cartridge ``@>`` operator evaluates the
    substructure containment test database-side; we never iterate
    Python-side over species.
    """
    if query_kind is StructureQueryKind.smarts:
        # Parse client-side to surface a 422 before issuing SQL.
        parse_smarts(query_value)
        query_expr = "qmol_from_smarts(:query_text)"
        bound_value = query_value
    elif query_kind is StructureQueryKind.smiles:
        bound_value = parse_smiles_to_canonical(query_value)
        query_expr = "mol_from_smiles(:query_text)"
    else:  # pragma: no cover — guarded by enforce_mode_query_compatibility
        raise ValueError(
            "invalid_structure_query: substructure mode requires "
            "query_smiles or query_smarts."
        )

    match_expr = (
        f"{_STORED_MOL_EXPR} IS NOT NULL "
        f"AND {_STORED_MOL_EXPR} @> {query_expr}"
    )
    params: dict[str, Any] = {
        "query_text": bound_value,
        "visible_statuses": _visible_status_values(visible),
    }
    match_expr = _bound(paging, match_expr, params)
    counts = _aggregate_status_counts(
        session, match_expr=match_expr, params=params
    )
    page = _keyset_page(
        session,
        paging,
        match_expr=match_expr,
        params=params,
        select_extra="",
        keys=[(_REVIEW_RANK_EXPR, "asc", "review_rank"), ("se.id", "desc", "species_entry_id")],
        total=_visible_total(counts, visible),
    )
    return counts, page


def _run_similarity_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
    threshold: float,
    visible: set[RecordReviewStatus],
    paging: Paging,
) -> tuple[dict[str, int], Page]:
    """Bounded similarity search.

    Returns ``(status_counts, page_rows)`` where ``page_rows`` is sorted
    by ``similarity_score DESC, review_rank ASC, species_entry_id DESC``
    and bounded by ``LIMIT/OFFSET``. Uses
    ``tanimoto_sml(morganbv_fp(stored_mol), morganbv_fp(query_mol))``;
    the threshold filter is applied database-side via ``>= :threshold``.
    """
    if query_kind is StructureQueryKind.smiles:
        canonical_smiles = parse_smiles_to_canonical(query_value)
    elif query_kind is StructureQueryKind.inchi:
        canonical_smiles = parse_inchi_to_canonical_smiles(query_value)
    else:  # pragma: no cover — guarded by enforce_mode_query_compatibility
        raise ValueError(
            "invalid_structure_query: similarity mode requires "
            "query_smiles or query_inchi."
        )

    score_expr = (
        f"tanimoto_sml(morganbv_fp({_STORED_MOL_EXPR}), "
        f"morganbv_fp(mol_from_smiles(:query_text)))"
    )
    match_expr = (
        f"{_STORED_MOL_EXPR} IS NOT NULL "
        f"AND {score_expr} >= :threshold"
    )
    params: dict[str, Any] = {
        "query_text": canonical_smiles,
        "threshold": threshold,
        "visible_statuses": _visible_status_values(visible),
    }
    match_expr = _bound(paging, match_expr, params)
    counts = _aggregate_status_counts(
        session, match_expr=match_expr, params=params
    )
    page = _keyset_page(
        session,
        paging,
        match_expr=match_expr,
        params=params,
        select_extra=f"{score_expr} AS similarity_score,",
        keys=[
            (score_expr, "desc", "similarity_score"),
            (_REVIEW_RANK_EXPR, "asc", "review_rank"),
            ("se.id", "desc", "species_entry_id"),
        ],
        total=_visible_total(counts, visible),
    )
    return counts, page


def _run_exact_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
    isomeric: bool = False,
    visible: set[RecordReviewStatus],
    paging: Paging,
) -> tuple[dict[str, int], Page]:
    """Bounded exact-match by canonical InChIKey.

    Computes the query's InChIKey via RDKit and matches against the
    indexed ``species.inchi_key`` column — no cartridge call required
    for the actual lookup. SMARTS is rejected upstream by the mode/query
    compatibility check.
    """
    target_key = inchi_key_from_query(query_kind, query_value)
    match_expr = "sp.inchi_key = :inchi_key"
    params: dict[str, Any] = {
        "inchi_key": target_key,
        "visible_statuses": _visible_status_values(visible),
    }
    if isomeric:
        # The species key is stereo-blind; narrow to the entries whose stereo
        # descriptor is the one the query's marks name. A query with no stereo
        # element (or no marks to read) leaves the species-level match alone.
        descriptor = entry_stereo_descriptor_of_query(query_kind, query_value)
        if descriptor is not None:
            match_expr += " AND se.stereo_descriptor = :stereo_descriptor"
            params["stereo_descriptor"] = descriptor
    match_expr = _bound(paging, match_expr, params)
    counts = _aggregate_status_counts(
        session, match_expr=match_expr, params=params
    )
    page = _keyset_page(
        session,
        paging,
        match_expr=match_expr,
        params=params,
        select_extra="",
        keys=[(_REVIEW_RANK_EXPR, "asc", "review_rank"), ("se.id", "desc", "species_entry_id")],
        total=_visible_total(counts, visible),
    )
    return counts, page


# ---------------------------------------------------------------------------
# Loading + record building
# ---------------------------------------------------------------------------


def _load_entries(
    session: Session, entry_ids: list[int]
) -> dict[int, SpeciesEntry]:
    if not entry_ids:
        return {}
    rows = (
        session.query(SpeciesEntry)
        .filter(SpeciesEntry.id.in_(entry_ids))
        .all()
    )
    return {e.id: e for e in rows}


def _load_species(
    session: Session, species_ids: list[int]
) -> dict[int, Species]:
    if not species_ids:
        return {}
    rows = (
        session.query(Species).filter(Species.id.in_(species_ids)).all()
    )
    return {s.id: s for s in rows}


def _build_record(
    *,
    entry: SpeciesEntry,
    species: Species,
    badge: RecordReviewBadge,
    mode: StructureSearchMode,
    similarity_score: float | None,
    query_kind: StructureQueryKind,
    query_value: str,
) -> ScientificSpeciesStructureSearchRecord:
    return ScientificSpeciesStructureSearchRecord(
        species_ref=species.public_ref,
        species_id=species.id,
        species_entry_ref=entry.public_ref,
        species_entry_id=entry.id,
        smiles=species.smiles,
        inchi_key=species.inchi_key,
        charge=species.charge,
        multiplicity=species.multiplicity,
        stereo_kind=species.stereo_kind,
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
        # Read off the already-loaded entity; a NULL column stays None
        # rather than becoming "". Without these, two entries of one
        # species come back as two byte-identical rows differing only in
        # an opaque ref.
        stereo_label=entry.stereo_label,
        stereo_descriptor=entry.stereo_descriptor,
        stereo_resolution=entry.stereo_resolution,
        stereo_smiles=entry.stereo_smiles,
        stereo_not_represented=(
            list(unrepresented_stereo_kinds(species.smiles))
            if entry.stereo_descriptor is not None and "?" in entry.stereo_descriptor
            else []
        ),
        electronic_state_label=entry.electronic_state_label,
        term_symbol=entry.term_symbol,
        isotope_key=entry.isotope_key,
        species_entry_label=species_entry_label_for(entry),
        match=StructureMatchSummary(
            mode=mode,
            similarity_score=similarity_score,
            matched_query=query_value,
            matched_query_kind=query_kind,
        ),
        review=badge,
        endpoint=f"/api/v1/scientific/species-entries/{entry.public_ref}",
    )


# ---------------------------------------------------------------------------
# Echo + empty
# ---------------------------------------------------------------------------


def _request_filter_echo(
    request: ScientificSpeciesStructureSearchRequest,
    *,
    threshold: float,
) -> dict[str, Any]:
    echo: dict[str, Any] = {}
    for name in (
        "query_smiles",
        "query_smarts",
        "query_inchi",
        "query_inchi_key",
    ):
        value = getattr(request, name)
        if value is not None:
            echo[name] = value
    if request.mode is StructureSearchMode.similarity:
        # Always echo the effective threshold for similarity searches so
        # callers see the value they (implicitly or explicitly) ran with.
        echo["similarity_threshold"] = threshold
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: ScientificSpeciesStructureSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
    threshold: float,
    *,
    summary: ReviewStatusSummary | None = None,
    total: int = 0,
) -> ScientificSpeciesStructureSearchResponse:
    return ScientificSpeciesStructureSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request, threshold=threshold),
            mode=request.mode,
            sort=_DEFAULT_SORT_BY_MODE[request.mode],
            include=sorted(includes),
        ),
        review_summary=summary if summary is not None else ReviewStatusSummary(),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=total
        ),
    )


__all__ = ["search_species_by_structure"]
