"""Service for GET /api/v1/scientific/species-entries/{id}/transport.

A species-entry-scoped read of transport records. Thin wrapper around
the transport detail/search machinery: it pins the query to one
``species_entry`` and reuses :func:`build_transport_record` (so each
record matches the detail / search projection for the same include
set) plus the shared review / pagination / sort helpers.

Mirrors ``get_species_thermo``. ``trust`` is an *internal* include
token here (so ``include=all`` never expands to it) — matching the
transport detail endpoint, the per-entry thermo contract, and
``search_transport``, which accepts the token too now that its
materializer loads the evidence graph once per page.

See ``backend/docs/specs/scientific_transport_reads.md`` and
``backend/docs/specs/trust_read_api_current.md``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.species import SpeciesEntry
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
    browse_order,
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
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.hidden_by_default import (
    hidden_by_default_from_statuses,
    record_hidden_by_default,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.keyset import Paging
from app.services.scientific_read.transport import (
    _DETAIL_LEGAL_INCLUDE_TOKENS,
    _INTERNAL_INCLUDE_TOKENS,
    build_transport_record,
)

# Deterministic ordering matches the transport search service:
# review rank ASC, created_at DESC, id DESC.
_DEFAULT_SORT_ECHO = browse_order("review_rank", "created_at", "id")


def get_species_transport(
    session: Session,
    *,
    species_entry_id: int,
    include: list[str] | None = None,
    min_review_status: RecordReviewStatus | None = None,
    include_rejected: bool = False,
    include_deprecated: bool = False,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
    cursor: str | None = None,
) -> ScientificTransportSearchResponse:
    """Return transport records for one species entry.

    Records are pinned to ``species_entry_id`` and returned in the
    shared deterministic order. ``include=trust`` adds the
    ``computed_transport`` fragment per record; ``include=all``
    never expands to trust. The response reuses
    :class:`ScientificTransportSearchResponse`; the pinned entry is
    echoed in ``request.filter.species_entry_ref``.

    :raises NotFoundError: 404 when ``species_entry_id`` is unknown.
    :raises ValueError: 422 for sort / include / pagination violations.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    paging = Paging(
        "/scientific/species-entries/{id}/transport",
        {
            "cursor": cursor,
            "species_entry_id": species_entry_id,
            "min_review_status": min_review_status.value if min_review_status is not None else None,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
        },
        offset=offset,
        limit=limit,
    )
    includes = validate_includes(
        include or [],
        _DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/species-entries/{id}/transport",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise not_found("species_entry", row_id=species_entry_id)
    species_entry_ref = entry.public_ref

    rows = session.execute(
        select(Transport.id, Transport.created_at).where(
            Transport.species_entry_id == species_entry_id
        )
    ).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(species_entry_ref, includes, offset, limit)

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
    record_hidden_by_default(
        hidden_by_default_from_statuses(
            (badges[cid].status for cid in candidate_ids),
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            min_review_status=min_review_status,
        ),
        scope=("transport", species_entry_id),
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_response(species_entry_ref, includes, offset, limit)

    visible_ids = paging.snapshot(visible_ids, id_of=lambda cid: cid)
    summary = review_summary(badges[cid] for cid in visible_ids)
    page = paging.window(
        visible_ids,
        key_of=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        ),
    )
    page_ids = page.items
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificTransportSearchResponse(
        request=RequestEcho(
            filter={"species_entry_ref": species_entry_ref},
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(records),
            total=page.total,
            page=page,
        ),
        **page.envelope(),
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
) -> ScientificTransportSearchResponse:
    return ScientificTransportSearchResponse(
        request=RequestEcho(
            filter={"species_entry_ref": species_entry_ref},
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


__all__ = ["get_species_transport"]
