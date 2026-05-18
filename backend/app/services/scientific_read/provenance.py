"""Service implementation for /api/v1/scientific/reaction-entries/{id}/full.

Composite document: joins species, kinetics, transition states, calculations,
review summary into a single response. See docs/specs/read_api_mvp.md §Endpoint 5.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    PathSearchMethod,
    ReactionRole,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CalculationEvidenceSummary,
    LevelOfTheorySummary,
    PathSearchSummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_provenance import (
    ReactionEntrySummary,
    ReactionFullCalculationArtifacts,
    ReactionFullIRCItem,
    ReactionFullPathSearchItem,
    ReactionFullReadRequest,
    ReactionFullScanItem,
    ReactionFullSpecies,
    ReactionFullSpeciesParticipant,
    RequestEcho,
    ReviewDetail,
    ReviewRecordEntry,
    ScientificReactionFullResponse,
    TransitionStateCalculationSlot,
    TransitionStateDependency,
    TransitionStateInFull,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    visible_statuses,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.calculations import (
    _build_artifacts,
    _build_irc_include_summary,
    _build_path_search_include_summary,
    _build_scan_include_summary,
)
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.transition_states import (
    _build_evidence_summary_for_entries,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "species",
    "kinetics",
    "transition_states",
    "calculations",
    "path_search",
    "irc",
    "scans",
    "conformers",
    "artifacts",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

_DEFAULT_INCLUDES: set[str] = {"species", "kinetics", "transition_states"}


def get_reaction_full(
    session: Session,
    *,
    reaction_entry_id: int,
    request: ReactionFullReadRequest,
) -> ScientificReactionFullResponse:
    """Composite read for a reaction entry.

    Joins species, kinetics, transition states, calculations, and review
    summary into one document. Sub-arrays are deterministically ordered per
    L3. Top-level filters (``min_review_status`` / ``include_rejected`` /
    ``include_deprecated``) apply per joined sub-array's primary records and
    do not remove the parent reaction_entry.

    Non-TS-backed kinetics are returned in ``kinetics`` with null TS-chain
    provenance fields per Phase 2.2; the ``transition_states`` sub-array
    contains only TS rows actually associated with the reaction entry.

    :raises NotFoundError: 404 when ``reaction_entry_id`` is unknown.
    :raises ValueError: 422 for sort/include validation.
    """
    reject_client_sort(request.sort)
    includes = validate_includes(
        request.include or sorted(_DEFAULT_INCLUDES),
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/reaction-entries/{id}/full",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    ) or _DEFAULT_INCLUDES
    includes = filter_internal_ids_from_resolved(includes)

    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:
        raise NotFoundError(
            f"reaction_entry not found (reaction_entry_id={reaction_entry_id})"
        )

    chem = session.get(ChemReaction, entry.reaction_id)
    family_name: str | None = None
    if chem is not None and chem.reaction_family_id is not None:
        family_name = session.scalar(
            select(ReactionFamily.name).where(
                ReactionFamily.id == chem.reaction_family_id
            )
        )

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    # Top-level entry badge (always returned regardless of filter).
    entry_badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.reaction_entry,
        record_ids=[reaction_entry_id],
    )
    entry_badge = entry_badges[reaction_entry_id]

    reaction_entry_summary = ReactionEntrySummary(
        id=entry.id,
        reaction_entry_ref=entry.public_ref,
        reaction_id=entry.reaction_id,
        reaction_ref=chem.public_ref if chem is not None else "",
        equation=_format_entry_equation(session, entry, chem),
        reversible=chem.reversible if chem else True,
        family=family_name,
        review=entry_badge,
    )

    # Build each requested sub-section.
    species_block: ReactionFullSpecies | None = None
    if "species" in includes:
        species_block = _build_species_section(session, reaction_entry_id, visible)

    kinetics_block: list | None = None
    if "kinetics" in includes:
        kinetics_block = _build_kinetics_section(
            session,
            reaction_entry_id,
            request,
            visible,
        )

    ts_block: list[TransitionStateInFull] | None = None
    if "transition_states" in includes:
        ts_block = _build_transition_states_section(
            session, reaction_entry_id, visible
        )

    calculations_block: list[CalculationEvidenceSummary] | None = None
    if "calculations" in includes:
        calculations_block = _build_calculations_section(
            session, reaction_entry_id
        )

    path_search_block: list[ReactionFullPathSearchItem] | None = None
    if "path_search" in includes:
        path_search_block = _build_path_search_section(session, reaction_entry_id)

    irc_block: list[ReactionFullIRCItem] | None = None
    if "irc" in includes:
        irc_block = _build_irc_section(session, reaction_entry_id)

    scans_block: list[ReactionFullScanItem] | None = None
    if "scans" in includes:
        scans_block = _build_scans_section(session, reaction_entry_id)
    conformers_block: list[dict] | None = [] if "conformers" in includes else None
    artifacts_block: list[ReactionFullCalculationArtifacts] | None = None
    if "artifacts" in includes:
        artifacts_block = _build_artifacts_section(session, reaction_entry_id)

    # Hosted abuse-control caps: reject responses that would expand
    # beyond the configured public limits. ``include=all`` is what
    # most often pushes a heavily-studied reaction over the edge, but
    # the cap applies regardless of how the section was requested so
    # there is no way to bypass by enumerating tokens. The artifacts
    # cap counts individual artifact rows (the heavy payload), not the
    # number of grouping calcs.
    _enforce_full_expansion_caps(
        calculations=calculations_block,
        geometries=None,  # geometries not currently expanded in /full
        artifacts=(
            [a for group in artifacts_block for a in group.artifacts]
            if artifacts_block is not None
            else None
        ),
    )

    review_records_block: list[ReviewRecordEntry] | None = None
    if request.include_review == ReviewDetail.full:
        review_records_block = _build_review_records_section(
            session, reaction_entry_id
        )

    # Aggregate review_summary across visible sections' primary records.
    aggregate_badges: list[RecordReviewBadge] = [entry_badge]
    if species_block is not None:
        aggregate_badges.extend(
            p.review for p in (species_block.reactants + species_block.products)
        )
    if kinetics_block:
        aggregate_badges.extend(k.review for k in kinetics_block)
    if ts_block:
        aggregate_badges.extend(ts.review for ts in ts_block)

    summary = review_summary(aggregate_badges)

    return ScientificReactionFullResponse(
        request=RequestEcho(
            include=sorted(includes),
            include_review=request.include_review,
        ),
        reaction_entry=reaction_entry_summary,
        review_summary=summary,
        species=species_block,
        kinetics=kinetics_block,
        transition_states=ts_block,
        calculations=calculations_block,
        path_search=path_search_block,
        irc=irc_block,
        scans=scans_block,
        conformers=conformers_block,
        artifacts=artifacts_block,
        review_records=review_records_block,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_species_section(
    session: Session,
    reaction_entry_id: int,
    visible_review_statuses: set,
) -> ReactionFullSpecies:
    rows = session.execute(
        select(
            ReactionEntryStructureParticipant.species_entry_id,
            SpeciesEntry.public_ref,
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
            Species.smiles,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
        )
    ).all()

    badge_by_entry = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=[r[0] for r in rows],
    )

    reactants: list[ReactionFullSpeciesParticipant] = []
    products: list[ReactionFullSpeciesParticipant] = []
    for species_entry_id, species_entry_ref, role, participant_index, smiles in rows:
        badge = badge_by_entry[species_entry_id]
        if badge.status not in visible_review_statuses:
            continue
        participant = ReactionFullSpeciesParticipant(
            species_entry_id=species_entry_id,
            species_entry_ref=species_entry_ref,
            smiles=smiles,
            participant_index=participant_index,
            review=badge,
        )
        if role == ReactionRole.reactant:
            reactants.append(participant)
        else:
            products.append(participant)

    reactants.sort(
        key=lambda p: (REVIEW_RANK[p.review.status], p.participant_index, p.species_entry_id)
    )
    products.sort(
        key=lambda p: (REVIEW_RANK[p.review.status], p.participant_index, p.species_entry_id)
    )
    return ReactionFullSpecies(reactants=reactants, products=products)


def _build_kinetics_section(
    session: Session,
    reaction_entry_id: int,
    request: ReactionFullReadRequest,
    visible_review_statuses: set,
) -> list:
    """Reuse get_reaction_kinetics to ensure identical KineticsRecord shape.

    Top-level filters cascade to the kinetics endpoint via a fresh request.
    """
    kinetics_request = KineticsReadRequest(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
        # Always-present provenance keys are automatic; default include set.
        include=["provenance"],
        # Pagination wide-open; /full returns the full kinetics list.
        offset=0,
        limit=200,
    )
    response = get_reaction_kinetics(
        session,
        reaction_entry_id=reaction_entry_id,
        request=kinetics_request,
    )
    return list(response.records)


def _build_transition_states_section(
    session: Session,
    reaction_entry_id: int,
    visible_review_statuses: set,
) -> list[TransitionStateInFull]:
    ts_rows = session.scalars(
        select(TransitionState).where(
            TransitionState.reaction_entry_id == reaction_entry_id
        )
    ).all()
    if not ts_rows:
        return []

    ts_ref_by_id: dict[int, str] = {t.id: t.public_ref for t in ts_rows}

    ts_entry_rows = session.scalars(
        select(TransitionStateEntry).where(
            TransitionStateEntry.transition_state_id.in_([t.id for t in ts_rows])
        )
    ).all()

    badge_by_entry = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=[t.id for t in ts_entry_rows],
    )

    # Calculations + dependencies per TS entry.
    ts_entry_ids = [t.id for t in ts_entry_rows]
    calcs_by_ts_entry = _calcs_by_ts_entry(session, ts_entry_ids)
    deps_by_ts_entry = _deps_by_ts_entry(session, calcs_by_ts_entry)

    out: list[TransitionStateInFull] = []
    for ts_entry in ts_entry_rows:
        badge = badge_by_entry[ts_entry.id]
        if badge.status not in visible_review_statuses:
            continue
        ts_calcs = calcs_by_ts_entry.get(ts_entry.id, [])
        calc_refs = {c.id: c.public_ref for c in ts_calcs}
        # Reuse the evidence summary builder from the scientific TS
        # surface so the block surfaced under /full is byte-identical
        # to ``record.evidence_summary`` from
        # ``GET /scientific/transition-state-entries/{ref}``.
        evidence = _build_evidence_summary_for_entries(session, [ts_entry.id])
        out.append(
            TransitionStateInFull(
                transition_state_id=ts_entry.transition_state_id,
                transition_state_ref=ts_ref_by_id[ts_entry.transition_state_id],
                transition_state_entry_id=ts_entry.id,
                transition_state_entry_ref=ts_entry.public_ref,
                status=ts_entry.status,
                review=badge,
                evidence_summary=evidence,
                calculations=_format_ts_calc_slots(ts_calcs),
                dependencies=_format_ts_deps(
                    deps_by_ts_entry.get(ts_entry.id, []), calc_refs
                ),
            )
        )

    out.sort(
        key=lambda ts: (
            REVIEW_RANK[ts.review.status],
            -ts.transition_state_entry_id,
        )
    )
    return out


def _build_calculations_section(
    session: Session, reaction_entry_id: int
) -> list[CalculationEvidenceSummary]:
    """All calculations whose TS entry belongs to this reaction entry."""
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.public_ref,
            Calculation.type,
            Calculation.lot_id,
            LevelOfTheory.public_ref,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.dispersion,
            LevelOfTheory.solvent,
            Calculation.software_release_id,
            SoftwareRelease.public_ref,
            Software.name,
            SoftwareRelease.version,
            CalculationGeometryValidation.validation_status,
            CalculationSCFStability.status,
        )
        .join(
            TransitionStateEntry,
            TransitionStateEntry.id == Calculation.transition_state_entry_id,
        )
        .join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        )
        .join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id, isouter=True)
        .join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
            isouter=True,
        )
        .join(Software, Software.id == SoftwareRelease.software_id, isouter=True)
        .join(
            CalculationGeometryValidation,
            CalculationGeometryValidation.calculation_id == Calculation.id,
            isouter=True,
        )
        .join(
            CalculationSCFStability,
            CalculationSCFStability.calculation_id == Calculation.id,
            isouter=True,
        )
        .where(TransitionState.reaction_entry_id == reaction_entry_id)
        .order_by(Calculation.created_at.desc(), Calculation.id.desc())
    ).all()

    return [
        CalculationEvidenceSummary(
            calculation_id=row[0],
            calculation_ref=row[1],
            calculation_type=row[2].value,
            converged=None,
            geometry_validation_status=row[13].value if row[13] else "not_present",
            scf_stability_status=row[14].value if row[14] else "not_present",
            level_of_theory=(
                LevelOfTheorySummary(
                    level_of_theory_id=row[3],
                    level_of_theory_ref=row[4],
                    method=row[5] or "",
                    basis=row[6],
                    dispersion=row[7],
                    solvent=row[8],
                    label="/".join(p for p in (row[5] or "", row[6]) if p),
                )
                if row[3] is not None
                else None
            ),
            software=(
                SoftwareReleaseSummary(
                    software_release_id=row[9],
                    software_release_ref=row[10],
                    software=row[11] or "",
                    version=row[12],
                )
                if row[9] is not None
                else None
            ),
        )
        for row in rows
    ]


def _build_artifacts_section(
    session: Session, reaction_entry_id: int
) -> list[ReactionFullCalculationArtifacts]:
    """Group artifact metadata by reachable calculation.

    Reachability matches ``_build_calculations_section`` (calcs whose
    TS entry belongs to this reaction entry). Per-calc artifact rows
    come from the same ``_build_artifacts`` helper that powers
    ``include=artifacts`` on the calculation detail endpoint — the
    grouped surface and the calc-detail surface stay in sync by
    construction. Calcs with no artifact rows are omitted so empty
    groups don't clutter the response.

    Deterministic order:

    - Outer (groups): ``calculation_id`` ASC.
    - Inner (per-calc artifacts): inherited from ``_build_artifacts``
      (``kind`` ASC, ``created_at`` ASC nulls last, ``id`` ASC).
    """
    calc_rows = session.execute(
        select(Calculation.id, Calculation.public_ref, Calculation.type)
        .join(
            TransitionStateEntry,
            TransitionStateEntry.id == Calculation.transition_state_entry_id,
        )
        .join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        )
        .where(TransitionState.reaction_entry_id == reaction_entry_id)
        .order_by(Calculation.id.asc())
    ).all()

    out: list[ReactionFullCalculationArtifacts] = []
    for cid, cref, ctype in calc_rows:
        artifacts = _build_artifacts(session, cid)
        if not artifacts:
            continue
        out.append(
            ReactionFullCalculationArtifacts(
                calculation_id=cid,
                calculation_ref=cref,
                calculation_type=ctype,
                artifacts=artifacts,
            )
        )
    return out


def _calcs_of_type_for_reaction(
    session: Session,
    reaction_entry_id: int,
    calc_type: CalculationType,
) -> list[tuple[int, str]]:
    """Return ``[(calculation_id, calculation_ref), ...]`` for *calc_type*
    calcs whose TS entry belongs to *reaction_entry_id*. Ordered newest-
    first (id desc) — deterministic, no caller-supplied sort.
    """
    rows = session.execute(
        select(Calculation.id, Calculation.public_ref)
        .join(
            TransitionStateEntry,
            TransitionStateEntry.id == Calculation.transition_state_entry_id,
        )
        .join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        )
        .where(
            TransitionState.reaction_entry_id == reaction_entry_id,
            Calculation.type == calc_type,
        )
        .order_by(Calculation.id.desc())
    ).all()
    return [(row[0], row[1]) for row in rows]


def _scan_endpoint(calc_ref: str) -> str:
    return f"/api/v1/scientific/calculations/{calc_ref}/scan"


def _irc_endpoint(calc_ref: str) -> str:
    return f"/api/v1/scientific/calculations/{calc_ref}/irc"


def _path_search_endpoint(calc_ref: str) -> str:
    return f"/api/v1/scientific/calculations/{calc_ref}/path-search"


def _build_scans_section(
    session: Session, reaction_entry_id: int
) -> list[ReactionFullScanItem]:
    """Return one summary per scan calc reachable via this reaction entry's TS.

    Each item is byte-identical to ``record.scan`` from the calculation
    detail endpoint's ``include=scan`` projection — point arrays and
    coordinate-value rows live only behind the specialized
    ``/calculations/{ref}/scan`` endpoint (referenced by ``endpoint``).
    """
    return [
        ReactionFullScanItem(
            calculation_id=cid,
            calculation_ref=ref,
            endpoint=_scan_endpoint(ref),
            summary=_build_scan_include_summary(session, cid),
        )
        for cid, ref in _calcs_of_type_for_reaction(
            session, reaction_entry_id, CalculationType.scan
        )
    ]


def _build_path_search_section(
    session: Session, reaction_entry_id: int
) -> list[ReactionFullPathSearchItem]:
    """Return one summary per path-search calc for this reaction entry.

    Each item carries the ``include=path_search`` summary projection
    (method, n_points, ts_guess/climbing-image counts, energy and
    path-coordinate MIN/MAX aggregates). Per-image point arrays live
    only behind ``/calculations/{ref}/path-search``.
    """
    return [
        ReactionFullPathSearchItem(
            calculation_id=cid,
            calculation_ref=ref,
            endpoint=_path_search_endpoint(ref),
            summary=_build_path_search_include_summary(session, cid),
        )
        for cid, ref in _calcs_of_type_for_reaction(
            session, reaction_entry_id, CalculationType.path_search
        )
    ]


def _build_irc_section(
    session: Session, reaction_entry_id: int
) -> list[ReactionFullIRCItem]:
    """Return one summary per IRC calc for this reaction entry.

    Each item carries the ``include=irc`` summary projection (direction,
    forward/reverse counts, ts_point_count, energy + reaction-
    coordinate envelopes). Per-point arrays live only behind
    ``/calculations/{ref}/irc``.
    """
    return [
        ReactionFullIRCItem(
            calculation_id=cid,
            calculation_ref=ref,
            endpoint=_irc_endpoint(ref),
            summary=_build_irc_include_summary(session, cid),
        )
        for cid, ref in _calcs_of_type_for_reaction(
            session, reaction_entry_id, CalculationType.irc
        )
    ]


def _build_review_records_section(
    session: Session, reaction_entry_id: int
) -> list[ReviewRecordEntry]:
    """Audit-style review history across the joined records."""
    relevant_record_ids: dict[SubmissionRecordType, set[int]] = {
        SubmissionRecordType.reaction_entry: {reaction_entry_id},
        SubmissionRecordType.kinetics: set(
            session.scalars(
                select(Kinetics.id).where(Kinetics.reaction_entry_id == reaction_entry_id)
            ).all()
        ),
        SubmissionRecordType.transition_state_entry: set(
            session.scalars(
                select(TransitionStateEntry.id)
                .join(TransitionState, TransitionState.id == TransitionStateEntry.transition_state_id)
                .where(TransitionState.reaction_entry_id == reaction_entry_id)
            ).all()
        ),
    }

    out: list[ReviewRecordEntry] = []
    for record_type, ids in relevant_record_ids.items():
        if not ids:
            continue
        rows = session.scalars(
            select(RecordReview).where(
                RecordReview.record_type == record_type,
                RecordReview.record_id.in_(ids),
            )
        ).all()
        for r in rows:
            out.append(
                ReviewRecordEntry(
                    record_type=record_type.value,
                    record_id=r.record_id,
                    status=r.status,
                    reviewed_at=r.reviewed_at,
                )
            )
    return out


# ---------------------------------------------------------------------------
# TS calc / dependency helpers
# ---------------------------------------------------------------------------


def _calcs_by_ts_entry(
    session: Session, ts_entry_ids: list[int]
) -> dict[int, list[Calculation]]:
    if not ts_entry_ids:
        return {}
    rows = session.scalars(
        select(Calculation).where(
            Calculation.transition_state_entry_id.in_(ts_entry_ids)
        )
    ).all()
    grouped: dict[int, list[Calculation]] = {tid: [] for tid in ts_entry_ids}
    for c in rows:
        grouped[c.transition_state_entry_id].append(c)
    return grouped


def _format_ts_calc_slots(
    calcs: list[Calculation],
) -> dict[str, TransitionStateCalculationSlot]:
    """Map calculation_type → slot for the per-TS-entry calculations dict.

    Uses canonical short keys (ts_opt, ts_freq, ts_sp, ts_guess, ts_irc).
    Multiple calcs of the same type — the most recent wins.
    """
    type_to_key = {
        CalculationType.opt: "ts_opt",
        CalculationType.freq: "ts_freq",
        CalculationType.sp: "ts_sp",
        CalculationType.path_search: "ts_guess",
        CalculationType.irc: "ts_irc",
    }
    by_key: dict[str, TransitionStateCalculationSlot] = {}
    # Sort by id desc so the most recent calc wins for duplicates.
    for c in sorted(calcs, key=lambda c: -c.id):
        key = type_to_key.get(c.type)
        if key is None or key in by_key:
            continue
        method = None
        if c.type == CalculationType.path_search and isinstance(
            c.parameters_json, dict
        ):
            m = c.parameters_json.get("method")
            method = m if isinstance(m, str) else None
        by_key[key] = TransitionStateCalculationSlot(
            calculation_id=c.id,
            calculation_ref=c.public_ref,
            type=c.type.value,
            method=method,
        )
    return by_key


def _deps_by_ts_entry(
    session: Session, calcs_by_ts_entry: dict[int, list[Calculation]]
) -> dict[int, list[CalculationDependency]]:
    """Look up dependency edges among the TS-entry calculations only."""
    grouped: dict[int, list[CalculationDependency]] = {}
    for ts_entry_id, calcs in calcs_by_ts_entry.items():
        if not calcs:
            grouped[ts_entry_id] = []
            continue
        calc_ids = {c.id for c in calcs}
        rows = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id.in_(calc_ids),
                CalculationDependency.child_calculation_id.in_(calc_ids),
            )
        ).all()
        grouped[ts_entry_id] = list(rows)
    return grouped


def _format_ts_deps(
    deps: list[CalculationDependency],
    calc_refs: dict[int, str],
) -> list[TransitionStateDependency]:
    """Map ORM ``CalculationDependency`` rows to the read-schema dep shape.

    The ORM column is ``dependency_role`` (an SAEnum); the read schema
    field is named ``role`` for client ergonomics. Earlier versions
    accessed ``d.role`` directly which would have raised
    ``AttributeError`` the first time a TS calculation graph existed for
    a queried reaction — see Phase 7.1.
    """
    return [
        TransitionStateDependency(
            parent_calculation_id=d.parent_calculation_id,
            parent_calculation_ref=calc_refs.get(d.parent_calculation_id, ""),
            child_calculation_id=d.child_calculation_id,
            child_calculation_ref=calc_refs.get(d.child_calculation_id, ""),
            role=d.dependency_role.value,
        )
        for d in deps
    ]


# ---------------------------------------------------------------------------
# Equation formatter
# ---------------------------------------------------------------------------


def _enforce_full_expansion_caps(
    *,
    calculations: list | None,
    geometries: list | None,
    artifacts: list | None,
) -> None:
    """Reject /full responses whose expanded sub-arrays exceed the caps.

    Each section has its own configurable ceiling; we raise the first
    section that breaches it so the caller knows exactly which
    sub-array is the offender. The 422 ``query_too_expensive`` code
    is stable.
    """
    pairs: list[tuple[str, list | None, int]] = [
        ("calculations", calculations, settings.max_full_calculations_public),
        ("geometries", geometries, settings.max_full_geometries_public),
        ("artifacts", artifacts, settings.max_full_artifacts_public),
    ]
    for section_name, block, cap in pairs:
        if block is None or cap <= 0:
            continue
        if len(block) > cap:
            raise ValueError(
                "query_too_expensive: /full expansion for section "
                f"{section_name!r} would return {len(block)} rows "
                f"which exceeds the public cap of {cap}. Narrow the "
                "include= set or request specific sections directly."
            )


def _format_entry_equation(
    session: Session, entry: ReactionEntry, chem: ChemReaction | None
) -> str:
    rows = session.execute(
        select(
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
            Species.smiles,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == entry.id
        )
    ).all()
    reactants = sorted(
        [(idx, smiles) for role, idx, smiles in rows if role == ReactionRole.reactant],
        key=lambda x: x[0],
    )
    products = sorted(
        [(idx, smiles) for role, idx, smiles in rows if role == ReactionRole.product],
        key=lambda x: x[0],
    )
    arrow = "<=>" if (chem is None or chem.reversible) else "->"
    left = " + ".join(s for _, s in reactants)
    right = " + ".join(s for _, s in products)
    return f"{left} {arrow} {right}"
