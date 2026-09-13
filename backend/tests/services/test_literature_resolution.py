from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.literature import Literature
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.literature_resolution import (
    resolve_literature_submission,
    resolve_or_create_literature,
)
from app.services.provenance_warnings import (
    W_LITERATURE_TITLE_MISMATCH,
    W_LITERATURE_YEAR_MISMATCH,
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
    db_conn, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "Should Not Be Used"},
    )

    with Session(db_conn) as session:
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


# ---------------------------------------------------------------------------
# Depositor-vs-fetched-metadata mismatch warnings (#248)
# ---------------------------------------------------------------------------


def _stub_doi_metadata(monkeypatch, **overrides) -> None:
    metadata = {
        "title": "Canonical Title",
        "container-title": ["Journal of Testing"],
        "issued": 2024,
        "volume": "12",
        "issue": "3",
        "page": "101-110",
        "publisher": "Test Publisher",
        "URL": "https://example.org/article",
    }
    metadata.update(overrides)
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: metadata,
    )


def test_mismatched_title_keeps_doi_title_and_warns(monkeypatch) -> None:
    _stub_doi_metadata(monkeypatch)

    request = LiteratureUploadRequest(
        doi="https://doi.org/10.1000/ABC", title="A Completely Different Paper"
    )
    warnings: list[UploadWarning] = []
    resolved = resolve_literature_submission(None, request, warnings_out=warnings)

    # The DOI's title is what gets stored -- precedence is unchanged.
    assert resolved.title == "Canonical Title"

    # And the depositor is told both values disagreed.
    title_warnings = [w for w in warnings if w.code == W_LITERATURE_TITLE_MISMATCH]
    assert len(title_warnings) == 1
    assert "A Completely Different Paper" in title_warnings[0].message
    assert "Canonical Title" in title_warnings[0].message


def test_agreeing_title_carries_no_warning(monkeypatch) -> None:
    _stub_doi_metadata(monkeypatch)

    request = LiteratureUploadRequest(
        doi="https://doi.org/10.1000/ABC", title="Canonical Title"
    )
    warnings: list[UploadWarning] = []
    resolved = resolve_literature_submission(None, request, warnings_out=warnings)

    assert resolved.title == "Canonical Title"
    assert warnings == []


def test_title_differing_only_by_case_and_trailing_period_is_not_flagged(
    monkeypatch,
) -> None:
    _stub_doi_metadata(monkeypatch, title="Canonical Title")

    request = LiteratureUploadRequest(
        doi="https://doi.org/10.1000/ABC",
        title="canonical title.",
    )
    warnings: list[UploadWarning] = []
    resolve_literature_submission(None, request, warnings_out=warnings)

    assert warnings == []


def test_no_doi_or_isbn_carries_no_mismatch_warning() -> None:
    request = LiteratureUploadRequest(kind="report", title="Manual Title")
    warnings: list[UploadWarning] = []
    resolve_literature_submission(None, request, warnings_out=warnings)

    assert warnings == []


def test_mismatched_year_keeps_doi_year_and_warns(monkeypatch) -> None:
    _stub_doi_metadata(monkeypatch)

    request = LiteratureUploadRequest(
        doi="https://doi.org/10.1000/ABC",
        title="Canonical Title",
        year=1999,
    )
    warnings: list[UploadWarning] = []
    resolved = resolve_literature_submission(None, request, warnings_out=warnings)

    assert resolved.year == 2024
    year_warnings = [w for w in warnings if w.code == W_LITERATURE_YEAR_MISMATCH]
    assert len(year_warnings) == 1
    assert "1999" in year_warnings[0].message
    assert "2024" in year_warnings[0].message


def test_mismatched_isbn_title_keeps_isbn_title_and_warns(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_isbn_metadata",
        lambda isbn: {
            "Title": "Canonical Book",
            "Publisher": "Book Publisher",
            "Year": 1995,
        },
    )

    request = LiteratureUploadRequest(
        isbn="0-387-95452-X", title="A Totally Different Book"
    )
    warnings: list[UploadWarning] = []
    resolved = resolve_literature_submission(None, request, warnings_out=warnings)

    assert resolved.title == "Canonical Book"
    title_warnings = [w for w in warnings if w.code == W_LITERATURE_TITLE_MISMATCH]
    assert len(title_warnings) == 1


def test_no_warnings_out_means_no_comparison_is_attempted(monkeypatch) -> None:
    """Callers that pass no sink (the default) get the old, silent behavior."""

    _stub_doi_metadata(monkeypatch)

    request = LiteratureUploadRequest(
        doi="https://doi.org/10.1000/ABC", title="A Completely Different Paper"
    )
    resolved = resolve_literature_submission(None, request)
    assert resolved.title == "Canonical Title"


def test_resolve_or_create_literature_surfaces_mismatch_on_creation(
    db_conn, monkeypatch
) -> None:
    _stub_doi_metadata(monkeypatch)

    with Session(db_conn) as session:
        with session.begin():
            warnings: list[UploadWarning] = []
            literature = resolve_or_create_literature(
                session,
                LiteratureUploadRequest(
                    doi="https://doi.org/10.1000/XYZ",
                    title="A Completely Different Paper",
                ),
                warnings_out=warnings,
            )

            assert literature.title == "Canonical Title"
            assert any(
                w.code == W_LITERATURE_TITLE_MISMATCH for w in warnings
            )


def test_resolve_or_create_literature_reused_row_does_not_refetch_or_warn(
    db_conn, monkeypatch
) -> None:
    """Adopting an existing row never re-fetches metadata, so there is
    nothing to compare and no warning -- even though the depositor's title
    here disagrees with the title already stored on the existing row."""

    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: (_ for _ in ()).throw(
            AssertionError("fetch_doi_metadata must not be called")
        ),
    )

    with Session(db_conn) as session:
        with session.begin():
            existing = Literature(
                kind="article",
                title="Existing Title",
                doi="10.1000/def",
            )
            session.add(existing)
            session.flush()

            warnings: list[UploadWarning] = []
            resolved = resolve_or_create_literature(
                session,
                LiteratureUploadRequest(
                    doi="DOI:10.1000/DEF", title="Some Other Title"
                ),
                warnings_out=warnings,
            )

            assert resolved.id == existing.id
            assert warnings == []
