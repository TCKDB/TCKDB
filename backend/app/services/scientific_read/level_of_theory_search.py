"""Service implementation for /api/v1/scientific/level-of-theories/search.

Records reuse :class:`ScientificLevelOfTheoryRecord` from the detail
endpoint via :func:`build_level_of_theory_record`.

**Usage-derived, not a registry dump.** A level of theory nothing
attaches to does not appear in these results. The candidate-id query
below filters with ``EXISTS (SELECT 1 FROM calculation WHERE
calculation.lot_id = level_of_theory.id)`` -- an existence check against
``calculation``, never an ``OUTER JOIN`` from ``level_of_theory`` that
would let an unused row leak back in. This mirrors the 2026-08 fix
already applied to ``list_software`` / ``list_workflow_tools``
(``app/services/scientific_read/meta.py``) -- see that module's
docstring for the "registered but unused" failure this guards against.
``tests/api/scientific/test_api_level_of_theory.py`` pins this with a
seeded-but-uncalculated LOT and a join-direction mutation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.energy_correction import EnergyCorrectionScheme, FrequencyScaleFactor
from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.schemas.reads.scientific_level_of_theory import (
    ScientificLevelOfTheoryRecord,
)
from app.schemas.reads.scientific_level_of_theory_search import (
    LevelOfTheorySearchRequest,
    RequestEcho,
    ScientificLevelOfTheorySearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    validate_includes,
    validate_pagination,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.level_of_theory import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_level_of_theory_record,
)

_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "level_of_theory_ref",
    "lot_hash",
    "method",
    "basis",
    "dispersion",
    "solvent",
    "spin_treatment",
    "has_correction_schemes",
    "has_frequency_scale_factors",
)

# Declared but no backing path today -- reserved for the LOT-scoped
# software aggregation (methods-surface plan §5.3), same "fail closed
# rather than silently no-op" posture as ECS's deferred software filters.
_DEFERRED_FILTER_FIELDS: tuple[str, ...] = ()

_DEFAULT_SORT_ECHO = "method,basis,id"


def search_levels_of_theory(
    session: Session, request: LevelOfTheorySearchRequest
) -> ScientificLevelOfTheorySearchResponse:
    """Multi-axis level-of-theory search."""
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/level-of-theories/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    lot_id, short_circuit = _resolve_ref(
        session, LevelOfTheory, request.level_of_theory_ref, "level_of_theory"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    stmt = select(LevelOfTheory.id).where(
        exists().where(Calculation.lot_id == LevelOfTheory.id)
    )
    if lot_id is not None:
        stmt = stmt.where(LevelOfTheory.id == lot_id)
    if request.lot_hash is not None:
        stmt = stmt.where(LevelOfTheory.lot_hash == request.lot_hash)
    if request.method is not None:
        stmt = stmt.where(LevelOfTheory.method == request.method)
    if request.basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.dispersion is not None:
        stmt = stmt.where(LevelOfTheory.dispersion == request.dispersion)
    if request.solvent is not None:
        stmt = stmt.where(LevelOfTheory.solvent == request.solvent)
    if request.spin_treatment is not None:
        stmt = stmt.where(LevelOfTheory.spin_treatment == request.spin_treatment)
    if request.has_correction_schemes is not None:
        ex = exists().where(
            EnergyCorrectionScheme.level_of_theory_id == LevelOfTheory.id
        )
        stmt = stmt.where(ex if request.has_correction_schemes else ~ex)
    if request.has_frequency_scale_factors is not None:
        ex = exists().where(
            FrequencyScaleFactor.level_of_theory_id == LevelOfTheory.id
        )
        stmt = stmt.where(ex if request.has_frequency_scale_factors else ~ex)

    stmt = stmt.order_by(
        LevelOfTheory.method.asc(),
        LevelOfTheory.basis.asc(),
        LevelOfTheory.id.asc(),
    )
    candidate_ids = [row.id for row in session.execute(stmt).all()]
    total = len(candidate_ids)
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    page_ids = candidate_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, includes)

    return ScientificLevelOfTheorySearchResponse(
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


def _enforce_at_least_one_filter(request: LevelOfTheorySearchRequest) -> None:
    for name in _MEANINGFUL_FILTER_FIELDS:
        if getattr(request, name) is not None:
            return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/level-of-theories/search."
    )


def _resolve_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(session, model_cls, ref, kind_label=kind_label)
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
) -> list[ScientificLevelOfTheoryRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(LevelOfTheory).where(LevelOfTheory.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificLevelOfTheoryRecord] = []
    for lid in page_ids:
        lot = by_id.get(lid)
        if lot is None:  # pragma: no cover — race with delete
            continue
        out.append(build_level_of_theory_record(session, lot=lot, includes=includes))
    return out


def _empty_response(
    request: LevelOfTheorySearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificLevelOfTheorySearchResponse:
    return ScientificLevelOfTheorySearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=[],
        pagination=build_pagination(offset=offset, limit=limit, returned=0, total=0),
    )


def _request_filter_echo(request: LevelOfTheorySearchRequest) -> dict[str, Any]:
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


__all__ = ["search_levels_of_theory"]
