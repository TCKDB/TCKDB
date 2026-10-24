"""Service for GET /api/v1/scientific/species-entries/{id}/statmech.

A species-entry-scoped read of statmech records. This is a thin
wrapper around the statmech detail/search machinery: it pins the
query to one ``species_entry`` and reuses :func:`build_statmech_record`
(so each record is byte-identical to the detail / search projection
for the same include set) plus the shared review / pagination / sort
helpers.

Mirrors ``get_species_thermo`` rather than delegating to
``search_statmech``. Both surfaces now expose ``trust`` — the search
vocabulary gained the token once its per-page eager-load was in place —
and on both it is internal-tokenized, so ``include=all`` never expands to
it. The reason not to delegate is the ordering and pagination contract,
not the include vocabulary: this read is pinned to one species entry and
sorts as the thermo subresource does.

See ``backend/docs/specs/scientific_statmech_reads.md`` and
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
from app.db.models.statmech import Statmech
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
    browse_order,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)
from app.schemas.reads.scientific_statmech_search import (
    RequestEcho,
    ScientificStatmechSearchResponse,
)
from app.services.scientific_read.hidden_by_default import (
    hidden_by_default_from_statuses,
    record_hidden_by_default,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    review_visibility_echo,
    slice_for_pagination,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.statmech import (
    _DETAIL_LEGAL_INCLUDE_TOKENS,
    _INTERNAL_INCLUDE_TOKENS,
    STATMECH_TRUST_EAGER_LOADS,
    build_statmech_record,
)
from app.services.scientific_read.supersession import fetch_supersession_notices

# Display order, the same as the statmech search service: review rank ASC, created_at DESC, id DESC.
_DEFAULT_SORT_ECHO = browse_order("review_rank", "created_at", "id")


def get_species_statmech(
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
) -> ScientificStatmechSearchResponse:
    """Return statmech records for one species entry.

    Records are pinned to ``species_entry_id`` and returned in the
    shared deterministic order; every matching record is returned, none is picked. ``include=trust`` adds the
    record's computed statmech rubric fragment per record; ``include=all`` never
    expands to trust (it is an internal token here). The response
    reuses :class:`ScientificStatmechSearchResponse`; the pinned entry
    is echoed in ``request.filter.species_entry_ref``.

    :raises NotFoundError: 404 when ``species_entry_id`` is unknown.
    :raises ValueError: 422 for sort / include / pagination violations.
    """
    reject_client_sort(sort)
    offset, limit = validate_pagination(offset, limit)
    includes = validate_includes(
        include or [],
        _DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/species-entries/{id}/statmech",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)

    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise not_found("species_entry", row_id=species_entry_id)
    species_entry_ref = entry.public_ref

    rows = session.execute(
        select(Statmech.id, Statmech.created_at).where(
            Statmech.species_entry_id == species_entry_id
        )
    ).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    if not candidate_ids:
        return _empty_response(species_entry_ref, includes, offset, limit, visible)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=candidate_ids,
    )
    record_hidden_by_default(
        hidden_by_default_from_statuses(
            (badges[cid].status for cid in candidate_ids),
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            min_review_status=min_review_status,
        ),
        scope=("statmech", species_entry_id),
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_response(species_entry_ref, includes, offset, limit, visible)

    summary = review_summary(badges[cid] for cid in visible_ids)
    total = len(visible_ids)
    ordered_ids = sorted(
        visible_ids,
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        ),
    )
    page_ids = slice_for_pagination(ordered_ids, offset=offset, limit=limit)
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificStatmechSearchResponse(
        request=_request_echo(species_entry_ref, includes, visible),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(records),
            total=total,
        ),
    )


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificStatmechRecord]:
    if not page_ids:
        return []
    stmt = select(Statmech).where(Statmech.id.in_(page_ids))
    if "trust" in includes:
        # Same fixed-cost batched load as ``statmech_search``: the trust
        # evaluator's relationship graph is loaded once per page, not lazily
        # once per row.
        stmt = stmt.options(*STATMECH_TRUST_EAGER_LOADS)
    rows = session.scalars(stmt).all()
    by_id = {r.id: r for r in rows}
    # Correction notices for the page, batched (two queries whatever the
    # page size), exactly as detail and search do.
    supersessions = fetch_supersession_notices(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=page_ids,
    )
    out: list[ScientificStatmechRecord] = []
    for cid in page_ids:
        sm = by_id.get(cid)
        if sm is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_statmech_record(
                session,
                sm=sm,
                badge=badges[cid],
                includes=includes,
                supersession=supersessions.get(cid),
            )
        )
    return out


def _request_echo(
    species_entry_ref: str,
    includes: set[str],
    visible: set[RecordReviewStatus],
) -> RequestEcho:
    return RequestEcho(
        filter={"species_entry_ref": species_entry_ref},
        sort=_DEFAULT_SORT_ECHO,
        include=sorted(includes),
        review_visibility=review_visibility_echo(visible),
    )


def _empty_response(
    species_entry_ref: str,
    includes: set[str],
    offset: int,
    limit: int,
    visible: set[RecordReviewStatus],
) -> ScientificStatmechSearchResponse:
    return ScientificStatmechSearchResponse(
        request=_request_echo(species_entry_ref, includes, visible),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


__all__ = ["get_species_statmech"]
