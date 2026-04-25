"""Tests for LiteratureAuthor schemas in app/schemas/entities/literature.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.entities.literature import (
    LiteratureAuthorCreate,
    LiteratureAuthorRead,
    LiteratureCreate,
    LiteratureRead,
)


class TestLiteratureAuthorCreate:
    def test_valid(self) -> None:
        la = LiteratureAuthorCreate(author_id=1, author_order=1)
        assert la.author_id == 1

    def test_rejects_zero_order(self) -> None:
        with pytest.raises(ValidationError):
            LiteratureAuthorCreate(author_id=1, author_order=0)

    def test_rejects_negative_order(self) -> None:
        with pytest.raises(ValidationError):
            LiteratureAuthorCreate(author_id=1, author_order=-1)


class TestLiteratureCreateWithAuthors:
    def test_valid_with_authors(self) -> None:
        lit = LiteratureCreate(
            kind="article",
            title="A Study",
            authors=[
                LiteratureAuthorCreate(author_id=1, author_order=1),
                LiteratureAuthorCreate(author_id=2, author_order=2),
            ],
        )
        assert len(lit.authors) == 2

    def test_rejects_duplicate_author_id(self) -> None:
        with pytest.raises(ValidationError, match="unique by author_id"):
            LiteratureCreate(
                kind="article",
                title="A Study",
                authors=[
                    LiteratureAuthorCreate(author_id=1, author_order=1),
                    LiteratureAuthorCreate(author_id=1, author_order=2),
                ],
            )

    def test_rejects_duplicate_author_order(self) -> None:
        with pytest.raises(ValidationError, match="unique by author_order"):
            LiteratureCreate(
                kind="article",
                title="A Study",
                authors=[
                    LiteratureAuthorCreate(author_id=1, author_order=1),
                    LiteratureAuthorCreate(author_id=2, author_order=1),
                ],
            )

    def test_allows_empty_authors(self) -> None:
        lit = LiteratureCreate(kind="article", title="A Study")
        assert lit.authors == []


class TestLiteratureAuthorRead:
    def test_from_orm(self) -> None:
        la = SimpleNamespace(
            literature_id=1, author_id=5, author_order=3,
        )
        read = LiteratureAuthorRead.model_validate(la)
        assert read.literature_id == 1
        assert read.author_id == 5
        assert read.author_order == 3


class TestLiteratureReadWithAuthors:
    def test_from_orm_with_authors(self) -> None:
        author1 = SimpleNamespace(
            literature_id=1, author_id=1, author_order=1,
        )
        author2 = SimpleNamespace(
            literature_id=1, author_id=2, author_order=2,
        )
        lit = SimpleNamespace(
            id=1,
            kind="article",
            title="A Study",
            journal="J. Chem.", year=2024, volume="1", issue="2",
            pages="10-20", doi="10.1000/abc", isbn=None,
            url=None, publisher=None, institution=None,
            created_at="2024-01-01T00:00:00",
            authors=[author1, author2],
        )
        read = LiteratureRead.model_validate(lit)
        assert read.id == 1
        assert len(read.authors) == 2
        assert read.authors[0].author_order == 1
        assert read.authors[1].author_id == 2
