from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import LiteratureKind
from app.db.models.literature import Literature
from app.schemas.entities.literature import LiteratureCreate
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.literature_metadata import (
    fetch_doi_metadata,
    fetch_isbn_metadata,
    normalize_doi,
    normalize_isbn,
)


def _kind_from_identifiers(
    request: LiteratureUploadRequest,
    *,
    normalized_doi: str | None,
    normalized_isbn: str | None,
) -> LiteratureKind:
    if request.kind is not None:
        return request.kind
    if normalized_isbn is not None:
        return LiteratureKind.book
    if normalized_doi is not None:
        return LiteratureKind.article
    raise ValueError(
        "Unable to infer literature kind without DOI, ISBN, or explicit kind"
    )


def _metadata_to_fields(
    metadata: dict[str, object], *, source: str
) -> dict[str, object]:
    if source == "doi":
        return {
            "title": metadata.get("title"),
            "journal": (
                (metadata.get("container-title") or [None])[0]
                if isinstance(metadata.get("container-title"), list)
                else metadata.get("container-title")
            ),
            "year": metadata.get("issued"),
            "volume": metadata.get("volume"),
            "issue": metadata.get("issue"),
            "pages": metadata.get("page"),
            "publisher": metadata.get("publisher"),
            "url": metadata.get("URL"),
        }

    return {
        "title": metadata.get("Title"),
        "publisher": metadata.get("Publisher"),
        "year": metadata.get("Year"),
    }


def resolve_literature_submission(
    session: Session,
    request: LiteratureUploadRequest,
) -> LiteratureCreate:
    """Resolve a workflow literature submission into a canonical create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing literature submission payload.
    :returns: Canonical ``LiteratureCreate`` schema.
    :raises ValueError: If ISBN normalization fails or manual submission lacks required fields.
    """

    normalized_doi = normalize_doi(request.doi)
    normalized_isbn = normalize_isbn(request.isbn) if request.isbn is not None else None

    if request.isbn is not None and normalized_isbn is None:
        raise ValueError("Invalid ISBN")

    metadata_fields: dict[str, object] = {}
    if normalized_doi is not None:
        metadata_fields = _metadata_to_fields(
            fetch_doi_metadata(normalized_doi) or {}, source="doi"
        )
    elif normalized_isbn is not None:
        metadata_fields = _metadata_to_fields(
            fetch_isbn_metadata(normalized_isbn) or {},
            source="isbn",
        )

    kind = _kind_from_identifiers(
        request,
        normalized_doi=normalized_doi,
        normalized_isbn=normalized_isbn,
    )

    title = metadata_fields.get("title") or request.title
    if title is None:
        raise ValueError("Resolved literature submission still requires a title")

    return LiteratureCreate(
        kind=kind,
        title=title,
        journal=metadata_fields.get("journal") or request.journal,
        year=metadata_fields.get("year") or request.year,
        volume=metadata_fields.get("volume") or request.volume,
        issue=metadata_fields.get("issue") or request.issue,
        pages=metadata_fields.get("pages") or request.pages,
        doi=normalized_doi,
        isbn=normalized_isbn,
        url=metadata_fields.get("url") or request.url,
        publisher=metadata_fields.get("publisher") or request.publisher,
        institution=request.institution,
    )


def _select_existing_literature(
    session: Session,
    *,
    normalized_doi: str | None,
    normalized_isbn: str | None,
) -> Literature | None:
    """Look up an existing citation by normalized DOI, then normalized ISBN.

    Shared by the pre-insert check and the concurrent-insert recovery so both
    answer "is this citation already here?" the same way.

    :raises ValueError: If the DOI and ISBN resolve to two different rows.
    """
    existing_by_doi = None
    if normalized_doi is not None:
        existing_by_doi = session.scalar(
            select(Literature).where(Literature.doi == normalized_doi)
        )

    existing_by_isbn = None
    if normalized_isbn is not None:
        existing_by_isbn = session.scalar(
            select(Literature).where(Literature.isbn == normalized_isbn)
        )

    if (
        existing_by_doi is not None
        and existing_by_isbn is not None
        and existing_by_doi.id != existing_by_isbn.id
    ):
        raise ValueError("DOI and ISBN resolve to different existing literature rows")

    return existing_by_doi or existing_by_isbn


def resolve_or_create_literature(
    session: Session,
    request: LiteratureUploadRequest,
) -> Literature:
    """Resolve or create a literature row from workflow submission data.

    Idempotent across transactions as well as within one: a concurrent upload
    that creates the same citation first is adopted rather than collided with.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing literature submission payload.
    :returns: Existing or newly created ``Literature`` row.
    :raises ValueError: If identifier normalization or literature resolution fails.
    """

    normalized_doi = normalize_doi(request.doi)
    normalized_isbn = normalize_isbn(request.isbn) if request.isbn is not None else None

    existing = _select_existing_literature(
        session,
        normalized_doi=normalized_doi,
        normalized_isbn=normalized_isbn,
    )
    if existing is not None:
        return existing

    literature_create = resolve_literature_submission(session, request)

    def _build() -> Literature:
        return Literature(
            kind=literature_create.kind,
            title=literature_create.title,
            journal=literature_create.journal,
            year=literature_create.year,
            volume=literature_create.volume,
            issue=literature_create.issue,
            pages=literature_create.pages,
            doi=literature_create.doi,
            isbn=literature_create.isbn,
            url=(
                str(literature_create.url)
                if literature_create.url is not None
                else None
            ),
            publisher=literature_create.publisher,
            institution=literature_create.institution,
        )

    # Check-then-insert against the normalized DOI/ISBN uniqueness indexes
    # (``ix_literature_doi_normalized`` / ``ix_literature_isbn_normalized``).
    # Two contributors citing the same paper at the same time both read
    # ``None`` above, and the loser's violation would abort a transaction that
    # already holds its entire upload — literature is resolved *during*
    # deposit, so the collateral is scientific records, not a citation.
    #
    # Every sibling resolver (species, reaction, geometry, software,
    # calculation, energy-correction) already wraps its identity insert this
    # way; this one was the exception. Losing the race is not a conflict: the
    # winner's row is the same citation this call was about to create, so the
    # loser adopts it, exactly as it would have done had it arrived a moment
    # later.
    try:
        with session.begin_nested():
            literature = _build()
            session.add(literature)
            session.flush()
    except IntegrityError:
        existing = _select_existing_literature(
            session,
            normalized_doi=normalized_doi,
            normalized_isbn=normalized_isbn,
        )
        if existing is None:
            # Not the identity race — a genuinely broken row. Let it out.
            raise
        return existing
    return literature
