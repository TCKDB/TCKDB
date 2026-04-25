from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.literature import Literature
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.literature_resolution import (
    resolve_literature_submission,
    resolve_or_create_literature,
)


def test_resolve_literature_submission_enriches_from_doi(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Canonical Title",
            "container-title": ["Journal of Testing"],
            "issued": 2024,
            "volume": "12",
            "issue": "3",
            "page": "101-110",
            "publisher": "Test Publisher",
            "URL": "https://example.org/article",
        },
    )

    request = LiteratureUploadRequest(doi="https://doi.org/10.1000/ABC")
    resolved = resolve_literature_submission(None, request)  # session currently unused

    assert resolved.kind.value == "article"
    assert resolved.title == "Canonical Title"
    assert resolved.journal == "Journal of Testing"
    assert resolved.year == 2024
    assert resolved.volume == "12"
    assert resolved.issue == "3"
    assert resolved.pages == "101-110"
    assert resolved.publisher == "Test Publisher"
    assert resolved.doi == "10.1000/abc"
    assert str(resolved.url) == "https://example.org/article"


def test_resolve_literature_submission_enriches_from_isbn(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_isbn_metadata",
        lambda isbn: {
            "Title": "Canonical Book",
            "Publisher": "Book Publisher",
            "Year": 1995,
        },
    )

    request = LiteratureUploadRequest(isbn="0-387-95452-X")
    resolved = resolve_literature_submission(None, request)

    assert resolved.kind.value == "book"
    assert resolved.title == "Canonical Book"
    assert resolved.publisher == "Book Publisher"
    assert resolved.year == 1995
    assert resolved.isbn == "9780387954523"


def test_resolve_literature_submission_manual_fallback() -> None:
    request = LiteratureUploadRequest(kind="report", title="Manual Title")
    resolved = resolve_literature_submission(None, request)

    assert resolved.kind.value == "report"
    assert resolved.title == "Manual Title"
    assert resolved.doi is None
    assert resolved.isbn is None


def test_resolve_or_create_literature_reuses_existing_row(
    db_engine, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "Should Not Be Used"},
    )

    with Session(db_engine) as session:
        with session.begin():
            existing = Literature(
                kind="article",
                title="Existing",
                doi="10.1000/abc",
            )
            session.add(existing)
            session.flush()

            resolved = resolve_or_create_literature(
                session,
                LiteratureUploadRequest(doi="DOI:10.1000/ABC"),
            )

            assert resolved.id == existing.id
            assert session.scalar(
                select(Literature).where(Literature.id == existing.id)
            )
