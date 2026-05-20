"""Read schemas for the literature-centered inverse records endpoint.

Covers:

- ``GET /api/v1/scientific/literature/{literature_ref_or_id}/records``

The endpoint flattens every record type with a **direct**
``literature_id`` FK into one paginated list. Each item carries a
``record_type`` discriminator, the record's public ref, and a small
type-appropriate summary — clients follow the ``endpoint`` URL to
fetch the full record.

See ``backend/docs/specs/scientific_literature_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.reads.scientific_common import (
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)


# ---------------------------------------------------------------------------
# Supported record types
# ---------------------------------------------------------------------------


# Only record types with a *direct* ``literature_id`` FK are listed
# here. Adding a type requires both schema linkage and a loader in
# ``app/services/scientific_read/literature_records.py``.
LiteratureLinkedRecordType = Literal[
    "calculation",
    "thermo",
    "kinetics",
    "statmech",
    "transport",
    "network",
    "network_solve",
]


SUPPORTED_RECORD_TYPES: tuple[str, ...] = (
    "calculation",
    "thermo",
    "kinetics",
    "statmech",
    "transport",
    "network",
    "network_solve",
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class LiteratureRecordsRequest(BaseModel):
    """Service-layer request for /scientific/literature/{handle}/records."""

    record_type: str | None = None
    include_rejected: bool = False
    include_deprecated: bool = False
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Linked record summary
# ---------------------------------------------------------------------------


class LiteratureLinkedRecordSummary(BaseModel):
    """One scientific record citing the literature.

    ``record_type`` discriminates which type of record this is. The
    type-specific summary fields (``species_ref``, ``reaction_ref``,
    ``calculation_ref``, etc.) populate only where applicable; the
    rest stay ``None``.

    ``endpoint`` is the **ref-based** detail URL for the target record
    — never an ID-based path — so a client can follow it without
    needing to know about integer keys.
    """

    record_type: LiteratureLinkedRecordType
    record_ref: str
    record_id: int | None = None

    # All current loaders use the direct ``literature_id`` FK, so the
    # relationship is always direct in v0; the field is kept so the
    # shape is forward-compatible with indirect linkage.
    relationship_kind: str = "direct"
    role: str | None = None

    # Generic small-text identification.
    title: str | None = None
    label: str | None = None

    # Type-specific context (only the ones relevant to the record_type populate).
    species_ref: str | None = None
    species_entry_ref: str | None = None
    reaction_ref: str | None = None
    reaction_entry_ref: str | None = None
    calculation_ref: str | None = None
    network_ref: str | None = None
    network_solve_ref: str | None = None

    review: RecordReviewBadge | None = None
    created_at: datetime | None = None
    endpoint: str


# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------


class ScientificLiteratureRecordsResponse(BaseModel):
    """Response envelope for ``GET /scientific/literature/{handle}/records``.

    Records are flattened across types and paginated. ``total`` is
    the post-filter (review/visibility/record_type) count before
    pagination.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[LiteratureLinkedRecordSummary]
    pagination: Pagination


__all__ = [
    "LiteratureLinkedRecordSummary",
    "LiteratureLinkedRecordType",
    "LiteratureRecordsRequest",
    "RequestEcho",
    "SUPPORTED_RECORD_TYPES",
    "ScientificLiteratureRecordsResponse",
]
