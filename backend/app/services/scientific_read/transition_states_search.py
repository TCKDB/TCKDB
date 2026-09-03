"""Service implementation for /api/v1/scientific/transition-states/search.

Returns records at the transition-state-entry grain (one record per
``transition_state_entry`` row that matches the filter set). Shares the
``ScientificTransitionStateEntryRecord`` shape with the TS-entry detail
endpoint so search and detail callers can parse responses with one set
of code.

Composition mirrors the calculations-search service:

1. Validate include / sort / pagination via shared helpers.
2. Reject the empty-filter request with 422 ``missing_filter``.
3. Resolve owner/parent refs to integer ids (422 on malformed /
   wrong-prefix refs; empty short-circuit on unknown refs).
4. Build the candidate SQL query joining ``transition_state_entry`` to
   its parent ``transition_state``, ``reaction_entry``, and the
   ``calculation`` evidence tables.
5. Bulk-load review badges; apply the visible-statuses gate.
6. Sort deterministically (review rank → created_at desc → id desc).
7. Slice for pagination, then materialize each page row via the shared
   :func:`build_entry_record` helper.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionFamily,
    ReactionParticipant,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
    SoftwareReleaseSummary,
)
from app.schemas.reads.scientific_structure_search import StructureQueryKind
from app.schemas.reads.scientific_transition_state import (
    ScientificTransitionStateEntryRecord,
)
from app.schemas.reads.scientific_transition_state_search import (
    RequestEcho,
    ScientificTransitionStatesBrowseResponse,
    ScientificTransitionStatesSearchResponse,
    TransitionStatesBrowseRequest,
    TransitionStatesSearchRequest,
)
from app.services.scientific_read import levels_of_theory
from app.services.scientific_read.calculation_provenance_filters import (
    apply_calculation_provenance_filter,
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
    resolve_filter_ref,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.structure_query import (
    inchi_key_from_query,
)
from app.services.scientific_read.transition_states import (
    _INTERNAL_INCLUDE_TOKENS as _CONCEPT_INTERNAL_INCLUDE_TOKENS,
)
from app.services.scientific_read.transition_states import (
    _LEGAL_INCLUDE_TOKENS as _CONCEPT_LEGAL_INCLUDE_TOKENS,
)
from app.services.scientific_read.transition_states import (
    TRANSITION_STATE_ENTRY_TRUST_EAGER_LOADS,
    _build_reaction_context,
    _build_saddle_point_index,
    _build_ts_core_block,
    build_entry_record,
    build_sibling_entry_records,
)

# ``trust`` is legal on this surface, and it was not always. The search
# response has always carried ``records[*].trust`` — at the record root
# here, unlike the thermo and kinetics search twins — while rejecting
# ``include=trust`` with ``422 unknown_include_token``, so the field was
# permanently ``null`` on all 34 records of an observed page. The token is
# legal now and the route drops the key when the caller did not ask.
#
# It is **internal-tokenized**, so ``include=all`` never expands to it.
# That matters most here: of the five search surfaces this change touches,
# this one carries the largest eager-load chain (23 entries), the only
# four-hop chain, and the only one rooted on a *collection*
# (``TransitionStateEntry.calculations``) rather than a scalar
# relationship. ``_materialize_records`` applies it to the page query so
# the cost is per page, not per record.
#
# ``entries`` is internal-tokenized on *this* surface too, and only here.
# Its cost follows the fan-out under the page's parents rather than the
# page: a record's block is every entry of its transition state, and each
# of those is a full record build. Measured on a 20-entry parent it is 162
# statements — flat in ``limit``, because the block is resolved once per
# distinct parent, but not flat in how many entries those parents have. It
# was previously *discarded* on this surface, so ``include=all`` never paid
# it; keeping it out of the expansion means ``include=all`` here returns
# exactly what it returned before, and a caller who wants the sibling lists
# asks for them. It stays public on the two detail surfaces, where the
# block is bounded by one record's parent.
_LEGAL_INCLUDE_TOKENS: set[str] = _CONCEPT_LEGAL_INCLUDE_TOKENS | {"trust"}
_INTERNAL_INCLUDE_TOKENS: set[str] = _CONCEPT_INTERNAL_INCLUDE_TOKENS | {
    "trust",
    "entries",
}

# Filter knobs that count as "meaningful" for the at-least-one-filter rule.
# Pure pagination / include / review knobs are deliberately excluded.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "reaction_ref",
    "reaction_entry_ref",
    "transition_state_ref",
    "transition_state_entry_ref",
    "status",
    "charge",
    "multiplicity",
    "has_calculations",
    "has_opt",
    "has_freq",
    "has_sp",
    "has_irc",
    "has_path_search",
    "has_geometry_validation",
    "has_scf_stability",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "family",
    "participant_smiles",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_transition_states(
    session: Session, request: TransitionStatesSearchRequest
) -> ScientificTransitionStatesSearchResponse:
    """Multi-axis transition-state-entry search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/transition-states/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)
    # ``include=entries`` used to be discarded here, on the reading that a
    # record already *is* an entry so the token had nothing to say. It had:
    # the parent transition state's other entries, which an entry-grained
    # search cannot otherwise report. Discarding it also dropped the token
    # from ``request.include``, so the echo told the caller their request
    # had been understood as something they did not send.

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    reaction_id, short_circuit = _resolve_filter_ref(
        session, ChemReaction, request.reaction_ref, "reaction"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    reaction_entry_id, short_circuit = _resolve_filter_ref(
        session, ReactionEntry, request.reaction_entry_ref, "reaction_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    ts_id, short_circuit = _resolve_filter_ref(
        session, TransitionState, request.transition_state_ref,
        "transition_state",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    tse_id, short_circuit = _resolve_filter_ref(
        session,
        TransitionStateEntry,
        request.transition_state_entry_ref,
        "transition_state_entry",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(
        TransitionStateEntry.id,
        TransitionStateEntry.created_at,
    )
    stmt = _apply_parent_filters(
        stmt,
        reaction_id=reaction_id,
        reaction_entry_id=reaction_entry_id,
        ts_id=ts_id,
        tse_id=tse_id,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)
    stmt = _apply_reaction_context_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    # --- review filter ------------------------------------------------------
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_response(request, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)

    # --- deterministic sort -------------------------------------------------
    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )

    total = len(visible_ids)
    page_ids = visible_ids[offset : offset + limit]

    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificTransitionStatesSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


def browse_transition_states(
    session: Session, request: TransitionStatesBrowseRequest
) -> ScientificTransitionStatesBrowseResponse:
    """List transition-state entries by secondary filter alone -- no identifier required.

    See ``/scientific/transition-states/browse``. This is the identifier-free
    catalogue read that :func:`search_transition_states` deliberately
    cannot serve: that function's :func:`_enforce_at_least_one_filter`
    rejects an empty query with 422 ``missing_filter`` to keep an
    accidental unbounded scan from being possible on a route whose other
    callers rely on that guard. Relaxing the guard in place would make one
    route mean two different things depending on which query parameters
    happened to be present; a bounded, paged "what does the archive hold"
    listing is a different question with a different request shape
    (:class:`TransitionStatesBrowseRequest`, which structurally cannot
    carry the owner/parent ref filters that make search a lookup), so it
    gets its own function and its own route, sibling to ``/search`` rather
    than a flag on it.

    Everything downstream of "which candidate TS entries" is shared with
    :func:`search_transition_states` verbatim: the same scalar / evidence /
    method-basis-software filter builders (:func:`_apply_scalar_filters`,
    :func:`_apply_evidence_filters`, :func:`_apply_method_basis_software_filters`),
    the same review-visibility gate (:func:`app.services.scientific_read.common.visible_statuses`),
    the same ``review_rank ASC, created_at DESC, id DESC`` order, the same
    bounded page size, and the same per-page record builder
    (:func:`_materialize_records`) -- so a browse record and a search
    record are byte-identical in shape.

    Only the candidate set differs, and only by omission:
    :func:`_apply_parent_filters` (the four owner/parent ref filters) is
    never called here, because :class:`TransitionStatesBrowseRequest` has
    no ref fields to read one from. With every optional filter absent
    (the default, empty browse request) the candidate set is every
    ``transition_state_entry`` row in the corpus -- the whole point of the
    endpoint, and the 34-row live-archive gap this endpoint exists to
    close.

    :param session: SQLAlchemy session bound to the read DB.
    :param request: Parsed request model.
    :returns: ``ScientificTransitionStatesBrowseResponse`` Pydantic model.
    :raises ValueError: 422 for sort/pagination/include validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/transition-states/browse",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # --- candidate query ----------------------------------------------------
    # No _apply_parent_filters() call: TransitionStatesBrowseRequest carries
    # no reaction_ref / reaction_entry_ref / transition_state_ref /
    # transition_state_entry_ref, so there is nothing to join or filter on
    # for owner/parent scope. See the docstring above.
    stmt = select(
        TransitionStateEntry.id,
        TransitionStateEntry.created_at,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)
    stmt = _apply_reaction_context_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    if not candidate_ids:
        return _empty_browse_response(request, includes, offset, limit)

    # --- review filter ------------------------------------------------------
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_browse_response(request, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)

    # --- deterministic sort -------------------------------------------------
    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )

    total = len(visible_ids)
    page_ids = visible_ids[offset : offset + limit]

    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificTransitionStatesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


# ---------------------------------------------------------------------------
# Filter rule + ref resolution
# ---------------------------------------------------------------------------


def _enforce_at_least_one_filter(
    request: TransitionStatesSearchRequest,
) -> None:
    """Reject requests with no meaningful filter.

    Bool filters in :class:`TransitionStatesSearchRequest` default to
    ``None``; an explicit ``False`` from the caller is a meaningful
    filter (e.g. ``has_opt=false`` selects TS entries *without* opt
    evidence), so only ``None`` skips here. ``include_rejected`` and
    friends are not in ``_MEANINGFUL_FILTER_FIELDS``, so their
    ``bool = False`` defaults can't accidentally satisfy the gate.
    """
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/transition-states/search."
    )


def _resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    """Resolve an optional ``*_ref`` filter to (resolved_id, short_circuit)."""
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(
        session, model_cls, ref, kind_label=kind_label
    )
    if resolved is None:
        return None, True
    return resolved, False


# ---------------------------------------------------------------------------
# WHERE-clause builders
# ---------------------------------------------------------------------------


def _apply_parent_filters(
    stmt,
    *,
    reaction_id: int | None,
    reaction_entry_id: int | None,
    ts_id: int | None,
    tse_id: int | None,
):
    if tse_id is not None:
        stmt = stmt.where(TransitionStateEntry.id == tse_id)
    if ts_id is not None:
        stmt = stmt.where(TransitionStateEntry.transition_state_id == ts_id)
    if reaction_entry_id is not None:
        stmt = stmt.join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        ).where(TransitionState.reaction_entry_id == reaction_entry_id)
    elif reaction_id is not None:
        # Narrow to entries whose TS belongs to a reaction_entry that
        # belongs to the given chem_reaction.
        stmt = (
            stmt.join(
                TransitionState,
                TransitionState.id
                == TransitionStateEntry.transition_state_id,
            )
            .join(
                ReactionEntry,
                ReactionEntry.id == TransitionState.reaction_entry_id,
            )
            .where(ReactionEntry.reaction_id == reaction_id)
        )
    return stmt


def _apply_scalar_filters(
    stmt, request: TransitionStatesSearchRequest | TransitionStatesBrowseRequest
):
    if request.status is not None:
        stmt = stmt.where(TransitionStateEntry.status == request.status)
    if request.charge is not None:
        stmt = stmt.where(TransitionStateEntry.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(
            TransitionStateEntry.multiplicity == request.multiplicity
        )
    return stmt


def _apply_evidence_filters(
    stmt, request: TransitionStatesSearchRequest | TransitionStatesBrowseRequest
):
    if request.has_calculations is not None:
        ex = exists().where(
            Calculation.transition_state_entry_id == TransitionStateEntry.id
        )
        stmt = stmt.where(ex if request.has_calculations else ~ex)

    type_filters: list[tuple[bool | None, CalculationType]] = [
        (request.has_opt, CalculationType.opt),
        (request.has_freq, CalculationType.freq),
        (request.has_sp, CalculationType.sp),
        (request.has_irc, CalculationType.irc),
        (request.has_path_search, CalculationType.path_search),
    ]
    for want, calc_type in type_filters:
        if want is None:
            continue
        ex = exists().where(
            and_(
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
                Calculation.type == calc_type,
            )
        )
        stmt = stmt.where(ex if want else ~ex)

    if request.has_geometry_validation is not None:
        ex = exists().where(
            and_(
                CalculationGeometryValidation.calculation_id
                == Calculation.id,
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
            )
        )
        stmt = stmt.where(ex if request.has_geometry_validation else ~ex)

    if request.has_scf_stability is not None:
        ex = exists().where(
            and_(
                CalculationSCFStability.calculation_id == Calculation.id,
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
            )
        )
        stmt = stmt.where(ex if request.has_scf_stability else ~ex)

    return stmt


def _apply_method_basis_software_filters(
    stmt, request: TransitionStatesSearchRequest | TransitionStatesBrowseRequest
):
    """Method/basis/software/workflow filters narrow TS entries to those
    whose calculation evidence includes a row matching the supplied
    provenance. The match is an OR-across-calc set: a TS entry passes
    if at least one of its calculations matches.

    Delegates the join/predicate construction to
    :func:`app.services.scientific_read.calculation_provenance_filters.apply_calculation_provenance_filter`,
    which ``/species/browse`` also uses — see that module's docstring for
    why the extraction is behaviour-preserving here.
    """
    return apply_calculation_provenance_filter(
        stmt,
        request,
        select(Calculation.id).where(
            Calculation.transition_state_entry_id == TransitionStateEntry.id
        ),
    )


def _apply_reaction_context_filters(
    stmt, request: TransitionStatesSearchRequest | TransitionStatesBrowseRequest
):
    """``family`` and ``participant_smiles`` narrow by the *reaction*, not
    the TS entry -- see :class:`TransitionStatesBrowseRequest`'s docstring
    for why a TS entry, which has no molecular graph of its own, can still
    be filtered structurally through the reaction it belongs to.

    Both are correlated ``EXISTS`` predicates against
    ``TransitionStateEntry.transition_state_id``, matching the shape every
    other optional filter in this module uses -- additive, AND-combined in
    the same statement, never a second round trip.

    An empty string is treated the same as ``None`` (no filter) for both
    fields, not as a value to match against. Neither field has a
    ``min_length`` constraint, so ``?participant_smiles=`` (present, empty)
    reaches here as ``""`` rather than being rejected up front; without
    this guard RDKit parses ``""`` as a valid *empty* molecule (it does not
    return ``None``, so the exact-match branch below would not 422 either)
    and computes a real InChIKey for it, which matches nothing in the
    corpus -- silently narrowing a request that supplied no meaningful
    SMILES into a request that matches zero rows. ``family=""`` has the
    same failure shape for the same reason: no seeded family is named the
    empty string, so it would silently narrow rather than silently no-op.
    """
    if request.family:
        ex = exists().where(
            and_(
                TransitionState.id == TransitionStateEntry.transition_state_id,
                TransitionState.reaction_entry_id == ReactionEntry.id,
                ReactionEntry.reaction_id == ChemReaction.id,
                ChemReaction.reaction_family_id == ReactionFamily.id,
                ReactionFamily.name == request.family,
            )
        )
        stmt = stmt.where(ex)

    if request.participant_smiles:
        target_key = inchi_key_from_query(
            StructureQueryKind.smiles,
            request.participant_smiles,
            field_name="participant_smiles",
        )
        ex = exists().where(
            and_(
                TransitionState.id == TransitionStateEntry.transition_state_id,
                TransitionState.reaction_entry_id == ReactionEntry.id,
                ReactionParticipant.reaction_id == ReactionEntry.reaction_id,
                Species.id == ReactionParticipant.species_id,
                Species.inchi_key == target_key,
            )
        )
        stmt = stmt.where(ex)

    return stmt


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificTransitionStateEntryRecord]:
    """Materialize TS-entry records via the shared per-entry builder."""
    if not page_ids:
        return []
    # Bulk-load entries, parent TS rows, and TS review badges. Each page
    # is small (limit <= 200), so per-entry parent lookups are bounded.
    entry_stmt = select(TransitionStateEntry).where(
        TransitionStateEntry.id.in_(page_ids)
    )
    if "trust" in includes:
        # The chain the trust evaluator walks: 23 entries, four hops deep,
        # rooted on a collection. Applied here, as options on the page
        # query, ``selectinload`` issues a fixed number of statements for
        # the whole batch. Left to the per-record builder it would be that
        # many lazy loads *per record*, which on the largest observed page
        # (34 records) is the N+1 the old search vocabulary avoided by
        # refusing the token outright. This is the load-bearing line of the
        # whole trust-on-search change.
        entry_stmt = entry_stmt.options(*TRANSITION_STATE_ENTRY_TRUST_EAGER_LOADS)
    entries = session.scalars(entry_stmt).all()
    entry_by_id = {e.id: e for e in entries}

    ts_ids = {e.transition_state_id for e in entries}
    ts_rows = session.scalars(
        select(TransitionState).where(TransitionState.id.in_(ts_ids))
    ).all()
    ts_by_id = {t.id: t for t in ts_rows}

    ts_badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.transition_state,
            record_ids=list(ts_ids),
        )
        if ts_ids
        else {}
    )

    # Cache reaction context per reaction_entry_id to avoid re-querying
    # the participants when several TS entries share a parent.
    reaction_cache: dict[int | None, Any] = {}

    # Levels of theory for the whole page in one grouped statement. Left to
    # the per-record builder this is a statement per record -- the exact
    # slope ``test_record_builder_statement_cost.py`` exists to catch, and
    # the reason the block is worth resolving here rather than gating it
    # behind an include token nobody would send.
    levels_index = levels_of_theory.for_transition_state_entries(
        session, page_ids
    )

    # Same bargain, for ``saddle_point``: one statement for the whole page
    # rather than one per record. See ``_build_saddle_point_index``'s own
    # docstring — this is the exact regression
    # ``test_the_saddle_point_map_costs_a_ts_search_page_one_statement``
    # exists to catch (measured before this fix: +1 statement per record,
    # 169 → 189 at ``limit=20``).
    saddle_point_index = _build_saddle_point_index(session, page_ids)

    # Software attributed to the whole page's calculations, one grouped
    # statement, same cost discipline as ``levels_index`` above. Deliberately
    # NOT threaded into ``build_entry_record`` (that shared builder is also
    # the TS-entry-detail code path) -- applied here, after the record is
    # built, so this surface's evidence-summary addition cannot perturb
    # detail's construction of the same shared schema.
    software_index = _software_index_for_entries(session, page_ids)

    # ``include=entries`` gives every record its parent's entry list. Built
    # once per distinct parent on the page and shared, because search pages
    # cluster: several entries of one transition state routinely match the
    # same filter, and resolving the same sibling list once per record is
    # the shape this module's docstring calls the N+1 trap.
    entries_blocks: dict[int, list[ScientificTransitionStateEntryRecord]] = {}

    records: list[ScientificTransitionStateEntryRecord] = []
    for cid in page_ids:
        entry = entry_by_id.get(cid)
        if entry is None:  # pragma: no cover — race with delete
            continue
        ts = ts_by_id.get(entry.transition_state_id)
        if ts is None:  # pragma: no cover — FK guarantees existence
            continue
        ts_badge = ts_badges.get(
            ts.id,
            RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
        )
        ts_core = _build_ts_core_block(ts, ts_badge)
        re_id = ts.reaction_entry_id
        if re_id not in reaction_cache:
            reaction_cache[re_id] = _build_reaction_context(session, re_id)
        reaction = reaction_cache[re_id]
        entries_block: list[ScientificTransitionStateEntryRecord] | None = None
        if "entries" in includes:
            if ts.id not in entries_blocks:
                entries_blocks[ts.id] = build_sibling_entry_records(
                    session,
                    entry=entry,
                    ts_core=ts_core,
                    reaction=reaction,
                    includes=includes,
                )
            entries_block = entries_blocks[ts.id]
        record = build_entry_record(
            session,
            entry=entry,
            ts_core=ts_core,
            reaction=reaction,
            entry_badge=badges[cid],
            includes=includes,
            entries_block=entries_block,
            levels_index=levels_index,
            saddle_point_index=saddle_point_index,
        )
        software_map = software_index.get(cid, {})
        if software_map:
            record = record.model_copy(
                update={
                    "evidence_summary": record.evidence_summary.model_copy(
                        update={"software": software_map}
                    )
                }
            )
        records.append(record)
    return records


def _software_index_for_entries(
    session: Session, entry_ids: list[int]
) -> dict[int, dict[str, list[SoftwareReleaseSummary]]]:
    """Software (release) actually attributed to a page's TS entries.

    Mirrors :func:`levels_of_theory.for_transition_state_entries`'s shape
    and cost contract (one grouped statement for the whole page, an
    outer join so a calculation naming no ``software_release`` still puts
    its *type* in the map with an empty list rather than dropping the
    type entirely) -- see that module's docstring for the full argument.
    Kept local to this module rather than folded into ``levels_of_theory``
    because it serves one caller (this file's own materializer) and the
    shared module's ``for_*``/``merged`` machinery is built around
    :class:`LevelOfTheorySummary`, not :class:`SoftwareReleaseSummary`.

    :returns: ``transition_state_entry_id`` -> calculation type -> distinct
        software releases named by that entry's calculations of that type.
    """
    if not entry_ids:
        return {}
    stmt = (
        select(
            Calculation.transition_state_entry_id.label("owner_id"),
            Calculation.type,
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .select_from(Calculation)
        .outerjoin(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
        )
        .outerjoin(Software, Software.id == SoftwareRelease.software_id)
        .where(Calculation.transition_state_entry_id.in_(entry_ids))
        .distinct()
    )

    collected: dict[int, dict[str, dict[int, SoftwareReleaseSummary]]] = {}
    for row in session.execute(stmt):
        per_owner = collected.setdefault(row.owner_id, {})
        type_key = row.type.value if hasattr(row.type, "value") else str(row.type)
        bucket = per_owner.setdefault(type_key, {})
        if row.id is None:
            continue
        bucket[row.id] = SoftwareReleaseSummary(
            software_release_id=row.id,
            software_release_ref=row.public_ref,
            software=row.name,
            version=row.version,
        )

    return {
        owner_id: {
            key: sorted(
                bucket.values(),
                key=lambda s: (s.software, s.version or "", s.software_release_id),
            )
            for key, bucket in per_owner.items()
        }
        for owner_id, per_owner in collected.items()
    }


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: TransitionStatesSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificTransitionStatesSearchResponse:
    return ScientificTransitionStatesSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _request_filter_echo(request: TransitionStatesSearchRequest) -> dict[str, Any]:
    """Return the caller's filter inputs verbatim (post-parse)."""
    out: dict[str, Any] = {}
    for name in (*_MEANINGFUL_FILTER_FIELDS, "include_rejected", "include_deprecated", "min_review_status"):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


# Filter fields shared by browse and search, minus the four owner/parent
# ref filters (``reaction_ref`` etc.) that :class:`TransitionStatesBrowseRequest`
# does not have attributes for. Duplicated from the tail of
# ``_MEANINGFUL_FILTER_FIELDS`` rather than sliced from it, so a future
# addition to one tuple cannot silently resize the other.
_BROWSE_FILTER_FIELDS: tuple[str, ...] = (
    "status",
    "charge",
    "multiplicity",
    "has_calculations",
    "has_opt",
    "has_freq",
    "has_sp",
    "has_irc",
    "has_path_search",
    "has_geometry_validation",
    "has_scf_stability",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "family",
    "participant_smiles",
)


def _browse_filter_echo(request: TransitionStatesBrowseRequest) -> dict[str, Any]:
    """:func:`_request_filter_echo`'s counterpart for browse.

    Not a call to ``_request_filter_echo`` with the ref fields absent:
    :class:`TransitionStatesBrowseRequest` has no ``reaction_ref`` /
    ``reaction_entry_ref`` / ``transition_state_ref`` /
    ``transition_state_entry_ref`` attributes to read, so
    ``getattr(request, "reaction_ref")`` would raise rather than quietly
    return ``None``. Same ``value is None`` gate as
    :func:`_request_filter_echo` -- ``include_rejected`` /
    ``include_deprecated`` are always echoed (they default ``False``, never
    ``None``), matching the search sibling's existing wire behaviour.
    """
    out: dict[str, Any] = {}
    for name in (
        *_BROWSE_FILTER_FIELDS,
        "include_rejected",
        "include_deprecated",
        "min_review_status",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


def _empty_browse_response(
    request: TransitionStatesBrowseRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificTransitionStatesBrowseResponse:
    return ScientificTransitionStatesBrowseResponse(
        request=RequestEcho(
            filter=_browse_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


__all__ = [
    "browse_transition_states",
    "search_transition_states",
]
