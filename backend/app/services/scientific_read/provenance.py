"""Service implementation for /api/v1/scientific/reaction-entries/{id}/full.

Composite document: joins species, kinetics, transition states, calculations,
review summary into a single response. See docs/specs/read_api_mvp.md §Endpoint 5.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.api.errors import not_found
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    ReactionRole,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.reaction_atom_map import (
    ReactionAtomMap,
    ReactionAtomMapPair,
)
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    LevelOfTheorySummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_provenance import (
    ReactionAtomMapBadge,
    ReactionAtomMapDetail,
    ReactionAtomMapPairRead,
    ReactionEntrySummary,
    ReactionFullCalculationArtifacts,
    ReactionFullCalculationEvidenceSummary,
    ReactionFullConformerGroupItem,
    ReactionFullIRCItem,
    ReactionFullPathSearchItem,
    ReactionFullReadRequest,
    ReactionFullScanItem,
    ReactionFullSpecies,
    ReactionFullSpeciesConformers,
    ReactionFullSpeciesParticipant,
    RequestEcho,
    ReviewDetail,
    ReviewRecordEntry,
    ScientificReactionFullResponse,
    TransitionStateCalculationSlot,
    TransitionStateDependency,
    TransitionStateInFull,
)
from app.services.scientific_read import levels_of_theory
from app.services.scientific_read.calculations import (
    _TRUST_EAGER_LOADS as _CALCULATION_TRUST_EAGER_LOADS,
)
from app.services.scientific_read.calculations import (
    _build_artifacts,
    _build_irc_include_summary,
    _build_path_search_include_summary,
    _build_scan_include_summary,
    build_calculation_trust_fragment,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    visible_statuses,
)
from app.services.scientific_read.conformers import build_group_record
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.transition_states import (
    _TRUST_EAGER_LOADS as _TS_ENTRY_TRUST_EAGER_LOADS,
)
from app.services.scientific_read.transition_states import (
    _build_entry_evidence_summary,
    build_transition_state_entry_trust_fragment,
)

logger = logging.getLogger(__name__)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "species",
    "kinetics",
    "transition_states",
    "atom_map",
    "calculations",
    "path_search",
    "irc",
    "scans",
    "conformers",
    "artifacts",
    "review",
    "internal_ids",
    "trust",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "trust"}

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
    # Modifier-only requests (e.g. ``?include=trust``) should still
    # return the default section set; the modifier token only changes
    # the shape of records inside the embedded sections, not which
    # sections appear.
    if not (includes - _INTERNAL_INCLUDE_TOKENS):
        includes = (includes & _INTERNAL_INCLUDE_TOKENS) | _DEFAULT_INCLUDES
    trust_requested = "trust" in includes

    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:
        raise not_found("reaction_entry", row_id=reaction_entry_id)

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

    # The header badge is unconditional (a consumer must be able to tell a
    # mapped reaction from an unmapped one without a second request); the
    # per-atom legs are the opt-in ``atom_map`` section over the same maps.
    # Read the map headers once and let the section decide how much of each
    # map has to be loaded: the badge needs two counts per map, which the
    # database can produce without shipping a row per atom.
    atom_map_headers = _fetch_atom_map_headers(session, reaction_entry_id)
    atom_map_details: list[ReactionAtomMapDetail] | None = None
    if "atom_map" in includes:
        atom_map_details = _build_atom_map_details(session, atom_map_headers)
        atom_map_badges = [
            ReactionAtomMapBadge(
                **detail.model_dump(include=set(ReactionAtomMapBadge.model_fields))
            )
            for detail in atom_map_details
        ]
    else:
        atom_map_badges = _build_atom_map_badges(session, atom_map_headers)

    reaction_entry_summary = ReactionEntrySummary(
        id=entry.id,
        reaction_entry_ref=entry.public_ref,
        reaction_id=entry.reaction_id,
        reaction_ref=chem.public_ref if chem is not None else "",
        equation=_format_entry_equation(session, entry, chem),
        reversible=chem.reversible if chem else True,
        family=family_name,
        review=entry_badge,
        atom_maps=atom_map_badges,
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
            include_trust=trust_requested,
        )

    ts_block: list[TransitionStateInFull] | None = None
    if "transition_states" in includes:
        ts_block = _build_transition_states_section(
            session,
            reaction_entry_id,
            visible,
            include_trust=trust_requested,
        )

    calculations_block: list[ReactionFullCalculationEvidenceSummary] | None = None
    if "calculations" in includes:
        calculations_block = _build_calculations_section(
            session,
            reaction_entry_id,
            include_trust=trust_requested,
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
    conformers_block: list[ReactionFullSpeciesConformers] | None = None
    if "conformers" in includes:
        conformers_block = _build_conformers_section(
            session, reaction_entry_id, visible
        )
    artifacts_block: list[ReactionFullCalculationArtifacts] | None = None
    if "artifacts" in includes:
        artifacts_block = _build_artifacts_section(session, reaction_entry_id)
    atom_map_block: list[ReactionAtomMapDetail] | None = atom_map_details

    # Hosted abuse-control caps: reject responses that would expand
    # beyond the configured public limits. ``include=all`` is what
    # most often pushes a heavily-studied reaction over the edge, but
    # the cap applies regardless of how the section was requested so
    # there is no way to bypass by enumerating tokens. The artifacts
    # and conformers caps count the heavy *leaf* rows (individual
    # artifact / conformer-group rows), not the grouping outer rows.
    _enforce_full_expansion_caps(
        calculations=calculations_block,
        geometries=None,  # geometries not currently expanded in /full
        artifacts=(
            [a for group in artifacts_block for a in group.artifacts]
            if artifacts_block is not None
            else None
        ),
        conformer_groups=(
            [
                g
                for participant in conformers_block
                for g in participant.conformer_groups
            ]
            if conformers_block is not None
            else None
        ),
        # Same leaf-row rule: a reaction carries at most a handful of maps,
        # but each one holds a pair per atom per leg, so the pairs are what
        # can actually run away on a large saddle point. Counted only when
        # the section was expanded — the always-present ``atom_maps`` badge
        # on the header carries no pairs.
        atom_map_pairs=(
            [pair for detail in atom_map_block for pair in detail.pairs]
            if atom_map_block is not None
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
        atom_map=atom_map_block,
        review_records=review_records_block,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _fetch_atom_map_headers(
    session: Session,
    reaction_entry_id: int,
) -> list:
    """Return one row per atom map on this micro reaction, without its pairs.

    Not filtered by review status. The map is not a scientific *result* that
    could be rejected on its own; it is the correspondence the depositor
    asserted between structures that carry their own review state, and
    dropping it under a filter would make a mapped reaction read as unmapped —
    which is precisely the indistinguishability ADR 0011 exists to remove.

    An empty list therefore means the record genuinely carries no map.
    """

    return session.execute(
        select(
            ReactionAtomMap.id,
            ReactionAtomMap.transition_state_entry_id,
            TransitionStateEntry.public_ref,
            ReactionAtomMap.transition_state_geometry_id,
            Geometry.public_ref,
            ReactionAtomMap.source,
            ReactionAtomMap.equivalent_map_count,
            ReactionAtomMap.note,
        )
        .join(
            TransitionStateEntry,
            TransitionStateEntry.id == ReactionAtomMap.transition_state_entry_id,
        )
        .join(Geometry, Geometry.id == ReactionAtomMap.transition_state_geometry_id)
        .where(ReactionAtomMap.reaction_entry_id == reaction_entry_id)
        .order_by(ReactionAtomMap.transition_state_entry_id, ReactionAtomMap.id)
    ).all()


def _build_atom_map_badges(
    session: Session,
    maps: list,
) -> list[ReactionAtomMapBadge]:
    """Return the header badge for each atom map, counting pairs in the database.

    The badge says a map exists, on whose authority, and how many atoms of
    each leg it accounts for. Those last two are the only thing it needs the
    pairs for, and they are counts — so they are taken as counts. Building the
    badge by loading a row per atom per leg made an unconditional field on
    every ``/full`` response scale with the size of the saddle point, for a
    consumer that asked for none of it; a large map is thousands of rows read
    and discarded. ``include=atom_map`` still materialises the pairs, because
    then the caller has asked for them.

    An atom map with no pairs is not expected but is not refused either: it
    reads back as a map that accounts for zero atoms on both legs, which is
    what it is.
    """

    if not maps:
        return []

    map_ids = [row[0] for row in maps]
    counts: dict[tuple[int, ReactionRole], int] = {
        (atom_map_id, side): count
        for atom_map_id, side, count in session.execute(
            select(
                ReactionAtomMapPair.atom_map_id,
                ReactionAtomMapPair.side,
                func.count(),
            )
            .where(ReactionAtomMapPair.atom_map_id.in_(map_ids))
            .group_by(ReactionAtomMapPair.atom_map_id, ReactionAtomMapPair.side)
        ).all()
    }

    # Unpacked rather than indexed, so the header select's column order is
    # named in both consumers and a reordering breaks loudly.
    return [
        ReactionAtomMapBadge(
            transition_state_entry_id=ts_entry_id,
            transition_state_entry_ref=ts_entry_ref,
            source=source,
            equivalent_map_count=equivalent_map_count,
            note=note,
            reactant_atoms_mapped=counts.get(
                (atom_map_id, ReactionRole.reactant), 0
            ),
            product_atoms_mapped=counts.get((atom_map_id, ReactionRole.product), 0),
        )
        for (
            atom_map_id,
            ts_entry_id,
            ts_entry_ref,
            _ts_geometry_id,
            _ts_geometry_ref,
            source,
            equivalent_map_count,
            note,
        ) in maps
    ]


def _build_atom_map_details(
    session: Session,
    maps: list,
) -> list[ReactionAtomMapDetail]:
    """Return every atom map on this micro reaction, both legs, atom by atom.

    The opt-in ``include=atom_map`` expansion. Review-status filtering is
    deliberately absent for the reason given on :func:`_fetch_atom_map_headers`.
    """

    if not maps:
        return []

    map_ids = [row[0] for row in maps]
    pair_rows = session.execute(
        select(
            ReactionAtomMapPair.atom_map_id,
            ReactionAtomMapPair.side,
            ReactionEntryStructureParticipant.species_entry_id,
            SpeciesEntry.public_ref,
            ReactionEntryStructureParticipant.participant_index,
            ReactionAtomMapPair.geometry_id,
            Geometry.public_ref,
            ReactionAtomMapPair.atom_index,
            ReactionAtomMapPair.element,
            ReactionAtomMapPair.ts_atom_index,
        )
        .join(
            ReactionEntryStructureParticipant,
            ReactionEntryStructureParticipant.id
            == ReactionAtomMapPair.structure_participant_id,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Geometry, Geometry.id == ReactionAtomMapPair.geometry_id)
        .where(ReactionAtomMapPair.atom_map_id.in_(map_ids))
        .order_by(
            ReactionAtomMapPair.atom_map_id,
            ReactionAtomMapPair.side,
            ReactionEntryStructureParticipant.participant_index,
            ReactionAtomMapPair.atom_index,
        )
    ).all()

    pairs_by_map: dict[int, list[ReactionAtomMapPairRead]] = {}
    for row in pair_rows:
        pairs_by_map.setdefault(row[0], []).append(
            ReactionAtomMapPairRead(
                side=row[1],
                species_entry_id=row[2],
                species_entry_ref=row[3],
                participant_index=row[4],
                geometry_id=row[5],
                geometry_ref=row[6],
                atom_index=row[7],
                element=row[8].strip(),
                ts_atom_index=row[9],
            )
        )

    details: list[ReactionAtomMapDetail] = []
    for (
        atom_map_id,
        ts_entry_id,
        ts_entry_ref,
        ts_geometry_id,
        ts_geometry_ref,
        source,
        equivalent_map_count,
        note,
    ) in maps:
        pairs = pairs_by_map.get(atom_map_id, [])
        details.append(
            ReactionAtomMapDetail(
                transition_state_entry_id=ts_entry_id,
                transition_state_entry_ref=ts_entry_ref,
                transition_state_geometry_id=ts_geometry_id,
                transition_state_geometry_ref=ts_geometry_ref,
                source=source,
                equivalent_map_count=equivalent_map_count,
                note=note,
                reactant_atoms_mapped=sum(
                    1 for pair in pairs if pair.side == ReactionRole.reactant
                ),
                product_atoms_mapped=sum(
                    1 for pair in pairs if pair.side == ReactionRole.product
                ),
                pairs=pairs,
            )
        )
    return details


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
    *,
    include_trust: bool = False,
) -> list:
    """Reuse get_reaction_kinetics to ensure identical KineticsRecord shape.

    Top-level filters cascade to the kinetics endpoint via a fresh
    request. When ``include_trust`` is True the kinetics request also
    carries ``trust`` so each :class:`KineticsRecord` returned here is
    byte-identical to what
    ``GET /scientific/reaction-entries/{id}/kinetics?include=trust``
    would emit for the same record.
    """
    kinetics_include = ["provenance"]
    if include_trust:
        kinetics_include.append("trust")
    kinetics_request = KineticsReadRequest(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
        # Always-present provenance keys are automatic; default include set.
        include=kinetics_include,
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
    *,
    include_trust: bool = False,
) -> list[TransitionStateInFull]:
    """Embed the reaction entry's TS-entry rows.

    When ``include_trust`` is True, each visible TS-entry additionally
    carries a ``computed_transition_state_v2`` trust fragment. The
    evidence graph the rubric walks is eagerly loaded via
    ``_TS_ENTRY_TRUST_EAGER_LOADS`` — the same tuple the standalone
    TS-entry trust read uses — so the loaded evaluator emits no hidden
    N+1 queries. The fragment is built through
    :func:`build_transition_state_entry_trust_fragment`, which calls the
    *loaded* evaluator (never the session/id wrapper), so an embedded
    TS-entry trust block is byte-identical to what
    ``GET /scientific/transition-state-entries/{ref}?include=trust``
    would emit for the same record.
    """
    ts_rows = session.scalars(
        select(TransitionState).where(
            TransitionState.reaction_entry_id == reaction_entry_id
        )
    ).all()
    if not ts_rows:
        return []

    ts_ref_by_id: dict[int, str] = {t.id: t.public_ref for t in ts_rows}

    ts_entry_query = select(TransitionStateEntry).where(
        TransitionStateEntry.transition_state_id.in_([t.id for t in ts_rows])
    )
    if include_trust:
        ts_entry_query = ts_entry_query.options(*_TS_ENTRY_TRUST_EAGER_LOADS)
    ts_entry_rows = session.scalars(ts_entry_query).all()

    badge_by_entry = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=[t.id for t in ts_entry_rows],
    )

    # Calculations + dependencies per TS entry.
    ts_entry_ids = [t.id for t in ts_entry_rows]
    calcs_by_ts_entry = _calcs_by_ts_entry(session, ts_entry_ids)
    deps_by_ts_entry = _deps_by_ts_entry(session, calcs_by_ts_entry)
    # One statement for the document's whole TS-entry set, matching the
    # ``_calcs_by_ts_entry`` / ``_deps_by_ts_entry`` batching above rather
    # than adding a round trip inside the loop.
    ts_levels = levels_of_theory.for_transition_state_entries(
        session, ts_entry_ids
    )

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
        evidence = _build_entry_evidence_summary(
            session, ts_entry.id, levels_index=ts_levels
        )
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
                trust=(
                    build_transition_state_entry_trust_fragment(
                        ts_entry,
                        review_status=badge.status,
                    )
                    if include_trust
                    else None
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
    session: Session,
    reaction_entry_id: int,
    *,
    include_trust: bool = False,
) -> list[ReactionFullCalculationEvidenceSummary]:
    """All calculations whose TS entry belongs to this reaction entry.

    When ``include_trust`` is True, each item additionally carries a
    ``computed_calculation_v1`` trust fragment. The calculation graph
    needed by the rubric is eagerly loaded via
    ``_CALCULATION_TRUST_EAGER_LOADS`` so the trust runners stay
    deterministic and do not emit hidden N+1 queries (the calculation
    detail endpoint uses the same eager-load tuple).
    """
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

    calc_ids = [row[0] for row in rows]
    trust_by_calc_id = (
        _build_calculation_trust_fragments(session, calc_ids)
        if include_trust
        else {}
    )

    return [
        ReactionFullCalculationEvidenceSummary(
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
            trust=trust_by_calc_id.get(row[0]),
        )
        for row in rows
    ]


def _build_calculation_trust_fragments(
    session: Session, calculation_ids: list[int]
):
    """Bulk-load calculations with trust eager loads and build fragments.

    Returns a ``{calculation_id: TrustFragment}`` mapping. Review badges
    are loaded once for the whole set so ``trust.review_status`` lines
    up with the standalone calculation detail surface.
    """
    if not calculation_ids:
        return {}

    calcs = session.scalars(
        select(Calculation)
        .options(*_CALCULATION_TRUST_EAGER_LOADS)
        .where(Calculation.id.in_(calculation_ids))
    ).all()
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=calculation_ids,
    )
    return {
        calc.id: build_calculation_trust_fragment(
            calc,
            review_status=badges[calc.id].status,
        )
        for calc in calcs
    }


def _conformer_group_endpoint(group_ref: str) -> str:
    return f"/api/v1/scientific/conformer-groups/{group_ref}"


def _build_conformers_section(
    session: Session,
    reaction_entry_id: int,
    visible_review_statuses: set,
) -> list[ReactionFullSpeciesConformers]:
    """Group conformer-group summaries by reaction participant species entry.

    Reachability: walk
    ``reaction_entry_structure_participant`` → ``species_entry`` →
    ``conformer_group`` (the ORM relationship is direct). One outer row
    per participant slot from the reaction-entry; if a participant has
    no conformer groups, the participant still appears with
    ``conformer_groups = []`` — symmetric with how the bounded
    available-sections / summary blocks elsewhere distinguish "section
    was requested but empty" from "section was not requested".

    Per-group items reuse the conformer detail surface's
    :func:`build_group_record` helper and then project to the smaller
    ``/full``-safe shape (core block + observations summary + evidence
    summary + selection summary + available_sections). Observation
    lists / calculation lists / geometry links / review history remain
    behind ``GET /scientific/conformer-groups/{ref}``.

    Participants whose species-entry review badge is outside the
    visible-statuses set are dropped, matching the species-section
    treatment.
    """
    participant_rows = session.execute(
        select(
            ReactionEntryStructureParticipant.species_entry_id,
            SpeciesEntry.id.label("entry_id"),
            SpeciesEntry.public_ref.label("entry_ref"),
            SpeciesEntry.species_id.label("species_id"),
            Species.public_ref.label("species_ref"),
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
        )
        .order_by(
            ReactionEntryStructureParticipant.role.asc(),
            ReactionEntryStructureParticipant.participant_index.asc(),
        )
    ).all()
    if not participant_rows:
        return []

    species_entry_ids = [row.entry_id for row in participant_rows]
    se_badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=species_entry_ids,
    )

    # Bulk-load conformer groups per species_entry. The relationship is
    # one species_entry → 0..N conformer_groups.
    cg_rows = session.scalars(
        select(ConformerGroup)
        .where(ConformerGroup.species_entry_id.in_(species_entry_ids))
        .order_by(
            ConformerGroup.species_entry_id.asc(),
            ConformerGroup.id.asc(),
        )
    ).all()
    groups_by_entry: dict[int, list[ConformerGroup]] = {
        sid: [] for sid in species_entry_ids
    }
    for g in cg_rows:
        groups_by_entry.setdefault(g.species_entry_id, []).append(g)

    # Bulk-load conformer-group review badges so the visibility filter
    # applies at group grain too (rejected groups disappear from /full
    # by default, matching the rest of the scientific surface).
    all_group_ids = [g.id for g in cg_rows]
    cg_badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.conformer_group,
            record_ids=all_group_ids,
        )
        if all_group_ids
        else {}
    )

    # As above: one statement for every conformer group in the document.
    group_levels = levels_of_theory.for_conformer_groups(
        session, list(all_group_ids)
    )

    out: list[ReactionFullSpeciesConformers] = []
    for row in participant_rows:
        se_badge = se_badges[row.entry_id]
        if se_badge.status not in visible_review_statuses:
            continue
        items: list[ReactionFullConformerGroupItem] = []
        for cg in groups_by_entry.get(row.entry_id, []):
            cg_badge = cg_badges[cg.id]
            if cg_badge.status not in visible_review_statuses:
                continue
            full_record = build_group_record(
                session,
                cg=cg,
                cg_badge=cg_badge,
                includes=set(),  # /full keeps the summary-safe default shape
                levels_map=group_levels.for_owner(cg.id),
            )
            items.append(
                ReactionFullConformerGroupItem(
                    conformer_group_id=cg.id,
                    conformer_group_ref=cg.public_ref,
                    endpoint=_conformer_group_endpoint(cg.public_ref),
                    conformer_group=full_record.conformer_group,
                    observations_summary=full_record.observations_summary,
                    evidence_summary=full_record.evidence_summary,
                    selection_summary=full_record.selection_summary,
                    available_sections=full_record.available_sections,
                )
            )
        out.append(
            ReactionFullSpeciesConformers(
                species_id=row.species_id,
                species_ref=row.species_ref,
                species_entry_id=row.entry_id,
                species_entry_ref=row.entry_ref,
                role=row.role,
                participant_index=row.participant_index,
                conformer_groups=items,
            )
        )
    return out


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
    conformer_groups: list | None = None,
    atom_map_pairs: list | None = None,
) -> None:
    """Reject /full responses whose expanded sub-arrays exceed the caps.

    Each section has its own configurable ceiling; we raise the first
    section that breaches it so the caller knows exactly which
    sub-array is the offender. The 422 ``query_too_expensive`` code
    is stable.

    ``conformer_groups`` is a flat list of individual group items
    (heavy leaf rows), not the species-participant grouping list — the
    cap counts conformer-group rows so a heavily-studied species with
    many basins can't tunnel past it via a single participant.

    ``atom_map_pairs`` is likewise flattened across the reaction's maps:
    a reaction has few maps and each holds one row per atom per leg, so
    the pairs are the leaf rows and the maps are the grouping ones.

    What the refusal publishes, and what it withholds
    -------------------------------------------------
    ``query_too_expensive`` is a
    :attr:`~app.api.code_catalogue.Shape.relationship` code as of
    2026-08-18 — it says a sub-array and a cap are the wrong way round
    and names neither — so it owes a client ``context``. It carries the
    offending ``section`` and that section's ``cap``, which is the whole
    repair: narrow ``include=``, or request the section directly with
    pagination.

    It deliberately does **not** carry ``len(block)``. That number is how
    many rows TCKDB *holds* for the requested record — how many
    calculations, geometries, artifacts, conformer groups or atom-map
    pairs exist. Unlike a geometry's atom count, which is a property of
    the molecule, this is a count of the corpus, and a caller who can
    read it off a cheap refusal has a row-counting oracle: set the cap
    low, sweep records, and recover the holdings profile and roughly the
    upload schedule that ``docs/specs/public_identifier_policy.md``
    (§"Why this matters now", item 3) refuses to leak through primary
    keys. The count is logged instead, and is absent from ``detail`` as
    well as ``context`` — ``detail`` is published too.
    """
    pairs: list[tuple[str, list | None, int]] = [
        ("calculations", calculations, settings.max_full_calculations_public),
        ("geometries", geometries, settings.max_full_geometries_public),
        ("artifacts", artifacts, settings.max_full_artifacts_public),
        (
            "conformer_groups",
            conformer_groups,
            settings.max_full_conformer_groups_public,
        ),
        (
            "atom_map_pairs",
            atom_map_pairs,
            settings.max_full_atom_map_pairs_public,
        ),
    ]
    for section_name, block, cap in pairs:
        if block is None or cap <= 0:
            continue
        if len(block) > cap:
            logger.info(
                "full expansion refused as query_too_expensive; section %r "
                "would return %d rows against a cap of %d",
                section_name,
                len(block),
                cap,
            )
            raise CodedValueError(
                "query_too_expensive",
                f"/full expansion for section {section_name!r} would "
                f"return more rows than the public cap of {cap}. Narrow "
                "the include= set or request specific sections directly.",
                context={"section": section_name, "cap": cap},
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
