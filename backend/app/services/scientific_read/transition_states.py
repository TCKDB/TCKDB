"""Service implementations for the scientific transition-state read surface.

Two detail surfaces and one search surface:

- ``GET /scientific/transition-states/{ts_ref_or_id}`` — one TS concept.
- ``GET /scientific/transition-state-entries/{tse_ref_or_id}`` — one TS entry.
- ``GET/POST /scientific/transition-states/search`` — TS-entry-grain search.

The TS detail endpoint returns the parent-concept record. The TS entry
detail endpoint returns the concrete-entry record. The search endpoint
returns concrete-entry records — same per-record shape as the TS-entry
detail surface — because entries are the rows that carry status /
charge / multiplicity / calculation evidence.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import not_found
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSCFStability,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationRecordKind,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionEntry, ReactionFamily
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
    TransitionStateValidationEvidence,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_calculation import (
    CalculationGeometryLinkSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_transition_state import (
    AvailableTransitionStateSections,
    RequestEcho,
    ScientificTransitionStateDetailResponse,
    ScientificTransitionStateEntryDetailResponse,
    ScientificTransitionStateEntryRecord,
    ScientificTransitionStateRecord,
    TransitionStateCalculationSummary,
    TransitionStateCoreBlock,
    TransitionStateDetailRequest,
    TransitionStateEntriesSummary,
    TransitionStateEntryCoreBlock,
    TransitionStateEntryDetailRequest,
    TransitionStateEntryEvidenceSummary,
    TransitionStateEntryValidationEvidence,
    TransitionStateEvidenceCoverage,
    TransitionStateEvidenceSummary,
    TransitionStateReactionContext,
    TransitionStateReviewEntry,
    TransitionStateSaddlePointEvidence,
    TransitionStateValidationDescriptor,
    TransitionStateValidationEvidenceSummary,
)
from app.services.scientific_read import levels_of_theory
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import (
    resolve_transition_state_entry_handle,
    resolve_transition_state_handle,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species_calculations_search import (
    _build_energy_block,
    _load_sp_lot_pairs,
)
from app.services.trust import (
    TrustFragment,
    build_trust_fragment,
    evaluate_loaded_transition_state_entry,
)

# ---------------------------------------------------------------------------
# Include policy
# ---------------------------------------------------------------------------


# Heavy include tokens shared between the TS and TS-entry detail surfaces.
# ``entries`` is meaningful only on the TS-concept surface; passing it on
# the TS-entry surface is silently a no-op (the entry is implicitly the
# record), but it is still listed as legal so a generic client can pass
# the same include set to both endpoints.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "entries",
    "calculations",
    "geometries",
    "review",
    "validation_evidence",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# ``trust`` is legal on the two **entry-grained** surfaces: the standalone
# TS-entry detail endpoint and ``/transition-states/search``, whose search
# vocabulary lives in ``transition_states_search`` and adds the token to
# this set. The parent-TS **concept** surface keeps the set below and still
# answers 422 ``unknown_include_token`` — a concept is a collection of
# entries evaluated at different levels of theory, and one trust verdict
# for the collection would be an aggregation, not a reading.
#
# On both surfaces where it is legal, ``trust`` is internal-tokenized, so
# ``include=all`` does not pull it in: the evaluator walks a 23-entry
# eager-load graph and a caller must ask for that by name.
_TSE_DETAIL_LEGAL_INCLUDE_TOKENS: set[str] = _LEGAL_INCLUDE_TOKENS | {"trust"}
_TSE_DETAIL_INTERNAL_INCLUDE_TOKENS: set[str] = _INTERNAL_INCLUDE_TOKENS | {
    "trust"
}


# Eager-load graph required by ``computed_transition_state_v2``. Mirrors
# the load plan inside
# :func:`app.services.trust.evaluator.evaluate_computed_transition_state_entry`
# so the loaded evaluator (and its check runners) issue no further
# queries — the read path must never push hidden queries into the trust
# runners. Loaded once in :func:`get_transition_state_entry` when
# ``include=trust`` is requested.
_TRUST_EAGER_LOADS = (
    selectinload(TransitionStateEntry.transition_state)
    .selectinload(TransitionState.reaction_entry)
    .selectinload(ReactionEntry.reaction),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.artifacts
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.geometry_validation
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.sp_result
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.opt_result
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.freq_result
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.irc_result
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.scan_result
    ),
    selectinload(TransitionStateEntry.calculations).selectinload(
        Calculation.path_search_result
    ),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.artifacts),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.sp_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.opt_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.freq_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.irc_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.parent_dependencies)
    .selectinload(CalculationDependency.child_calculation)
    .selectinload(Calculation.path_search_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.artifacts),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.sp_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.opt_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.freq_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.scan_result),
    selectinload(TransitionStateEntry.calculations)
    .selectinload(Calculation.child_dependencies)
    .selectinload(CalculationDependency.parent_calculation)
    .selectinload(Calculation.path_search_result),
)

# Public seam for the search service, which must load the same evidence
# graph over a whole page before ``build_entry_record`` reads it. Named
# rather than reached for through the private alias so a reader of the
# search module can see it is the *same* chain and not a second opinion.
TRANSITION_STATE_ENTRY_TRUST_EAGER_LOADS = _TRUST_EAGER_LOADS


# Calculation types that carry a primary "evidence" role for a TS entry.
_EVIDENCE_TYPES: tuple[CalculationType, ...] = (
    CalculationType.opt,
    CalculationType.freq,
    CalculationType.sp,
    CalculationType.irc,
    CalculationType.path_search,
)


# ---------------------------------------------------------------------------
# TS detail endpoint
# ---------------------------------------------------------------------------


def get_transition_state(
    session: Session,
    *,
    transition_state_handle: str,
    request: TransitionStateDetailRequest,
) -> ScientificTransitionStateDetailResponse:
    """Resolve *transition_state_handle* and return its scientific projection.

    Path-handle semantics match the rest of the scientific read API:

    - Integer ``transition_state.id`` string: SELECT by id.
    - Public ref ``ts_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    The default response surfaces the TS core block, reaction context,
    an entries summary (counts by status), an evidence summary across
    all entries, and an ``available_sections`` boolean map. Optional
    includes (``entries``, ``calculations``, ``geometries``, ``review``,
    ``all``, ``internal_ids``) expand the response without paginating
    children — the TS concept is bounded (a small handful of entries
    per channel in practice).
    """
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/transition-states/{transition_state_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    ts_id = resolve_transition_state_handle(session, transition_state_handle)
    ts = session.get(TransitionState, ts_id)
    if ts is None:  # pragma: no cover — defended by resolver 404
        raise not_found("transition_state", row_id=ts_id, code="handle_not_found")

    ts_badge = _load_review_badge(
        session, SubmissionRecordType.transition_state, ts.id
    )
    reaction = _build_reaction_context(session, ts.reaction_entry_id)

    # Collect entries up front — entries_summary, evidence_summary, and
    # several include blocks all need them.
    entries = session.scalars(
        select(TransitionStateEntry)
        .where(TransitionStateEntry.transition_state_id == ts.id)
        .order_by(TransitionStateEntry.id.asc())
    ).all()
    entry_ids = [e.id for e in entries]

    entries_summary = _build_entries_summary(entries)
    # One statement for the whole concept, shared by the pooled summary and
    # by every embedded entry record under ``include=entries``. Built here
    # rather than inside each builder so a 20-entry TS pays it once.
    levels_index = levels_of_theory.for_transition_state_entries(
        session, entry_ids
    )
    evidence_summary = _build_concept_evidence_summary(
        session, entry_ids, levels_index=levels_index
    )
    available = _build_available_sections(session, entries, entry_ids)
    # Same bargain as ``levels_index`` above: one statement for the whole
    # TS concept's entries, shared across every embedded entry record,
    # rather than one per entry — see ``_build_saddle_point_index``'s own
    # docstring for the N+1 this guards against.
    saddle_point_index = _build_saddle_point_index(session, entry_ids)

    entry_badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_ids=entry_ids,
        )
        if entry_ids
        else {}
    )

    entry_records_block: list[ScientificTransitionStateEntryRecord] | None = None
    if "entries" in includes:
        ts_core = _build_ts_core_block(ts, ts_badge)
        # Without ``entries`` in the nested set, every embedded entry would
        # resolve the same sibling list this block already is.
        nested_includes = includes - {"entries"}
        entry_records_block = [
            _build_entry_record(
                session,
                entry=e,
                ts_core=ts_core,
                reaction=reaction,
                entry_badge=entry_badges.get(
                    e.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                includes=nested_includes,
                levels_index=levels_index,
                saddle_point_index=saddle_point_index,
            )
            for e in entries
        ]

    ts_calcs_block: list[TransitionStateCalculationSummary] | None = None
    if "calculations" in includes:
        ts_calcs_block = _build_calculations_summary(session, entry_ids)

    ts_geoms_block: list[CalculationGeometryLinkSummary] | None = None
    if "geometries" in includes:
        ts_geoms_block = _build_output_geometry_links(session, entry_ids)

    ts_review_block: list[TransitionStateReviewEntry] | None = None
    if "review" in includes:
        ts_review_block = _build_review_history(
            session, SubmissionRecordType.transition_state, ts.id
        )

    # ``validation_evidence`` was legal on this surface and produced nothing:
    # the concept record had no such field, while its own
    # ``available_sections`` advertised ``has_validation_evidence``. The
    # block is keyed by entry ref rather than unioned across entries. A
    # concept is a collection of entries computed at different levels of
    # theory, and collapsing their evidence into one verdict is an
    # aggregation error — the same one ``0a6271c8`` removed from
    # conformer-group evidence summaries.
    ts_validation_block: (
        list[TransitionStateEntryValidationEvidence] | None
    ) = None
    if "validation_evidence" in includes:
        ts_validation_block = [
            TransitionStateEntryValidationEvidence(
                transition_state_entry_ref=e.public_ref,
                validation_evidence=_build_validation_evidence(session, e.id),
            )
            for e in entries
        ]

    record = ScientificTransitionStateRecord(
        transition_state=_build_ts_core_block(ts, ts_badge),
        reaction=reaction,
        entries_summary=entries_summary,
        evidence_summary=evidence_summary,
        available_sections=available,
        entries=entry_records_block,
        calculations=ts_calcs_block,
        geometries=ts_geoms_block,
        review_history=ts_review_block,
        validation_evidence=ts_validation_block,
    )

    return ScientificTransitionStateDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([ts_badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# TS-entry detail endpoint
# ---------------------------------------------------------------------------


def get_transition_state_entry(
    session: Session,
    *,
    transition_state_entry_handle: str,
    request: TransitionStateEntryDetailRequest,
) -> ScientificTransitionStateEntryDetailResponse:
    """Resolve *transition_state_entry_handle* and return its projection.

    Same handle / 404 / 422 contract as the TS detail endpoint. Returns
    a single TS-entry record plus parent-TS context and reaction
    context. The default response includes the evidence summary; the
    ``calculations`` / ``geometries`` / ``review`` includes expand the
    response without paginating.
    """
    includes = validate_includes(
        request.include,
        _TSE_DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/transition-state-entries/"
        "{transition_state_entry_ref_or_id}",
        internal_tokens=_TSE_DETAIL_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    tse_id = resolve_transition_state_entry_handle(
        session, transition_state_entry_handle
    )
    if "trust" in includes:
        # Eager-load the graph computed_transition_state_v2 inspects so the
        # loaded evaluator issues no further queries.
        tse = session.scalars(
            select(TransitionStateEntry)
            .where(TransitionStateEntry.id == tse_id)
            .options(*_TRUST_EAGER_LOADS)
        ).one_or_none()
    else:
        tse = session.get(TransitionStateEntry, tse_id)
    if tse is None:  # pragma: no cover — defended by resolver 404
        raise not_found("transition_state_entry", row_id=tse_id, code="handle_not_found")

    ts = session.get(TransitionState, tse.transition_state_id)
    if ts is None:  # pragma: no cover — FK guarantees existence
        raise not_found(
            "transition_state for the requested transition_state_entry",
            row_id=tse.transition_state_id,
            code="handle_not_found",
        )

    ts_badge = _load_review_badge(
        session, SubmissionRecordType.transition_state, ts.id
    )
    tse_badge = _load_review_badge(
        session, SubmissionRecordType.transition_state_entry, tse.id
    )
    reaction = _build_reaction_context(session, ts.reaction_entry_id)
    ts_core = _build_ts_core_block(ts, ts_badge)

    record = _build_entry_record(
        session,
        entry=tse,
        ts_core=ts_core,
        reaction=reaction,
        entry_badge=tse_badge,
        includes=includes,
    )

    return ScientificTransitionStateEntryDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([tse_badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared record builder
# ---------------------------------------------------------------------------


def build_entry_record(
    session: Session,
    *,
    entry: TransitionStateEntry,
    ts_core: TransitionStateCoreBlock,
    reaction: TransitionStateReactionContext,
    entry_badge: RecordReviewBadge,
    includes: set[str],
    entries_block: list[ScientificTransitionStateEntryRecord] | None = None,
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
    saddle_point_index: (
        dict[int, TransitionStateSaddlePointEvidence | None] | None
    ) = None,
) -> ScientificTransitionStateEntryRecord:
    """Public alias for :func:`_build_entry_record`.

    Exported so the search service can produce records with the same
    shape as the TS-entry detail endpoint.
    """
    return _build_entry_record(
        session,
        entry=entry,
        ts_core=ts_core,
        reaction=reaction,
        entry_badge=entry_badge,
        includes=includes,
        entries_block=entries_block,
        levels_index=levels_index,
        saddle_point_index=saddle_point_index,
    )


def _build_entry_record(
    session: Session,
    *,
    entry: TransitionStateEntry,
    ts_core: TransitionStateCoreBlock,
    reaction: TransitionStateReactionContext,
    entry_badge: RecordReviewBadge,
    includes: set[str],
    entries_block: list[ScientificTransitionStateEntryRecord] | None = None,
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
    saddle_point_index: (
        dict[int, TransitionStateSaddlePointEvidence | None] | None
    ) = None,
) -> ScientificTransitionStateEntryRecord:
    """Project one TS entry into the shared record shape.

    ``entries_block`` lets a caller that has already grouped the sibling
    entries over a whole page hand them in, so the search surface pays one
    load for the page instead of one per record. A caller that passes
    nothing and asks for ``include=entries`` gets the siblings resolved
    here — the parent always has at least this entry under it, so ``None``
    unambiguously means "not supplied" and never "supplied and empty".

    ``levels_index`` is the same bargain for ``levels_of_theory``, and it is
    the reason that block costs a search page one statement rather than
    ``limit`` of them. ``None`` means "resolve it for this entry alone",
    which is the right answer on a detail read and the wrong one inside a
    loop — every loop in this module hands one in.

    ``saddle_point_index`` is the same bargain again, for the
    ``saddle_point`` block — see :func:`_build_saddle_point_index`. ``None``
    means "resolve it for this entry alone" (the single-entry detail path);
    every loop over more than one entry in this module hands one in.
    """
    evidence = _build_entry_evidence_summary(
        session, entry.id, levels_index=levels_index
    )
    if saddle_point_index is None:
        saddle_point_index = _build_saddle_point_index(session, [entry.id])
    saddle_point = saddle_point_index.get(entry.id)
    available = _build_available_sections(session, [entry], [entry.id])

    calcs_block: list[TransitionStateCalculationSummary] | None = None
    if "calculations" in includes:
        calcs_block = _build_calculations_summary(session, [entry.id])

    geoms_block: list[CalculationGeometryLinkSummary] | None = None
    if "geometries" in includes:
        geoms_block = _build_output_geometry_links(session, [entry.id])

    review_block: list[TransitionStateReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(
            session, SubmissionRecordType.transition_state_entry, entry.id
        )

    validation_block: list[TransitionStateValidationEvidenceSummary] | None = None
    if "validation_evidence" in includes:
        validation_block = _build_validation_evidence(session, entry.id)

    # ``entries`` on an entry-grained record answers "what else is under
    # this transition state" — the one piece of context an entry-grained
    # response cannot otherwise give. It was legal on this shape and did
    # nothing: the search service discarded the token before it reached a
    # builder, and the record had no field to put it in.
    #
    # The nested records are built without ``entries`` in their include set,
    # which is not an optimisation but the thing that terminates the
    # recursion: a sibling that resolved its own siblings would resolve this
    # record again, and so on.
    if entries_block is None and "entries" in includes:
        entries_block = _build_sibling_entry_records(
            session,
            entry=entry,
            ts_core=ts_core,
            reaction=reaction,
            includes=includes,
        )

    # ``trust`` is populated wherever the token is legal — the standalone
    # TS-entry detail surface and, since the search vocabulary was widened,
    # ``/transition-states/search``. Both paths eager-load the graph the
    # evaluator walks before reaching this builder: the detail path for one
    # row, the search path for the whole page. Nothing here issues that load
    # itself, because anything this function loads is multiplied by the page
    # size.
    trust_block: TrustFragment | None = None
    if "trust" in includes:
        trust_block = build_transition_state_entry_trust_fragment(
            entry,
            review_status=entry_badge.status,
            include_internal_ids="internal_ids" in includes,
        )

    return ScientificTransitionStateEntryRecord(
        transition_state_entry=TransitionStateEntryCoreBlock(
            transition_state_entry_id=entry.id,
            transition_state_entry_ref=entry.public_ref,
            charge=entry.charge,
            multiplicity=entry.multiplicity,
            status=entry.status,
            unmapped_smiles=entry.unmapped_smiles,
            created_at=entry.created_at,
            review=entry_badge,
        ),
        transition_state=ts_core,
        reaction=reaction,
        evidence_summary=evidence,
        validation=_build_validation_descriptor(session, entry.id),
        saddle_point=saddle_point,
        available_sections=available,
        entries=entries_block,
        calculations=calcs_block,
        geometries=geoms_block,
        review_history=review_block,
        validation_evidence=validation_block,
        trust=trust_block,
    )


def build_sibling_entry_records(
    session: Session,
    *,
    entry: TransitionStateEntry,
    ts_core: TransitionStateCoreBlock,
    reaction: TransitionStateReactionContext,
    includes: set[str],
) -> list[ScientificTransitionStateEntryRecord]:
    """Public alias for :func:`_build_sibling_entry_records`.

    Exported so the search service can group the sibling lists over a whole
    page and hand each one to ``build_entry_record``.
    """
    return _build_sibling_entry_records(
        session,
        entry=entry,
        ts_core=ts_core,
        reaction=reaction,
        includes=includes,
    )


def _build_sibling_entry_records(
    session: Session,
    *,
    entry: TransitionStateEntry,
    ts_core: TransitionStateCoreBlock,
    reaction: TransitionStateReactionContext,
    includes: set[str],
) -> list[ScientificTransitionStateEntryRecord]:
    """Every entry under *entry*'s parent transition state, this one included.

    The same list, in the same shape and the same order, that the TS-concept
    detail surface returns under ``include=entries``. The parent and its
    reaction context are shared by construction, so they are passed through
    rather than re-resolved per sibling.
    """
    stmt = (
        select(TransitionStateEntry)
        .where(
            TransitionStateEntry.transition_state_id == entry.transition_state_id
        )
        .order_by(TransitionStateEntry.id.asc())
    )
    if "trust" in includes:
        # The siblings carry ``trust`` on the same terms as the record they
        # hang off, so they need the same graph loaded before the evaluator
        # walks it — grouped over the sibling set, never per sibling.
        stmt = stmt.options(*_TRUST_EAGER_LOADS)
    siblings = session.scalars(stmt).all()
    sibling_ids = [e.id for e in siblings]
    badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_ids=sibling_ids,
        )
        if sibling_ids
        else {}
    )
    nested_includes = includes - {"entries"}
    # Grouped over the sibling set, never per sibling — same reason as the
    # trust eager loads above.
    levels_index = levels_of_theory.for_transition_state_entries(
        session, sibling_ids
    )
    saddle_point_index = _build_saddle_point_index(session, sibling_ids)
    return [
        _build_entry_record(
            session,
            entry=sibling,
            ts_core=ts_core,
            reaction=reaction,
            entry_badge=badges.get(
                sibling.id,
                RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
            ),
            includes=nested_includes,
            levels_index=levels_index,
            saddle_point_index=saddle_point_index,
        )
        for sibling in siblings
    ]


def build_transition_state_entry_trust_fragment(
    transition_state_entry: TransitionStateEntry,
    review_status: RecordReviewStatus | None = None,
    include_internal_ids: bool = False,
) -> TrustFragment:
    """Build a read-layer trust fragment for a transition-state entry.

    Calls the *loaded* evaluator
    (:func:`evaluate_loaded_transition_state_entry`) — never the
    session/id wrapper — because the caller has already loaded the entry
    (and, on the trust path, its evidence graph). The evaluator owns
    deterministic ``evidence_completeness``; the read layer owns review
    status, the disabled LLM-precheck default, and certification default
    (all supplied by :func:`build_trust_fragment`).

    ``include_internal_ids`` mirrors the resolved include set so callers
    can reason about ID exposure at this layer, but the canonical gate
    for ``trust.evidence.record_id`` is the response boundary
    (:func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`),
    which strips the id recursively unless ``include=internal_ids`` is
    both requested and permitted by the deployment. The flag is therefore
    advisory here — the fragment always carries the evaluator's
    ``record_id`` and the boundary removes it when policy disallows it.
    """
    evaluation = evaluate_loaded_transition_state_entry(transition_state_entry)
    return build_trust_fragment(evaluation, review_status=review_status)


# ---------------------------------------------------------------------------
# Core block builders
# ---------------------------------------------------------------------------


def _build_ts_core_block(
    ts: TransitionState, badge: RecordReviewBadge
) -> TransitionStateCoreBlock:
    return TransitionStateCoreBlock(
        transition_state_id=ts.id,
        transition_state_ref=ts.public_ref,
        label=ts.label,
        note=ts.note,
        created_at=ts.created_at,
        review=badge,
    )


def _build_reaction_context(
    session: Session, reaction_entry_id: int | None
) -> TransitionStateReactionContext:
    """Resolve reaction-entry → reaction context for a TS.

    Returns a context with all-None fields when *reaction_entry_id* is
    None (defensive — the schema forbids this, but we surface a usable
    response rather than 500 if a row ever slips through).
    """
    if reaction_entry_id is None:
        return TransitionStateReactionContext()
    row = session.execute(
        select(
            ReactionEntry.id.label("entry_id"),
            ReactionEntry.public_ref.label("entry_ref"),
            ChemReaction.id.label("reaction_id"),
            ChemReaction.public_ref.label("reaction_ref"),
            ChemReaction.reversible.label("reversible"),
            ChemReaction.reaction_family_id.label("family_id"),
        )
        .join(ChemReaction, ChemReaction.id == ReactionEntry.reaction_id)
        .where(ReactionEntry.id == reaction_entry_id)
    ).one_or_none()
    if row is None:
        return TransitionStateReactionContext()

    family_name: str | None = None
    if row.family_id is not None:
        family_name = session.scalar(
            select(ReactionFamily.name).where(
                ReactionFamily.id == row.family_id
            )
        )

    equation = _format_equation(session, row.entry_id, row.reversible)
    return TransitionStateReactionContext(
        reaction_id=row.reaction_id,
        reaction_ref=row.reaction_ref,
        reaction_entry_id=row.entry_id,
        reaction_entry_ref=row.entry_ref,
        equation=equation,
        reversible=row.reversible,
        family=family_name,
    )


def _format_equation(
    session: Session, reaction_entry_id: int, reversible: bool
) -> str | None:
    """Build ``"SMI + SMI <=> SMI + SMI"`` for the reaction entry.

    Reaction entries carry an *ordered* participant table
    (``reaction_entry_structure_participant``) keyed by
    ``species_entry_id`` and ``participant_index``; SMILES live on the
    parent ``Species`` row. Order by ``(role, participant_index)`` so
    the rendering is stable.
    """
    from app.db.models.common import ReactionRole
    from app.db.models.reaction import ReactionEntryStructureParticipant
    from app.db.models.species import Species, SpeciesEntry

    rows = session.execute(
        select(
            Species.smiles.label("smiles"),
            ReactionEntryStructureParticipant.role.label("role"),
            ReactionEntryStructureParticipant.participant_index.label(
                "participant_index"
            ),
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id
            == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id
            == reaction_entry_id
        )
        .order_by(
            ReactionEntryStructureParticipant.role.asc(),
            ReactionEntryStructureParticipant.participant_index.asc(),
        )
    ).all()
    if not rows:
        return None
    reactants = [r.smiles for r in rows if r.role == ReactionRole.reactant]
    products = [r.smiles for r in rows if r.role == ReactionRole.product]
    if not reactants and not products:
        return None
    arrow = "<=>" if reversible else "->"
    return f"{' + '.join(reactants)} {arrow} {' + '.join(products)}"


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _build_entries_summary(
    entries: list[TransitionStateEntry],
) -> TransitionStateEntriesSummary:
    by_status: dict[str, int] = {}
    for e in entries:
        key = e.status.value if hasattr(e.status, "value") else str(e.status)
        by_status[key] = by_status.get(key, 0) + 1
    return TransitionStateEntriesSummary(total=len(entries), by_status=by_status)


def _build_concept_evidence_summary(
    session: Session,
    entry_ids: list[int],
    *,
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
) -> TransitionStateEvidenceSummary:
    """Compute the TS-concept-scope calculation-evidence summary.

    ``calculation_count`` is the total across every entry under the TS —
    a sum is honest about a sum.

    ``evidence_coverage`` is deliberately **not** a calculation count.
    Each value is the number of *entries* in *entry_ids* with at least
    one calculation of that kind, so an entry carrying three ``freq``
    calculations contributes ``1``. That is what makes the value
    readable against the ``entry_count`` denominator, and it is why the
    coverage queries count ``DISTINCT
    Calculation.transition_state_entry_id`` rather than
    ``Calculation.id``. Counting calculations here would let a coverage
    value exceed ``entry_count``, which is nonsense on its face.

    This block replaced a set of ``has_*`` booleans that OR-ed every
    calculation under the TS together; see
    :class:`TransitionStateEvidenceSummary` for why. ``count > 0``
    reproduces the retired boolean exactly.

    ``levels_of_theory`` is the **union** over the entries, not a count:
    at concept grain the honest statement is "these are the levels used
    somewhere under this TS", and a reader who needs to know which entry
    used which reads the entry records under ``include=entries``.
    """
    if not entry_ids:
        # No entries: every coverage value is 0 out of 0. Nothing is
        # vacuously "covered" here — a caller reading ``freq == 0``
        # against ``entry_count == 0`` sees an empty TS, not a complete
        # one.
        return TransitionStateEvidenceSummary(
            entry_count=0,
            calculation_count=0,
            evidence_coverage=TransitionStateEvidenceCoverage(
                opt=0,
                freq=0,
                sp=0,
                irc=0,
                path_search=0,
                geometry_validation=0,
                scf_stability=0,
            ),
            # No entries, so no calculations, so no observed types. ``{}``
            # matches the zeroed counts beside it: nothing is attached.
            levels_of_theory={},
        )

    # One pass gives both the calculation total (COUNT(id)) and the
    # per-type entry coverage (COUNT(DISTINCT transition_state_entry_id)).
    type_rows = session.execute(
        select(
            Calculation.type,
            func.count(Calculation.id),
            func.count(func.distinct(Calculation.transition_state_entry_id)),
        )
        .where(Calculation.transition_state_entry_id.in_(entry_ids))
        .group_by(Calculation.type)
    ).all()
    calc_counts: dict[CalculationType, int] = {row[0]: row[1] for row in type_rows}
    entry_coverage: dict[CalculationType, int] = {
        row[0]: row[2] for row in type_rows
    }

    if levels_index is None:
        levels_index = levels_of_theory.for_transition_state_entries(
            session, entry_ids
        )

    return TransitionStateEvidenceSummary(
        entry_count=len(entry_ids),
        calculation_count=sum(calc_counts.values()),
        evidence_coverage=TransitionStateEvidenceCoverage(
            opt=entry_coverage.get(CalculationType.opt, 0),
            freq=entry_coverage.get(CalculationType.freq, 0),
            sp=entry_coverage.get(CalculationType.sp, 0),
            irc=entry_coverage.get(CalculationType.irc, 0),
            path_search=entry_coverage.get(CalculationType.path_search, 0),
            geometry_validation=_entry_coverage_via_join(
                session,
                entry_ids,
                joined=CalculationGeometryValidation,
                onclause=(
                    CalculationGeometryValidation.calculation_id
                    == Calculation.id
                ),
            ),
            scf_stability=_entry_coverage_via_join(
                session,
                entry_ids,
                joined=CalculationSCFStability,
                onclause=CalculationSCFStability.calculation_id
                == Calculation.id,
            ),
        ),
        levels_of_theory=levels_index.merged(entry_ids),
    )


def _entry_coverage_via_join(
    session: Session,
    entry_ids: list[int],
    *,
    joined,
    onclause,
) -> int:
    """Count TS entries with >=1 calculation carrying a joined evidence row.

    DISTINCT is on ``transition_state_entry_id``, so an entry with three
    qualifying calculations still counts once.
    """
    return int(
        session.scalar(
            select(
                func.count(
                    func.distinct(Calculation.transition_state_entry_id)
                )
            )
            .select_from(Calculation)
            .join(joined, onclause)
            .where(Calculation.transition_state_entry_id.in_(entry_ids))
        )
        or 0
    )


def _build_entry_evidence_summary(
    session: Session,
    entry_id: int,
    *,
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
) -> TransitionStateEntryEvidenceSummary:
    """Compute the TS-entry-scope calculation-evidence summary.

    Scope is one candidate saddle point, so the ``has_*`` booleans here
    are unambiguous — there is no second entry for a ``true`` to hide
    behind. They are kept as booleans for exactly that reason; the TS
    concept surface reports counts instead (see
    :class:`TransitionStateEvidenceSummary`).
    """
    entry_ids = [entry_id]
    type_rows = session.execute(
        select(Calculation.type, func.count(Calculation.id))
        .where(Calculation.transition_state_entry_id.in_(entry_ids))
        .group_by(Calculation.type)
    ).all()
    type_counts: dict[CalculationType, int] = {row[0]: row[1] for row in type_rows}
    total = sum(type_counts.values())

    has_geom_val = bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        CalculationGeometryValidation.calculation_id
                        == Calculation.id,
                        Calculation.transition_state_entry_id.in_(entry_ids),
                    )
                )
            )
        )
    )
    has_scf = bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        CalculationSCFStability.calculation_id
                        == Calculation.id,
                        Calculation.transition_state_entry_id.in_(entry_ids),
                    )
                )
            )
        )
    )

    if levels_index is None:
        levels_index = levels_of_theory.for_transition_state_entries(
            session, [entry_id]
        )

    return TransitionStateEntryEvidenceSummary(
        calculation_count=total,
        has_opt=type_counts.get(CalculationType.opt, 0) > 0,
        has_freq=type_counts.get(CalculationType.freq, 0) > 0,
        has_sp=type_counts.get(CalculationType.sp, 0) > 0,
        has_irc=type_counts.get(CalculationType.irc, 0) > 0,
        has_path_search=type_counts.get(CalculationType.path_search, 0) > 0,
        has_geometry_validation=has_geom_val,
        has_scf_stability=has_scf,
        levels_of_theory=levels_index.for_owner(entry_id),
    )


def _build_available_sections(
    session: Session,
    entries: list[TransitionStateEntry] | None,
    entry_ids: list[int],
) -> AvailableTransitionStateSections:
    has_entries = bool(entries) if entries is not None else len(entry_ids) > 0
    has_calcs = False
    has_geoms = False
    if entry_ids:
        has_calcs = bool(
            session.scalar(
                select(
                    exists().where(
                        Calculation.transition_state_entry_id.in_(entry_ids)
                    )
                )
            )
        )
        if has_calcs:
            has_geoms = bool(
                session.scalar(
                    select(
                        exists().where(
                            and_(
                                CalculationOutputGeometry.calculation_id
                                == Calculation.id,
                                Calculation.transition_state_entry_id.in_(
                                    entry_ids
                                ),
                            )
                        )
                    )
                )
            )
    has_review = False
    has_validation_evidence = bool(
        entry_ids
        and session.scalar(
            select(
                exists().where(
                    TransitionStateValidationEvidence.transition_state_entry_id.in_(entry_ids)
                )
            )
        )
    )
    if entries is not None:
        has_review = bool(
            session.scalar(
                select(
                    exists().where(
                        and_(
                            RecordReview.record_id.in_(
                                [e.id for e in entries] or [0]
                            ),
                            RecordReview.record_type
                            == SubmissionRecordType.transition_state_entry,
                        )
                    )
                )
            )
        )
    return AvailableTransitionStateSections(
        has_entries=has_entries,
        has_calculations=has_calcs,
        has_geometries=has_geoms,
        has_review=has_review,
        has_validation_evidence=has_validation_evidence,
    )


def _build_validation_evidence(
    session: Session, entry_id: int
) -> list[TransitionStateValidationEvidenceSummary]:
    rows = session.execute(
        select(
            TransitionStateValidationEvidence,
            Calculation.public_ref.label("calculation_ref"),
            # The geometry the participant mappings' atom indices count into.
            # Projected beside them rather than left in the database: an index
            # a reader cannot resolve to a geometry does not identify an atom,
            # so shipping the mappings without this would ship numbers
            # relative to an ordering the caller has to guess at.
            Geometry.public_ref.label("geometry_ref"),
        )
        .outerjoin(
            Calculation,
            Calculation.id == TransitionStateValidationEvidence.reconstruction_calculation_id,
        )
        .outerjoin(
            Geometry,
            Geometry.id == TransitionStateValidationEvidence.transition_state_geometry_id,
        )
        .where(TransitionStateValidationEvidence.transition_state_entry_id == entry_id)
        .order_by(TransitionStateValidationEvidence.id.asc())
    ).all()
    return [
        TransitionStateValidationEvidenceSummary(
            kind=row.TransitionStateValidationEvidence.kind,
            passed=row.TransitionStateValidationEvidence.passed,
            rationale=row.TransitionStateValidationEvidence.rationale,
            reconstruction_calculation_ref=row.calculation_ref,
            reactant_participant_mapping=row.TransitionStateValidationEvidence.reactant_participant_mapping,
            product_participant_mapping=row.TransitionStateValidationEvidence.product_participant_mapping,
            transition_state_geometry_ref=row.geometry_ref,
        )
        for row in rows
    ]


def _build_validation_descriptor(
    session: Session, entry_id: int
) -> TransitionStateValidationDescriptor:
    """State IRC validation as one machine token, always.

    A TS with no IRC evidence is a legitimate deposit (the upload emits a
    warning), so the read surface must say ``absent`` rather than leave the
    caller to infer it from an empty optional block.
    """
    passed_values = session.scalars(
        select(TransitionStateValidationEvidence.passed).where(
            TransitionStateValidationEvidence.transition_state_entry_id == entry_id,
            TransitionStateValidationEvidence.kind == "irc",
        )
    ).all()
    if not passed_values:
        return TransitionStateValidationDescriptor(irc="absent")
    return TransitionStateValidationDescriptor(
        irc="present" if any(passed_values) else "failed"
    )


def _build_saddle_point_index(
    session: Session, entry_ids: list[int]
) -> dict[int, TransitionStateSaddlePointEvidence | None]:
    """Resolve every entry's representative freq result in ONE statement.

    Mirrors ``levels_of_theory.for_transition_state_entries``'s bargain:
    called once per page (search) or once per parent's entry set (TS
    concept detail with ``include=entries``), never once per record — a
    per-record implementation is exactly the N+1
    ``test_the_saddle_point_map_costs_a_ts_search_page_one_statement``
    exists to catch (measured regression: +1 statement per record on
    ``/transition-states/search``, 169 → 189 at ``limit=20``).

    Scope is calculations directly attached to each entry
    (``calculation.transition_state_entry_id``) — the same scope
    ``_build_calculations_summary`` already queries for the
    ``calculations`` include. This is **narrower** than the trust rubric's
    ``_ts_source_calculations``, which additionally walks one dependency
    hop (e.g. a ``freq_on`` edge to a calc not directly attached to the
    entry). Selection *among candidates this scope finds* uses the same
    deterministic rule as the rubric's ``_ts_representative_freq_result``
    (latest ``created_at``, ``calculation.id`` DESC tie-break) — so the two
    surfaces agree whenever the representative freq calc is directly
    attached, which is the common case, but a rubric pick reached only
    through a dependency hop is a freq calc this block never sees, and the
    two surfaces can disagree on which entry's freq result is
    "representative" in that case. That gap is not closed here.

    Every id in *entry_ids* is a key in the returned map, valued ``None``
    for an entry with no freq calculation carrying a ``calc_freq_result``
    row — a real absence, per the invariant that absence describes the
    request and null describes the data.
    """
    index: dict[int, TransitionStateSaddlePointEvidence | None] = {
        entry_id: None for entry_id in entry_ids
    }
    if not entry_ids:
        return index

    rows = session.execute(
        select(Calculation, CalculationFreqResult)
        .join(
            CalculationFreqResult,
            CalculationFreqResult.calculation_id == Calculation.id,
        )
        .where(
            Calculation.transition_state_entry_id.in_(entry_ids),
            Calculation.type == CalculationType.freq,
        )
    ).all()
    if not rows:
        return index

    by_entry: dict[int, list[tuple[Calculation, CalculationFreqResult]]] = {}
    for calc, freq in rows:
        by_entry.setdefault(calc.transition_state_entry_id, []).append(
            (calc, freq)
        )

    lot_ids = {
        calc.lot_id
        for pairs in by_entry.values()
        for calc, _ in pairs
        if calc.lot_id is not None
    }
    lot_summaries = _bulk_lot_summaries(session, lot_ids)

    for entry_id, pairs in by_entry.items():
        calc, freq = max(pairs, key=lambda pair: (pair[0].created_at, pair[0].id))
        index[entry_id] = TransitionStateSaddlePointEvidence(
            n_imag=freq.n_imag,
            imag_freq_cm1=freq.imag_freq_cm1,
            reaction_coordinate_mode_index=freq.reaction_coordinate_mode_index,
            imaginary_mode_structural_flag=freq.imaginary_mode_structural_flag,
            calculation_ref=calc.public_ref,
            level_of_theory=lot_summaries.get(calc.lot_id),
        )
    return index


# ---------------------------------------------------------------------------
# Calculation summary loader (include=calculations)
# ---------------------------------------------------------------------------


def _build_calculations_summary(
    session: Session, entry_ids: list[int]
) -> list[TransitionStateCalculationSummary]:
    if not entry_ids:
        return []
    calcs = session.scalars(
        select(Calculation)
        .where(Calculation.transition_state_entry_id.in_(entry_ids))
        .order_by(Calculation.created_at.asc(), Calculation.id.asc())
    ).all()
    if not calcs:
        return []
    calc_ids = [c.id for c in calcs]
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=calc_ids,
    )

    lot_summaries = _bulk_lot_summaries(
        session, {c.lot_id for c in calcs if c.lot_id is not None}
    )
    sw_summaries = _bulk_software_summaries(
        session,
        {
            c.software_release_id
            for c in calcs
            if c.software_release_id is not None
        },
    )
    wf_summaries = _bulk_workflow_summaries(
        session,
        {
            c.workflow_tool_release_id
            for c in calcs
            if c.workflow_tool_release_id is not None
        },
    )

    # Real sp/opt energy per calc, mirroring the projection
    # ``species_calculations_search._query_candidate_calculations`` does
    # for species-owned rows: "electronic_energy" for a real ``sp``
    # result, "final_energy" for a real ``opt`` result, ``None`` for
    # every other calc type (and for an sp/opt whose result row hasn't
    # landed yet, energy_hartree stays null but the kind is still named).
    energy_by_calc = _bulk_energy_values(session, calc_ids)

    # transition_state_entry_id -> set of lot_ids carrying a real ``sp``
    # calculation on *that* entry, so ``_build_energy_block`` can decide
    # whether a converged ``opt``'s own final energy may be offered as a
    # single-point-equivalent. Scoped to the entries of opt calcs that
    # could actually use it (same restriction as the species side).
    sp_lot_pairs = _load_sp_lot_pairs(
        session,
        {
            CalculationRecordKind.transition_state: {
                c.transition_state_entry_id
                for c in calcs
                if c.type == CalculationType.opt
                and c.transition_state_entry_id is not None
                and c.lot_id is not None
                and energy_by_calc.get(c.id, (None, None))[0] is not None
            }
        },
    )

    out: list[TransitionStateCalculationSummary] = []
    for c in calcs:
        energy_hartree, energy_kind = energy_by_calc.get(c.id, (None, None))
        out.append(
            TransitionStateCalculationSummary(
                calculation_id=c.id,
                calculation_ref=c.public_ref,
                type=c.type,
                quality=c.quality,
                created_at=c.created_at,
                review=badges.get(
                    c.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                level_of_theory=lot_summaries.get(c.lot_id),
                software_release=sw_summaries.get(c.software_release_id),
                workflow_tool_release=wf_summaries.get(
                    c.workflow_tool_release_id
                ),
                energy=_build_energy_block(
                    calc_type=c.type,
                    energy_hartree=energy_hartree,
                    energy_kind=energy_kind,
                    lot_id=c.lot_id,
                    record_kind=CalculationRecordKind.transition_state,
                    owner_id=c.transition_state_entry_id,
                    sp_lot_pairs=sp_lot_pairs,
                ),
            )
        )
    return out


def _bulk_energy_values(
    session: Session, calc_ids: list[int]
) -> dict[int, tuple[float | None, str | None]]:
    """Map calculation_id -> (energy_hartree, energy_kind) for sp/opt calcs.

    Loaded from ``Calculation`` type plus an outer join against the two
    per-type result tables so an sp/opt calc with no result row yet still
    gets its ``energy_kind`` named (value ``None``) — the same three-way
    shape ``species_calculations_search`` produces per row.
    """
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.type,
            CalculationSPResult.electronic_energy_hartree,
            CalculationOptResult.final_energy_hartree,
        )
        .outerjoin(
            CalculationSPResult,
            CalculationSPResult.calculation_id == Calculation.id,
        )
        .outerjoin(
            CalculationOptResult,
            CalculationOptResult.calculation_id == Calculation.id,
        )
        .where(Calculation.id.in_(calc_ids))
    ).all()
    out: dict[int, tuple[float | None, str | None]] = {}
    for calc_id, calc_type, sp_energy, opt_energy in rows:
        if calc_type == CalculationType.sp:
            out[calc_id] = (sp_energy, "electronic_energy")
        elif calc_type == CalculationType.opt:
            out[calc_id] = (opt_energy, "final_energy")
    return out


def _bulk_lot_summaries(
    session: Session, lot_ids: set[int]
) -> dict[int, LevelOfTheorySummary]:
    if not lot_ids:
        return {}
    rows = session.scalars(
        select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids))
    ).all()
    return {
        lot.id: LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            label=None,
        )
        for lot in rows
    }


def _bulk_software_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, SoftwareReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: SoftwareReleaseSummary(
            software_release_id=row.id,
            software_release_ref=row.public_ref,
            software=row.name,
            version=row.version,
        )
        for row in rows
    }


def _bulk_workflow_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, WorkflowToolReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: WorkflowToolReleaseSummary(
            workflow_tool_release_id=row.id,
            workflow_tool_release_ref=row.public_ref,
            workflow_tool=row.name,
            version=row.version,
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# Geometry loader (include=geometries) — output geometries only
# ---------------------------------------------------------------------------


def _build_output_geometry_links(
    session: Session, entry_ids: list[int]
) -> list[CalculationGeometryLinkSummary]:
    """Return lightweight output-geometry links for calcs under *entry_ids*.

    Ref-only payload (geometry_id is policy-gated by the strip helper).
    Full coordinate data lives behind
    ``GET /scientific/geometries/{geometry_ref}`` and is never inlined.
    """
    if not entry_ids:
        return []
    rows = session.execute(
        select(
            Geometry.id.label("geometry_id"),
            Geometry.public_ref.label("geometry_ref"),
            Geometry.natoms.label("natoms"),
            Geometry.geom_hash.label("geom_hash"),
            CalculationOutputGeometry.output_order.label("output_order"),
            CalculationOutputGeometry.role.label("role"),
            Calculation.id.label("calculation_id"),
        )
        .join(
            CalculationOutputGeometry,
            CalculationOutputGeometry.geometry_id == Geometry.id,
        )
        .join(
            Calculation,
            Calculation.id == CalculationOutputGeometry.calculation_id,
        )
        .where(Calculation.transition_state_entry_id.in_(entry_ids))
        .order_by(
            Calculation.id.asc(),
            CalculationOutputGeometry.output_order.asc(),
        )
    ).all()
    return [
        CalculationGeometryLinkSummary(
            geometry_id=row.geometry_id,
            geometry_ref=row.geometry_ref,
            input_order=None,
            output_order=row.output_order,
            role=row.role,
            natoms=row.natoms,
            geom_hash=row.geom_hash,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Review history loader (include=review)
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> list[TransitionStateReviewEntry]:
    rows = session.scalars(
        select(RecordReview)
        .where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == record_id,
        )
        .order_by(RecordReview.reviewed_at.asc().nulls_last())
    ).all()
    return [
        TransitionStateReviewEntry(
            status=row.status.value
            if hasattr(row.status, "value")
            else str(row.status),
            reviewed_at=row.reviewed_at,
            reviewed_by=row.reviewed_by,
            note=row.note,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Review badge loader
# ---------------------------------------------------------------------------


def _load_review_badge(
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> RecordReviewBadge:
    badges = fetch_review_badges(
        session, record_type=record_type, record_ids=[record_id]
    )
    return badges.get(
        record_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


__all__ = [
    "_EVIDENCE_TYPES",
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "_TSE_DETAIL_LEGAL_INCLUDE_TOKENS",
    "build_entry_record",
    "build_transition_state_entry_trust_fragment",
    "get_transition_state",
    "get_transition_state_entry",
]
