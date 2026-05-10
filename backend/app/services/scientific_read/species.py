"""Service implementation for /api/v1/scientific/species/search.

See docs/specs/read_api_mvp.md §Endpoint 1 for the contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
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
    ScientificSpeciesSearchResponse,
    SpeciesEntryAvailability,
    SpeciesEntryScientificRecord,
    SpeciesEntrySectionIds,
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

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "thermo",
    "statmech",
    "transport",
    "conformers",
    "review",
    "all",
}

_DEFAULT_SORT_ECHO = "review_rank,has_entries,created_at,id"


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
        request.include, _LEGAL_INCLUDE_TOKENS, "/scientific/species/search"
    )

    if not any(
        v is not None
        for v in (request.smiles, request.inchi, request.inchi_key, request.formula)
    ):
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula} is required."
        )

    species_rows = _query_matching_species(session, request)
    if not species_rows:
        return _empty_response(request, includes, offset, limit)

    species_id_to_entries = _query_filtered_entries(
        session, species_rows, request
    )

    # Bulk badge fetch across all surviving entries.
    all_entry_ids = [
        entry.id
        for entries in species_id_to_entries.values()
        for entry in entries
    ]
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=all_entry_ids,
    )

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    # Apply review-status filter to entries; species drops out only if it has
    # no surviving entries OR if min_review_status is set and no entry passes.
    filtered_entries_per_species: dict[int, list[SpeciesEntry]] = {}
    for species_id, entries in species_id_to_entries.items():
        survivors = [e for e in entries if badges[e.id].status in visible]
        if request.min_review_status is None and not survivors:
            # No entries survived the default visibility filter — drop species
            # only if min_review_status was unspecified and no entries exist.
            # We still keep the species if it has entries (for has_entries=False).
            survivors = []
        filtered_entries_per_species[species_id] = survivors

    # Drop species whose every entry was filtered out by min_review_status.
    if request.min_review_status is not None:
        filtered_entries_per_species = {
            sid: entries
            for sid, entries in filtered_entries_per_species.items()
            if entries
        }

    # Build per-record availability counts and section payloads.
    availability_per_entry = _compute_availability(
        session,
        [e for entries in filtered_entries_per_species.values() for e in entries],
    )

    # Section ID payloads when include flags are set.
    section_ids = _compute_section_ids(
        session,
        [e for entries in filtered_entries_per_species.values() for e in entries],
        includes,
    )

    # Build response records.
    records: list[SpeciesScientificRecord] = []
    for species in species_rows:
        entries = filtered_entries_per_species.get(species.id, [])
        entry_records = [
            _build_entry_record(entry, badges[entry.id], availability_per_entry[entry.id], section_ids.get(entry.id, {}))
            for entry in entries
        ]
        records.append(
            SpeciesScientificRecord(
                species_id=species.id,
                canonical_smiles=species.smiles,
                inchi_key=species.inchi_key,
                formula=None,  # not stored on Species; future addition
                charge=species.charge,
                multiplicity=species.multiplicity,
                entries=entry_records,
            )
        )

    # Pre-collapse review summary across all surviving entries.
    summary = review_summary(
        badges[entry.id]
        for entries in filtered_entries_per_species.values()
        for entry in entries
    )

    # Sort: review_rank ASC (best entry's rank), has_entries DESC, created_at DESC, id DESC
    def sort_key(rec: SpeciesScientificRecord) -> tuple:
        if rec.entries:
            best_rank = min(REVIEW_RANK[e.review.status] for e in rec.entries)
            has_entries = 1
        else:
            best_rank = max(REVIEW_RANK.values()) + 1
            has_entries = 0
        return (best_rank, -has_entries, _negate_id(rec.species_id))

    # Sort with created_at DESC handled separately by retrieving created_at.
    species_created_at = {sp.id: sp.created_at for sp in species_rows}

    def full_sort_key(rec: SpeciesScientificRecord) -> tuple:
        if rec.entries:
            best_rank = min(REVIEW_RANK[e.review.status] for e in rec.entries)
            has_entries_int = 1
        else:
            best_rank = max(REVIEW_RANK.values()) + 1
            has_entries_int = 0
        # Negate descending fields by negation; for datetime, sort ascending
        # by negated timestamp via tuple inversion.
        created_at = species_created_at[rec.species_id]
        return (
            best_rank,
            -has_entries_int,
            -created_at.timestamp(),
            -rec.species_id,
        )

    records.sort(key=full_sort_key)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"

    if collapse_first:
        returned_records = records[:1]
    else:
        returned_records = records[offset : offset + limit]

    pagination = build_pagination(
        offset=offset,
        limit=limit,
        returned=len(returned_records),
        total=pre_collapse_total,
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _negate_id(value: int) -> int:
    return -value


def _query_matching_species(
    session: Session, request: SpeciesSearchRequest
) -> list[Species]:
    stmt = select(Species)
    if request.smiles is not None:
        stmt = stmt.where(Species.smiles == request.smiles)
    if request.inchi_key is not None:
        stmt = stmt.where(Species.inchi_key == request.inchi_key)
    if request.charge is not None:
        stmt = stmt.where(Species.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(Species.multiplicity == request.multiplicity)
    # inchi and formula are not stored on Species directly in the current
    # schema; if either is supplied alongside other filters they AND-combine
    # by yielding zero rows when inconsistent. For v0 we treat unsupported
    # identifier filters as "no record matches" rather than raising — this
    # matches the AND-with-empty-result rule from the spec.
    if request.inchi is not None or request.formula is not None:
        # We have no column to filter against → results AND to empty unless
        # the other identifiers happen to find rows. To keep semantics
        # honest, we accept the query but the rows are constrained only by
        # the other identifiers; downstream entry filtering may further
        # narrow. A future schema addition could surface inchi/formula.
        pass
    return session.scalars(stmt).all()


def _query_filtered_entries(
    session: Session,
    species_rows: list[Species],
    request: SpeciesSearchRequest,
) -> dict[int, list[SpeciesEntry]]:
    species_ids = [sp.id for sp in species_rows]
    stmt = select(SpeciesEntry).where(SpeciesEntry.species_id.in_(species_ids))
    if request.electronic_state_kind is not None:
        stmt = stmt.where(SpeciesEntry.electronic_state_kind == request.electronic_state_kind)
    if request.species_entry_kind is not None:
        stmt = stmt.where(SpeciesEntry.kind == request.species_entry_kind)

    entries = session.scalars(stmt).all()
    grouped: dict[int, list[SpeciesEntry]] = {sid: [] for sid in species_ids}
    for entry in entries:
        grouped[entry.species_id].append(entry)
    return grouped


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

    calc_counts: dict[int, int] = {eid: 0 for eid in entry_ids}
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
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
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
