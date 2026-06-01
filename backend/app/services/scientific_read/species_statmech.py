"""Service for GET /api/v1/scientific/species-entries/{id}/statmech.

A species-entry-scoped read of statmech records. This is a thin
wrapper around the statmech detail/search machinery: it pins the
query to one ``species_entry`` and reuses :func:`build_statmech_record`
(so each record is byte-identical to the detail / search projection
for the same include set) plus the shared review / pagination / sort
helpers.

Mirrors ``get_species_thermo`` rather than delegating to
``search_statmech``: the per-entry subresource *does* expose ``trust``
(via the trust-bearing ``_DETAIL_LEGAL_INCLUDE_TOKENS`` set, with
``trust`` internal-tokenized so ``include=all`` never expands to it),
whereas broad ``search_statmech`` uses the narrower
``_LEGAL_INCLUDE_TOKENS`` set and rejects ``include=trust`` outright —
trust is detail/subresource only, never on broad search.

See ``backend/docs/specs/scientific_statmech_reads.md`` and
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
from app.db.models.statmech import Statmech
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CollapseMode,
    RecordReviewBadge,
    SelectionPolicy,
    simple_selection_sort_key,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)
from app.schemas.reads.scientific_statmech_search import (
    RequestEcho,
    ScientificStatmechSearchResponse,
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
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.statmech import (
    _DETAIL_LEGAL_INCLUDE_TOKENS,
    _INTERNAL_INCLUDE_TOKENS,
    build_statmech_record,
)

# Deterministic ordering matches the statmech search service:
# review rank ASC, created_at DESC, id DESC.
_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def get_species_statmech(
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
) -> ScientificStatmechSearchResponse:
    """Return statmech records for one species entry.

    Records are pinned to ``species_entry_id`` and returned in the
    shared deterministic order. ``include=trust`` adds the
    ``computed_statmech_v1`` fragment per record; ``include=all`` never
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
        raise NotFoundError(
            f"species_entry not found (species_entry_id={species_entry_id})"
        )
    species_entry_ref = entry.public_ref

    rows = session.execute(
        select(Statmech.id, Statmech.created_at).where(
            Statmech.species_entry_id == species_entry_id
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
        record_type=SubmissionRecordType.statmech,
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
    if collapse is CollapseMode.first:
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
        page_ids = ranked[:1]
    else:
        visible_ids.sort(
            key=lambda cid: (
                REVIEW_RANK[badges[cid].status],
                -created_at_by_id[cid].timestamp(),
                -cid,
            )
        )
        page_ids = visible_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificStatmechSearchResponse(
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
            offset=offset, limit=limit, returned=len(records), total=total
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
    rows = session.scalars(
        select(Statmech).where(Statmech.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
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
) -> ScientificStatmechSearchResponse:
    return ScientificStatmechSearchResponse(
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


__all__ = ["get_species_statmech"]
