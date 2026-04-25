import pytest
from pydantic import ValidationError

from app.schemas.entities.literature import LiteratureCreate, LiteratureUpdate
from app.services.literature_metadata import normalize_doi, normalize_isbn


def test_literature_schema_validates_year_and_url() -> None:
    literature = LiteratureCreate(
        kind="article",
        title="  Example Paper  ",
        year=2024,
        url="https://example.org/paper",
    )

    assert literature.title == "Example Paper"
    assert str(literature.url) == "https://example.org/paper"

    with pytest.raises(ValidationError):
        LiteratureCreate(
            kind="article",
            title="Bad Year",
            year=0,
        )


def test_literature_update_normalizes_optional_text_fields() -> None:
    update = LiteratureUpdate(
        doi=" 10.1000/ABC ",
        isbn=" 978-0-123456-47-2 ",
        publisher="  Example Press  ",
    )

    assert update.doi == "10.1000/ABC"
    assert update.isbn == "978-0-123456-47-2"
    assert update.publisher == "Example Press"


def test_literature_metadata_normalizers() -> None:
    assert normalize_doi(" https://doi.org/10.1000/ABC ") == "10.1000/abc"
    assert normalize_doi("DOI:10.1000/ABC") == "10.1000/abc"
    assert normalize_isbn(" 978-0-123456-47-2 ") == "9780123456472"
    assert normalize_isbn(" 0-387-95452-X ") == "9780387954523"
    assert normalize_isbn("9780387954523") == "9780387954523"
    assert normalize_isbn("9780387954528") is None
