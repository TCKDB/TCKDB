"""Service for GET /api/v1/scientific/species-entries/{id}/transport.

A species-entry-scoped read of transport records. Thin wrapper around
the transport detail/search machinery: it pins the query to one
``species_entry`` and reuses :func:`build_transport_record` (so each
record matches the detail / search projection for the same include
set) plus the shared review / pagination / sort helpers.

Mirrors ``get_species_thermo``. ``trust`` is an *internal* include
token here (so ``include=all`` never expands to it) — matching the
transport detail endpoint and the per-entry thermo contract.
``search_transport`` deliberately does not accept ``trust`` at all, so
delegating to it would make per-entry trust impossible.

See ``backend/docs/specs/scientific_transport_reads.md`` and
``backend/docs/specs/trust_read_api_current.md``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.species import SpeciesEntry
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CollapseMode,
    RecordReviewBadge,
    SelectionPolicy,
    simple_selection_sort_key,
)
from app.schemas.reads.scientific_transport import (
    ScientificTransportRecord,
)
from app.schemas.reads.scientific_transport_search import (
    RequestEcho,
    ScientificTransportSearchResponse,
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
from app.services.scientific_read.transport import (
    _DETAIL_LEGAL_INCLUDE_TOKENS,
    _INTERNAL_INCLUDE_TOKENS,
    build_transport_record,
)

# Deterministic ordering matches the transport search service:
# review rank ASC, created_at DESC, id DESC.
_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def get_species_transport(
    session: Session,
    *,
    species_entry_id: int,
    include: list[str] | None = None,
    min_review_status: RecordReviewStatus | None = None,
    include_rejected: bool = False,
    include_deprecated: bool = False,
    sort: str | None = None,
    collapse: CollapseMode = CollapseMode.all,
    selection_policy: SelectionPolicy = SelectionPolicy.default,
    offset: int = 0,
    limit: int = 50,
) -> ScientificTransportSearchResponse:
    """Return transport records for one species entry.

    Records are pinned to ``species_entry_id`` and returned in the
    shared deterministic order. ``include=trust`` adds the
    ``computed_transport_v1`` fragment per record; ``include=all``
    never expands to trust. The response reuses
    :class:`ScientificTransportSearchResponse`; the pinned entry is
    echoed in ``request.filter.species_entry_ref``.

    :raises NotFoundError: 404 when ``species_entry_id`` is unknown.
    :raises ValueError: 422 for sort / include / pagination violations.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/species-entries/{id}/transport",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise NotFoundError(
            f"species_entry not found (species_entry_id={species_entry_id})"
        )
    species_entry_ref = entry.public_ref

    rows = session.execute(
        select(Transport.id, Transport.created_at).where(
            Transport.species_entry_id == species_entry_id
        )
    ).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(
            species_entry_ref, includes, offset, limit, collapse, selection_policy
        )

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transport,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_response(
            species_entry_ref, includes, offset, limit, collapse, selection_policy
        )

    summary = review_summary(badges[cid] for cid in visible_ids)
    total = len(visible_ids)
    collapse_first = collapse is CollapseMode.first
    if collapse_first:
        # Selection policy governs the single selected record only. The
        # default candidate-list order is unchanged for collapse=all.
        review_status_by_id = {cid: badges[cid].status for cid in visible_ids}
        ranked = sorted(
            visible_ids,
            key=lambda cid: simple_selection_sort_key(
                cid,
                policy=selection_policy,
                review_status_by_id=review_status_by_id,
                created_at_by_id=created_at_by_id,
            ),
        )
        ordered_ids = ranked
    else:
        visible_ids.sort(
            key=lambda cid: (
                REVIEW_RANK[badges[cid].status],
                -created_at_by_id[cid].timestamp(),
                -cid,
            )
        )
        ordered_ids = visible_ids
    page_ids = slice_for_pagination(
        ordered_ids,
        offset=offset,
        limit=limit,
        collapse_first=collapse_first,
    )
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificTransportSearchResponse(
        request=RequestEcho(
            filter={"species_entry_ref": species_entry_ref},
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
            collapse=collapse,
            selection_policy=selection_policy,
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(records),
            total=total,
            collapse_first=collapse_first,
        ),
    )


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificTransportRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(Transport).where(Transport.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificTransportRecord] = []
    for cid in page_ids:
        tr = by_id.get(cid)
        if tr is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_transport_record(
                session,
                tr=tr,
                badge=badges[cid],
                includes=includes,
            )
        )
    return out


def _empty_response(
    species_entry_ref: str,
    includes: set[str],
    offset: int,
    limit: int,
    collapse: CollapseMode = CollapseMode.all,
    selection_policy: SelectionPolicy = SelectionPolicy.default,
) -> ScientificTransportSearchResponse:
    return ScientificTransportSearchResponse(
        request=RequestEcho(
            filter={"species_entry_ref": species_entry_ref},
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
            collapse=collapse,
            selection_policy=selection_policy,
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


__all__ = ["get_species_transport"]
