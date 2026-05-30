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

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationOutputGeometry,
    CalculationSCFStability,
)
from app.db.models.common import (
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
    TransitionStateCalculationEvidenceSummary,
    TransitionStateCalculationSummary,
    TransitionStateCoreBlock,
    TransitionStateDetailRequest,
    TransitionStateEntriesSummary,
    TransitionStateEntryCoreBlock,
    TransitionStateEntryDetailRequest,
    TransitionStateReactionContext,
    TransitionStateReviewEntry,
)
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
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# ``trust`` is legal *only* on the standalone TS-entry detail surface.
# The parent-TS detail surface and the search surface keep the narrower
# ``_LEGAL_INCLUDE_TOKENS`` set, so a caller passing ``include=trust`` to
# those endpoints gets a 422 ``unknown_include_token`` — trust is never
# exposed through embedded entries or list/search responses (and, like
# ``internal_ids``, it is internal-tokenized so ``include=all`` does not
# pull it in).
_TSE_DETAIL_LEGAL_INCLUDE_TOKENS: set[str] = _LEGAL_INCLUDE_TOKENS | {"trust"}
_TSE_DETAIL_INTERNAL_INCLUDE_TOKENS: set[str] = _INTERNAL_INCLUDE_TOKENS | {
    "trust"
}


# Eager-load graph required by ``computed_transition_state_v1``. Mirrors
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
        raise NotFoundError(
            f"transition_state not found (transition_state_id={ts_id})",
            code="handle_not_found",
        )

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
    evidence_summary = _build_evidence_summary_for_entries(
        session, entry_ids
    )
    available = _build_available_sections(session, entries, entry_ids)

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
                includes=includes,
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
        # Eager-load the graph computed_transition_state_v1 inspects so the
        # loaded evaluator issues no further queries.
        tse = session.scalars(
            select(TransitionStateEntry)
            .where(TransitionStateEntry.id == tse_id)
            .options(*_TRUST_EAGER_LOADS)
        ).one_or_none()
    else:
        tse = session.get(TransitionStateEntry, tse_id)
    if tse is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            "transition_state_entry not found "
            f"(transition_state_entry_id={tse_id})",
            code="handle_not_found",
        )

    ts = session.get(TransitionState, tse.transition_state_id)
    if ts is None:  # pragma: no cover — FK guarantees existence
        raise NotFoundError(
            "transition_state not found for entry "
            f"(transition_state_entry_id={tse.id})",
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
    )


def _build_entry_record(
    session: Session,
    *,
    entry: TransitionStateEntry,
    ts_core: TransitionStateCoreBlock,
    reaction: TransitionStateReactionContext,
    entry_badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificTransitionStateEntryRecord:
    evidence = _build_evidence_summary_for_entries(session, [entry.id])
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

    # ``trust`` is only ever in *includes* on the standalone TS-entry detail
    # surface (the parent-TS and search surfaces reject the token), and only
    # that path eager-loads the graph the evaluator walks. Building it here
    # keeps the per-record shape identical across the detail surfaces while
    # never populating trust for embedded entries / search records.
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
        available_sections=available,
        calculations=calcs_block,
        geometries=geoms_block,
        review_history=review_block,
        trust=trust_block,
    )


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


def _build_evidence_summary_for_entries(
    session: Session, entry_ids: list[int]
) -> TransitionStateCalculationEvidenceSummary:
    """Compute the calculation-evidence summary for a set of TS entries."""
    if not entry_ids:
        return TransitionStateCalculationEvidenceSummary(
            calculation_count=0,
            has_opt=False,
            has_freq=False,
            has_sp=False,
            has_irc=False,
            has_path_search=False,
            has_geometry_validation=False,
            has_scf_stability=False,
        )

    # Single GROUP BY query: count + per-type presence.
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

    return TransitionStateCalculationEvidenceSummary(
        calculation_count=total,
        has_opt=type_counts.get(CalculationType.opt, 0) > 0,
        has_freq=type_counts.get(CalculationType.freq, 0) > 0,
        has_sp=type_counts.get(CalculationType.sp, 0) > 0,
        has_irc=type_counts.get(CalculationType.irc, 0) > 0,
        has_path_search=type_counts.get(CalculationType.path_search, 0) > 0,
        has_geometry_validation=has_geom_val,
        has_scf_stability=has_scf,
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
    )


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

    out: list[TransitionStateCalculationSummary] = []
    for c in calcs:
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
            )
        )
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
