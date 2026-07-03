"""Service implementation for the scientific literature detail surface.

One endpoint:

- ``GET /scientific/literature/{ref_or_id}`` — one literature record
  with citation metadata, authors, identifiers, and inverse record
  counts.

Literature is not a reviewable record type — there is no
``SubmissionRecordType.literature`` — so the response carries no
review badge for the literature itself. Reviews surface only on the
linked records under the companion ``/records`` endpoint.

See ``backend/docs/specs/scientific_literature_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.author import Author
from app.db.models.calculation import Calculation
from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.literature_author import LiteratureAuthor
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.schemas.reads.scientific_literature import (
    AvailableLiteratureSections,
    LiteratureAuthorSummary,
    LiteratureCoreBlock,
    LiteratureIdentifiers,
    LiteratureRecordCounts,
    RequestEcho,
    ScientificLiteratureDetailResponse,
    ScientificLiteratureRecord,
)
from app.services.scientific_read.common import validate_includes
from app.services.scientific_read.handles import resolve_literature_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# ---------------------------------------------------------------------------
# Include policy
# ---------------------------------------------------------------------------


# Literature is a small reference object: authors and record_counts
# are part of the default response, but the tokens are kept legal as
# no-op affordances for callers that want to be explicit. Only
# ``internal_ids`` materially changes the response.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "authors",
    "record_counts",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


def get_literature(
    session: Session,
    *,
    literature_handle: str,
    include: list[str] | None = None,
) -> ScientificLiteratureDetailResponse:
    """Resolve a literature handle and return its scientific projection.

    Path-handle semantics match the rest of the scientific surface:

    - Integer string: SELECT by id.
    - Public ref ``lit_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.
    """
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/literature/{literature_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    lit_id = resolve_literature_handle(session, literature_handle)
    lit = session.get(Literature, lit_id)
    if lit is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"literature not found (literature_id={lit_id})",
            code="handle_not_found",
        )

    authors = _load_authors(session, lit.id)
    counts = _load_record_counts(session, lit.id)

    record = ScientificLiteratureRecord(
        literature=_build_core(lit),
        identifiers=LiteratureIdentifiers(
            doi=lit.doi,
            isbn=lit.isbn,
            url=lit.url,
        ),
        authors=authors,
        record_counts=counts,
        available_sections=AvailableLiteratureSections(
            has_authors=bool(authors),
            has_identifiers=any(
                v is not None for v in (lit.doi, lit.isbn, lit.url)
            ),
            has_linked_records=counts.total_records > 0,
        ),
    )

    return ScientificLiteratureDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        # Literature is not reviewable; the summary is always empty.
        review_summary=ReviewStatusSummary(),
        record=record,
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_core(lit: Literature) -> LiteratureCoreBlock:
    return LiteratureCoreBlock(
        literature_id=lit.id,
        literature_ref=lit.public_ref,
        kind=lit.kind,
        title=lit.title,
        journal=lit.journal,
        year=lit.year,
        volume=lit.volume,
        issue=lit.issue,
        pages=lit.pages,
        publisher=lit.publisher,
        institution=lit.institution,
        created_at=lit.created_at,
    )


def _load_authors(
    session: Session, literature_id: int
) -> list[LiteratureAuthorSummary]:
    rows = session.execute(
        select(
            LiteratureAuthor.author_order,
            Author.id,
            Author.full_name,
            Author.given_name,
            Author.family_name,
            Author.orcid,
        )
        .join(Author, Author.id == LiteratureAuthor.author_id)
        .where(LiteratureAuthor.literature_id == literature_id)
        .order_by(LiteratureAuthor.author_order.asc())
    ).all()
    return [
        LiteratureAuthorSummary(
            author_id=row.id,
            full_name=row.full_name,
            given_name=row.given_name,
            family_name=row.family_name,
            orcid=row.orcid,
            position=row.author_order,
        )
        for row in rows
    ]


def _load_record_counts(
    session: Session, literature_id: int
) -> LiteratureRecordCounts:
    """Count linked records of each direct-link type in a single pass.

    Each scalar count is a bounded ``COUNT(*)`` against the linked
    table's ``literature_id`` column. No filtering by review status is
    applied here — the counts represent the total inverse fan-out.
    """
    def _count(model_cls) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(model_cls)
                .where(model_cls.literature_id == literature_id)
            )
            or 0
        )

    calcs = _count(Calculation)
    thermo = _count(Thermo)
    kinetics = _count(Kinetics)
    statmech = _count(Statmech)
    transport = _count(Transport)
    networks = _count(Network)
    nsolves = _count(NetworkSolve)

    return LiteratureRecordCounts(
        calculations=calcs,
        thermo=thermo,
        kinetics=kinetics,
        statmech=statmech,
        transport=transport,
        networks=networks,
        network_solves=nsolves,
        total_records=calcs
        + thermo
        + kinetics
        + statmech
        + transport
        + networks
        + nsolves,
    )


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "get_literature",
]
