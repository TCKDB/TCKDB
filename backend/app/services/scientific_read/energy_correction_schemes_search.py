"""Service implementation for /api/v1/scientific/energy-correction-schemes/search.

Records reuse :class:`ScientificEnergyCorrectionSchemeRecord` from the
detail endpoint via :func:`build_energy_correction_scheme_record`.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.api.error_contract import reject_unsupported_filters
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.schemas.reads.scientific_energy_correction_scheme import (
    ScientificEnergyCorrectionSchemeRecord,
)
from app.schemas.reads.scientific_energy_correction_scheme_search import (
    EnergyCorrectionSchemeSearchRequest,
    RequestEcho,
    ScientificEnergyCorrectionSchemeSearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    validate_includes,
    validate_pagination,
)
from app.services.scientific_read.energy_correction_schemes import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_energy_correction_scheme_record,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "energy_correction_scheme_ref",
    "name",
    "version",
    "scheme_kind",
    "method",
    "basis",
    "literature_ref",
    "has_corrections",
    "used_by_calculation",
)

# Legacy grouping name for declared filters without a backing path.
# The service rejects these before querying; none is treated as a no-op.
_DEFERRED_FILTER_FIELDS: tuple[str, ...] = (
    "software",
    "software_version",
    "used_by_thermo",
)


_DEFAULT_SORT_ECHO = "scheme_kind,name,version,id"


def search_energy_correction_schemes(
    session: Session, request: EnergyCorrectionSchemeSearchRequest
) -> ScientificEnergyCorrectionSchemeSearchResponse:
    """Multi-axis ECS search."""
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/energy-correction-schemes/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    reject_unsupported_filters(
        {
            "software": request.software,
            "software_version": request.software_version,
            "used_by_thermo": request.used_by_thermo,
        },
        endpoint="/scientific/energy-correction-schemes/search",
    )

    _enforce_at_least_one_filter(request)

    ecs_id, short_circuit = _resolve_ref(
        session,
        EnergyCorrectionScheme,
        request.energy_correction_scheme_ref,
        "energy_correction_scheme",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    literature_id, short_circuit = _resolve_ref(
        session, Literature, request.literature_ref, "literature"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    stmt = select(EnergyCorrectionScheme.id)
    if ecs_id is not None:
        stmt = stmt.where(EnergyCorrectionScheme.id == ecs_id)
    if literature_id is not None:
        stmt = stmt.where(
            EnergyCorrectionScheme.source_literature_id == literature_id
        )
    if request.scheme_kind is not None:
        stmt = stmt.where(EnergyCorrectionScheme.kind == request.scheme_kind)
    if request.name is not None:
        stmt = stmt.where(EnergyCorrectionScheme.name == request.name)
    if request.version is not None:
        stmt = stmt.where(EnergyCorrectionScheme.version == request.version)
    if request.method is not None or request.basis is not None:
        stmt = stmt.join(
            LevelOfTheory,
            LevelOfTheory.id == EnergyCorrectionScheme.level_of_theory_id,
        )
        if request.method is not None:
            stmt = stmt.where(LevelOfTheory.method == request.method)
        if request.basis is not None:
            stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.has_corrections is not None:
        ex = or_(
            exists().where(
                EnergyCorrectionSchemeAtomParam.scheme_id
                == EnergyCorrectionScheme.id
            ),
            exists().where(
                EnergyCorrectionSchemeBondParam.scheme_id
                == EnergyCorrectionScheme.id
            ),
            exists().where(
                EnergyCorrectionSchemeComponentParam.scheme_id
                == EnergyCorrectionScheme.id
            ),
        )
        stmt = stmt.where(ex if request.has_corrections else ~ex)
    if request.used_by_calculation is not None:
        ex = exists().where(
            AppliedEnergyCorrection.scheme_id == EnergyCorrectionScheme.id,
            AppliedEnergyCorrection.source_calculation_id.is_not(None),
        )
        stmt = stmt.where(ex if request.used_by_calculation else ~ex)

    stmt = stmt.order_by(
        EnergyCorrectionScheme.kind.asc(),
        EnergyCorrectionScheme.name.asc(),
        EnergyCorrectionScheme.id.asc(),
    )
    candidate_ids = [row.id for row in session.execute(stmt).all()]
    total = len(candidate_ids)
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    page_ids = candidate_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, includes)

    return ScientificEnergyCorrectionSchemeSearchResponse(
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
    request: EnergyCorrectionSchemeSearchRequest,
) -> None:
    for name in _MEANINGFUL_FILTER_FIELDS:
        if getattr(request, name) is not None:
            return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/energy-correction-schemes/search."
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
) -> list[ScientificEnergyCorrectionSchemeRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(EnergyCorrectionScheme).where(
            EnergyCorrectionScheme.id.in_(page_ids)
        )
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificEnergyCorrectionSchemeRecord] = []
    for cid in page_ids:
        ecs = by_id.get(cid)
        if ecs is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_energy_correction_scheme_record(
                session, ecs=ecs, includes=includes
            )
        )
    return out


def _empty_response(
    request: EnergyCorrectionSchemeSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificEnergyCorrectionSchemeSearchResponse:
    return ScientificEnergyCorrectionSchemeSearchResponse(
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
    request: EnergyCorrectionSchemeSearchRequest,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _MEANINGFUL_FILTER_FIELDS + _DEFERRED_FILTER_FIELDS + (
        "include_rejected",
        "include_deprecated",
        "min_review_status",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = ["search_energy_correction_schemes"]
