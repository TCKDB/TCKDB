"""Service implementation for /api/v1/scientific/frequency-scale-factors/search.

Records reuse :class:`ScientificFrequencyScaleFactorRecord` from the
detail endpoint via :func:`build_frequency_scale_factor_record`.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.software import Software
from app.db.models.statmech import Statmech
from app.schemas.reads.scientific_common import (
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_frequency_scale_factor import (
    ScientificFrequencyScaleFactorRecord,
)
from app.schemas.reads.scientific_frequency_scale_factor_search import (
    FrequencyScaleFactorSearchRequest,
    RequestEcho,
    ScientificFrequencyScaleFactorSearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    validate_includes,
    validate_pagination,
)
from app.services.scientific_read.frequency_scale_factors import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_frequency_scale_factor_record,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "frequency_scale_factor_ref",
    "value",
    "value_min",
    "value_max",
    "scale_kind",
    "method",
    "basis",
    "software",
    "software_version",
    "literature_ref",
    "used_by_statmech",
)

# Filters declared in the schema but not yet wired to a backing column.
# Documented as deferred in the spec. Listed here so the request echo
# faithfully reports what the caller sent even when the filter is a no-op.
_DEFERRED_FILTER_FIELDS: tuple[str, ...] = ("model_kind",)


_DEFAULT_SORT_ECHO = "scale_kind,value,id"


def search_frequency_scale_factors(
    session: Session, request: FrequencyScaleFactorSearchRequest
) -> ScientificFrequencyScaleFactorSearchResponse:
    """Multi-axis FSF search."""
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/frequency-scale-factors/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    fsf_id, short_circuit = _resolve_ref(
        session,
        FrequencyScaleFactor,
        request.frequency_scale_factor_ref,
        "frequency_scale_factor",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    literature_id, short_circuit = _resolve_ref(
        session, Literature, request.literature_ref, "literature"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    stmt = select(FrequencyScaleFactor.id)
    if fsf_id is not None:
        stmt = stmt.where(FrequencyScaleFactor.id == fsf_id)
    if literature_id is not None:
        stmt = stmt.where(
            FrequencyScaleFactor.source_literature_id == literature_id
        )
    if request.scale_kind is not None:
        stmt = stmt.where(FrequencyScaleFactor.scale_kind == request.scale_kind)
    if request.value is not None:
        stmt = stmt.where(FrequencyScaleFactor.value == request.value)
    if request.value_min is not None:
        stmt = stmt.where(FrequencyScaleFactor.value >= request.value_min)
    if request.value_max is not None:
        stmt = stmt.where(FrequencyScaleFactor.value <= request.value_max)
    if request.method is not None or request.basis is not None:
        stmt = stmt.join(
            LevelOfTheory,
            LevelOfTheory.id == FrequencyScaleFactor.level_of_theory_id,
        )
        if request.method is not None:
            stmt = stmt.where(LevelOfTheory.method == request.method)
        if request.basis is not None:
            stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.software is not None:
        stmt = stmt.join(
            Software, Software.id == FrequencyScaleFactor.software_id
        ).where(Software.name == request.software)
    # ``software_version`` is documented as deferred — the FSF row only
    # carries ``software_id``, not a software release.
    if request.used_by_statmech is not None:
        ex = exists().where(
            Statmech.frequency_scale_factor_id == FrequencyScaleFactor.id
        )
        stmt = stmt.where(ex if request.used_by_statmech else ~ex)

    stmt = stmt.order_by(
        FrequencyScaleFactor.scale_kind.asc(),
        FrequencyScaleFactor.value.asc(),
        FrequencyScaleFactor.id.asc(),
    )
    candidate_ids = [row.id for row in session.execute(stmt).all()]
    total = len(candidate_ids)
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    page_ids = candidate_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, includes)

    return ScientificFrequencyScaleFactorSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _enforce_at_least_one_filter(
    request: FrequencyScaleFactorSearchRequest,
) -> None:
    for name in _MEANINGFUL_FILTER_FIELDS:
        if getattr(request, name) is not None:
            return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/frequency-scale-factors/search."
    )


def _resolve_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(
        session, model_cls, ref, kind_label=kind_label
    )
    if resolved is None:
        return None, True
    return resolved, False


# ---------------------------------------------------------------------------
# Materialization + helpers
# ---------------------------------------------------------------------------


def _materialize_records(
    session: Session,
    page_ids: list[int],
    includes: set[str],
) -> list[ScientificFrequencyScaleFactorRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(FrequencyScaleFactor).where(
            FrequencyScaleFactor.id.in_(page_ids)
        )
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificFrequencyScaleFactorRecord] = []
    for fid in page_ids:
        fsf = by_id.get(fid)
        if fsf is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_frequency_scale_factor_record(
                session, fsf=fsf, includes=includes
            )
        )
    return out


def _empty_response(
    request: FrequencyScaleFactorSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificFrequencyScaleFactorSearchResponse:
    return ScientificFrequencyScaleFactorSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _request_filter_echo(
    request: FrequencyScaleFactorSearchRequest,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _MEANINGFUL_FILTER_FIELDS + _DEFERRED_FILTER_FIELDS + (
        "include_rejected",
        "include_deprecated",
        "min_review_status",
        "software_version",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = ["search_frequency_scale_factors"]
