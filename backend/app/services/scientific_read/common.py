"""Shared helpers for the scientific_read service layer.

Centralizes:
- request validation that's identical across endpoints (sort rejection,
  pagination bounds, include-token validation, temperature-range coherence)
- review-rank lookup
- review summary construction from a ``RecordReview`` set
- temperature coverage computation per spec D8
- review-status filtering

Each service module imports from here so the rules are defined once.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    REVIEW_RANK,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    TemperatureCoverage,
    default_visible_statuses,
    status_at_or_above,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Request-side validation helpers
# ---------------------------------------------------------------------------


def reject_client_sort(sort: str | None) -> None:
    """Reject any client-supplied sort (v0 rule, per spec §Sort vocabulary).

    Raises ValueError → 422 ``client_sort_not_supported``.
    """
    if sort is not None:
        raise ValueError(
            "client_sort_not_supported: sort= is not accepted in v0; the "
            "per-endpoint default sort applies."
        )


def validate_pagination(offset: int, limit: int) -> tuple[int, int]:
    """Validate offset/limit per L5 and return them coerced to defaults.

    Raises ValueError → 422 on out-of-range values.
    """
    if offset < 0:
        raise ValueError("invalid_pagination: offset must be >= 0")
    if limit < 1:
        raise ValueError("invalid_pagination: limit must be >= 1")
    if limit > MAX_LIMIT:
        raise ValueError(
            f"invalid_pagination: limit must be <= {MAX_LIMIT} (got {limit})"
        )
    return offset, limit


def validate_includes(
    requested: Iterable[str], legal: set[str], endpoint_name: str
) -> set[str]:
    """Resolve ``include=`` tokens for an endpoint.

    - ``all`` expands to ``legal - {"all"}``
    - Tokens not in ``legal`` raise ValueError → 422
    - Empty / duplicate input is normalized to a set
    """
    requested_set = {t for t in requested if t}
    if not requested_set:
        return set()
    if "all" in requested_set:
        return legal - {"all"}

    legal_no_all = legal - {"all"}
    bad = requested_set - legal
    if bad:
        sorted_legal = sorted(legal_no_all) + ["all"]
        raise ValueError(
            "unknown_include_token: "
            f"token(s) {sorted(bad)!r} not legal for {endpoint_name}. "
            f"Legal tokens: {sorted_legal!r}"
        )
    return requested_set & legal_no_all


def validate_temperature_range(
    temperature_min: float | None, temperature_max: float | None
) -> None:
    """Reject temperature_min > temperature_max with a 422."""
    if (
        temperature_min is not None
        and temperature_max is not None
        and temperature_min > temperature_max
    ):
        raise ValueError(
            "invalid_temperature_range: temperature_min must be <= temperature_max"
        )


# ---------------------------------------------------------------------------
# Review state helpers
# ---------------------------------------------------------------------------


def visible_statuses(
    *,
    min_review_status: RecordReviewStatus | None,
    include_rejected: bool,
    include_deprecated: bool,
) -> set[RecordReviewStatus]:
    """Compute the set of review statuses visible per the request.

    Combines the default-trust posture (D5) with an optional ``min_review_status``
    threshold (D7, shallow). Rejected and deprecated statuses are excluded
    unless their respective opt-in flag is set; ``min_review_status`` further
    restricts the set to statuses at or above the threshold.
    """
    base = default_visible_statuses(
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    if min_review_status is None:
        return base
    return base & status_at_or_above(min_review_status)


def fetch_review_badges(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Iterable[int],
) -> dict[int, RecordReviewBadge]:
    """Bulk-load review badges for ``(record_type, record_id)`` pairs.

    Returns a mapping from record_id → RecordReviewBadge. IDs without a
    RecordReview row map to a ``not_reviewed`` badge so every record always
    gets a deterministic status.
    """
    record_ids = list(record_ids)
    if not record_ids:
        return {}

    rows = session.scalars(
        select(RecordReview).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id.in_(record_ids),
        )
    ).all()

    badges: dict[int, RecordReviewBadge] = {
        row.record_id: RecordReviewBadge(
            status=row.status,
            reviewed_at=row.reviewed_at,
            reviewer_kind="human" if row.reviewed_by is not None else None,
        )
        for row in rows
    }
    # Fill in defaults for records with no review row.
    for rid in record_ids:
        badges.setdefault(
            rid, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
        )
    return badges


def review_summary(badges: Iterable[RecordReviewBadge]) -> ReviewStatusSummary:
    """Build a ReviewStatusSummary from an iterable of RecordReviewBadge."""
    summary = ReviewStatusSummary()
    for badge in badges:
        if badge.status is RecordReviewStatus.approved:
            summary.approved += 1
        elif badge.status is RecordReviewStatus.under_review:
            summary.under_review += 1
        elif badge.status is RecordReviewStatus.not_reviewed:
            summary.not_reviewed += 1
        elif badge.status is RecordReviewStatus.deprecated:
            summary.deprecated += 1
        elif badge.status is RecordReviewStatus.rejected:
            summary.rejected += 1
        summary.total += 1
    return summary


def review_rank(badge: RecordReviewBadge) -> int:
    """Return the L2 review_rank integer for a badge (lower = better)."""
    return REVIEW_RANK[badge.status]


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------


def build_pagination(
    *, offset: int, limit: int, returned: int, total: int
) -> Pagination:
    """Construct the response Pagination block.

    ``total`` is the pre-collapse, post-filter count (per spec).
    """
    return Pagination(
        offset=offset, limit=limit, returned=returned, total=total
    )


# ---------------------------------------------------------------------------
# Temperature coverage (D8)
# ---------------------------------------------------------------------------


def temperature_coverage(
    *,
    requested_min: float | None,
    requested_max: float | None,
    record_min: float | None,
    record_max: float | None,
) -> TemperatureCoverage:
    """Compute the TemperatureCoverage fragment per D8.

    - If ``requested_min`` is supplied, full coverage requires record_min <= requested_min.
    - If ``requested_max`` is supplied, full coverage requires record_max >= requested_max.
    - extrapolation_distance_k sums the missing K on each side.
    - overlap_fraction is diagnostic only and computed only when both
      requested bounds and both record bounds are present.
    """
    covers = True
    extrapolation = 0.0

    if requested_min is not None:
        if record_min is None or record_min > requested_min:
            covers = False
        if record_min is not None and record_min > requested_min:
            extrapolation += record_min - requested_min

    if requested_max is not None:
        if record_max is None or record_max < requested_max:
            covers = False
        if record_max is not None and record_max < requested_max:
            extrapolation += requested_max - record_max

    overlap_fraction: float | None = None
    if (
        requested_min is not None
        and requested_max is not None
        and record_min is not None
        and record_max is not None
        and requested_max > requested_min
    ):
        overlap_lo = max(record_min, requested_min)
        overlap_hi = min(record_max, requested_max)
        overlap = max(0.0, overlap_hi - overlap_lo)
        overlap_fraction = overlap / (requested_max - requested_min)

    return TemperatureCoverage(
        requested_min_k=requested_min,
        requested_max_k=requested_max,
        record_min_k=record_min,
        record_max_k=record_max,
        covers_requested_range=covers,
        overlap_fraction=overlap_fraction,
        extrapolation_distance_k=extrapolation,
    )


# ---------------------------------------------------------------------------
# Misc small helpers
# ---------------------------------------------------------------------------


def slice_for_pagination(
    items: list,
    *,
    offset: int,
    limit: int,
    collapse_first: bool,
) -> list:
    """Apply pagination after sort/collapse.

    With ``collapse_first=True``, returns at most one item (the first after
    sort), regardless of ``limit``. Otherwise, applies offset+limit.
    """
    if collapse_first:
        return items[:1]
    return items[offset : offset + limit]
