"""Read schemas for the scientific literature detail surface.

Covers:

- ``GET /api/v1/scientific/literature/{literature_ref_or_id}``

Literature is **not a reviewable record type** (no
``SubmissionRecordType.literature``); the response therefore omits a
review badge on the core block and surfaces only the linked
scientific records' review state via the inverse records endpoint.

See ``backend/docs/specs/scientific_literature_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import LiteratureKind
from app.schemas.reads.scientific_common import ReviewStatusSummary

# ---------------------------------------------------------------------------
# Request echo
# ---------------------------------------------------------------------------


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class LiteratureCoreBlock(BaseModel):
    """Direct literature-row citation metadata.

    Mirrors the columns on :class:`app.db.models.literature.Literature`
    plus the public ref. Literature is not reviewable, so no review
    badge is exposed here.
    """

    literature_id: int | None = None
    literature_ref: str
    kind: LiteratureKind
    title: str
    journal: str | None = None
    year: int | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    publisher: str | None = None
    institution: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


class LiteratureIdentifiers(BaseModel):
    """External identifiers carried on a literature row.

    The fields surface as-stored in the DB. Normalized lookups (against
    ``ix_literature_doi_normalized`` etc.) are handled by the upload
    service; this surface returns the verbatim string.
    """

    doi: str | None = None
    isbn: str | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# Author summary
# ---------------------------------------------------------------------------


class LiteratureAuthorSummary(BaseModel):
    """One author of a literature row, with position metadata.

    ``author_ref`` is unconditionally ``None`` today because the
    :class:`app.db.models.author.Author` model does not yet carry a
    public ref (no ``PublicRefMixin``). The field is kept on the
    schema so it stays forward-compatible if/when authors gain refs.
    """

    author_ref: str | None = None
    author_id: int | None = None
    full_name: str
    given_name: str | None = None
    family_name: str | None = None
    orcid: str | None = None
    position: int | None = None


# ---------------------------------------------------------------------------
# Record-counts summary
# ---------------------------------------------------------------------------


class LiteratureRecordCounts(BaseModel):
    """Inverse fan-out: how many records of each type cite this literature.

    Only record types with a **direct** ``literature_id`` FK are counted
    in v0. Indirect linkage (e.g. network_kinetics → network_solve →
    literature) is not yet exposed — see the literature reads spec.
    """

    calculations: int = 0
    thermo: int = 0
    kinetics: int = 0
    statmech: int = 0
    transport: int = 0
    networks: int = 0
    network_solves: int = 0
    total_records: int = 0


# ---------------------------------------------------------------------------
# Available sections
# ---------------------------------------------------------------------------


class AvailableLiteratureSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_authors: bool
    has_identifiers: bool
    has_linked_records: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificLiteratureRecord(BaseModel):
    """One literature row projected as a scientific record.

    Default response carries the core metadata + identifiers + record
    counts + an authors list. Heavy include behavior is intentionally
    minimal here: literature is a small reference object, so authors
    and counts are always present (with ``include=authors`` /
    ``include=record_counts`` legal as no-op affordances for callers
    that want to be explicit).
    """

    literature: LiteratureCoreBlock
    identifiers: LiteratureIdentifiers
    authors: list[LiteratureAuthorSummary] = Field(default_factory=list)
    record_counts: LiteratureRecordCounts
    available_sections: AvailableLiteratureSections


class ScientificLiteratureDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/literature/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificLiteratureRecord


__all__ = [
    "AvailableLiteratureSections",
    "LiteratureAuthorSummary",
    "LiteratureCoreBlock",
    "LiteratureIdentifiers",
    "LiteratureRecordCounts",
    "RequestEcho",
    "ScientificLiteratureDetailResponse",
    "ScientificLiteratureRecord",
]
