"""Service for GET /api/v1/scientific/species-entries/{id}/observations.

A species-entry-scoped read of ``molecular_property_observation`` rows.
Mirrors ``get_species_transport`` (``species_transport.py``): pin the query
to one ``species_entry``, apply the shared review-visibility and pagination
helpers, and return the shared review-badged envelope. Unlike thermo /
statmech / transport, this record type carries no candidacy/selection
semantics — there is no ``collapse`` or ``selection_policy`` knob (see the
read schema's module docstring).

See ``docs/research/tckdb-phase-c-implementation-plan.md`` C5 and
``backend/docs/specs/trust_read_api_current.md``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.common import (
    MolecularPropertyKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.literature import Literature
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import SpeciesEntry
from app.schemas.reads.scientific_common import REVIEW_RANK, RecordReviewBadge
from app.schemas.reads.scientific_observation import (
    MolecularPropertyObservationRecord,
    ObservationExternalSource,
    ObservationUncertainty,
    RequestEcho,
    ScientificSpeciesObservationsResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    slice_for_pagination,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# No heavy include-gated sections exist on this surface; ``internal_ids`` is
# the only legal token, matching the sibling per-entry reads' pattern of
# marking it internal so ``include=all`` never expands to it.
_LEGAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# Deterministic ordering matches the sibling per-entry reads: review rank
# ASC, created_at DESC, id DESC.
_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def get_species_observations(
    session: Session,
    *,
    species_entry_id: int,
    property_kind: str | None = None,
    include: list[str] | None = None,
    min_review_status: RecordReviewStatus | None = None,
    include_rejected: bool = False,
    include_deprecated: bool = False,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificSpeciesObservationsResponse:
    """Return molecular-property observations for one species entry.

    Records are pinned to ``species_entry_id`` and returned in the shared
    deterministic order. ``property_kind=`` filters to one
    :class:`~app.db.models.common.MolecularPropertyKind`.

    :raises NotFoundError: 404 when ``species_entry_id`` is unknown.
    :raises ValueError: 422 for sort / include / pagination / property_kind
        violations.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species-entries/{id}/observations",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise not_found("species_entry", row_id=species_entry_id)
    species_entry_ref = entry.public_ref

    kind_filter = None
    if property_kind is not None:
        try:
            kind_filter = MolecularPropertyKind(property_kind)
        except ValueError as exc:
            raise ValueError(
                f"invalid_property_kind: {property_kind!r} is not a known "
                "molecular property kind"
            ) from exc

    query = select(
        MolecularPropertyObservation.id, MolecularPropertyObservation.created_at
    ).where(MolecularPropertyObservation.species_entry_id == species_entry_id)
    if kind_filter is not None:
        query = query.where(
            MolecularPropertyObservation.property_kind == kind_filter
        )
    rows = session.execute(query).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    filter_echo: dict[str, object] = {"species_entry_ref": species_entry_ref}
    if property_kind is not None:
        filter_echo["property_kind"] = property_kind

    if not candidate_ids:
        return _empty_response(filter_echo, includes, offset, limit)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.molecular_property_observation,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    visible_ids = [cid for cid in candidate_ids if badges[cid].status in visible]
    if not visible_ids:
        return _empty_response(filter_echo, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)
    total = len(visible_ids)
    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )
    page_ids = slice_for_pagination(
        visible_ids, offset=offset, limit=limit, collapse_first=False
    )
    records = _materialize_records(session, page_ids, badges)

    return ScientificSpeciesObservationsResponse(
        request=RequestEcho(
            filter=filter_echo,
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
) -> list[MolecularPropertyObservationRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(MolecularPropertyObservation).where(
            MolecularPropertyObservation.id.in_(page_ids)
        )
    ).all()
    by_id = {r.id: r for r in rows}

    literature_ids = {r.literature_id for r in rows if r.literature_id is not None}
    literature_by_id: dict[int, Literature] = {}
    if literature_ids:
        for lit in session.scalars(
            select(Literature).where(Literature.id.in_(literature_ids))
        ).all():
            literature_by_id[lit.id] = lit

    esr_ids = {
        r.external_source_record_id
        for r in rows
        if r.external_source_record_id is not None
    }
    esr_by_id: dict[int, ExternalSourceRecord] = {}
    source_by_esr: dict[int, ExternalSource] = {}
    if esr_ids:
        esr_rows = session.scalars(
            select(ExternalSourceRecord).where(ExternalSourceRecord.id.in_(esr_ids))
        ).all()
        for esr in esr_rows:
            esr_by_id[esr.id] = esr
        source_ids = {esr.external_source_id for esr in esr_rows}
        if source_ids:
            for src in session.scalars(
                select(ExternalSource).where(ExternalSource.id.in_(source_ids))
            ).all():
                for esr in esr_rows:
                    if esr.external_source_id == src.id:
                        source_by_esr[esr.id] = src

    out: list[MolecularPropertyObservationRecord] = []
    for cid in page_ids:
        obs = by_id.get(cid)
        if obs is None:  # pragma: no cover — race with delete
            continue
        out.append(
            _build_record(
                obs,
                badge=badges[cid],
                literature_by_id=literature_by_id,
                esr_by_id=esr_by_id,
                source_by_esr=source_by_esr,
            )
        )
    return out


def _build_uncertainty(
    obs: MolecularPropertyObservation,
) -> ObservationUncertainty | None:
    if obs.scalar_uncertainty is None and obs.uncertainty_kind is None:
        return None
    return ObservationUncertainty(
        value=obs.scalar_uncertainty,
        kind=obs.uncertainty_kind,
        coverage_factor=obs.uncertainty_coverage_factor,
        level_of_confidence_pct=obs.uncertainty_level_of_confidence_pct,
        assessor=obs.uncertainty_assessor,
    )


def _build_external_source(
    obs: MolecularPropertyObservation,
    *,
    esr_by_id: dict[int, ExternalSourceRecord],
    source_by_esr: dict[int, ExternalSource],
) -> ObservationExternalSource | None:
    esr = (
        esr_by_id.get(obs.external_source_record_id)
        if obs.external_source_record_id is not None
        else None
    )
    if esr is not None:
        source = source_by_esr.get(esr.id)
        return ObservationExternalSource(
            name=source.source_name if source is not None else None,
            release=source.source_release if source is not None else None,
            doi=source.source_database_doi if source is not None else None,
            record_key=esr.source_record_key,
            content_sha256=esr.content_sha256,
            schema_label=esr.schema_id,
            parser_version=esr.parser_version,
            mapping_version=esr.mapping_version,
        )
    if (
        obs.external_source_name is None
        and obs.external_source_release is None
        and obs.external_source_doi is None
        and obs.external_source_record_key is None
        and obs.external_source_content_sha256 is None
        and obs.external_source_parser_version is None
    ):
        return None
    return ObservationExternalSource(
        name=obs.external_source_name,
        release=obs.external_source_release,
        doi=obs.external_source_doi,
        record_key=obs.external_source_record_key,
        content_sha256=obs.external_source_content_sha256,
        schema_label=None,
        parser_version=obs.external_source_parser_version,
        mapping_version=None,
    )


def _build_record(
    obs: MolecularPropertyObservation,
    *,
    badge: RecordReviewBadge,
    literature_by_id: dict[int, Literature],
    esr_by_id: dict[int, ExternalSourceRecord],
    source_by_esr: dict[int, ExternalSource],
) -> MolecularPropertyObservationRecord:
    lit = (
        literature_by_id.get(obs.literature_id)
        if obs.literature_id is not None
        else None
    )
    return MolecularPropertyObservationRecord(
        observation_ref=obs.public_ref,
        property_kind=obs.property_kind,
        property_label=obs.property_label,
        scalar_value=obs.scalar_value,
        scalar_unit=obs.scalar_unit,
        vector=obs.vector_json,
        tensor=obs.tensor_json,
        uncertainty=_build_uncertainty(obs),
        temperature_k=obs.temperature_k,
        pressure_bar=obs.pressure_bar,
        wavelength_nm=obs.wavelength_nm,
        state_basis=obs.state_basis,
        state_label_raw=obs.state_label_raw,
        method_note=obs.method_note,
        scientific_origin=obs.scientific_origin,
        literature_ref=lit.public_ref if lit is not None else None,
        reference_label=obs.reference_label,
        external_source=_build_external_source(
            obs, esr_by_id=esr_by_id, source_by_esr=source_by_esr
        ),
        review=badge,
    )


def _empty_response(
    filter_echo: dict[str, object],
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesObservationsResponse:
    return ScientificSpeciesObservationsResponse(
        request=RequestEcho(
            filter=filter_echo,
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(offset=offset, limit=limit, returned=0, total=0),
    )


__all__ = ["get_species_observations"]
