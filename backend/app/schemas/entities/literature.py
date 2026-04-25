"""Entity schemas for literature and literature-author link models."""

from typing import Self

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.db.models.common import LiteratureKind
from app.schemas.common import ORMBaseSchema, SchemaBase, TimestampedReadSchema
from app.schemas.utils import normalize_optional_text, normalize_required_text


# ---------------------------------------------------------------------------
# Literature ↔ Author link
# ---------------------------------------------------------------------------


class LiteratureAuthorBase(BaseModel):
    """Shared fields for a literature-author link.

    :param author_id: Referenced author row.
    :param author_order: Position in the author list (1-based).
    """

    author_id: int
    author_order: int = Field(ge=1)


class LiteratureAuthorCreate(LiteratureAuthorBase, SchemaBase):
    """Nested create payload for a literature-author link."""


class LiteratureAuthorUpdate(SchemaBase):
    """Patch schema for a literature-author link."""

    author_order: int | None = Field(default=None, ge=1)


class LiteratureAuthorRead(LiteratureAuthorBase, ORMBaseSchema):
    """Read schema for a literature-author link."""

    literature_id: int


# ---------------------------------------------------------------------------
# Literature
# ---------------------------------------------------------------------------


class LiteratureBase(BaseModel):
    kind: LiteratureKind
    title: str = Field(min_length=1)

    journal: str | None = None
    year: int | None = Field(default=None, ge=1, le=3000)
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None

    doi: str | None = None
    isbn: str | None = None
    url: HttpUrl | None = None

    publisher: str | None = None
    institution: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.journal = normalize_optional_text(self.journal)
        self.volume = normalize_optional_text(self.volume)
        self.issue = normalize_optional_text(self.issue)
        self.pages = normalize_optional_text(self.pages)
        self.doi = normalize_optional_text(self.doi)
        self.isbn = normalize_optional_text(self.isbn)
        self.publisher = normalize_optional_text(self.publisher)
        self.institution = normalize_optional_text(self.institution)
        return self


class LiteratureCreate(LiteratureBase, SchemaBase):
    """Create schema for a literature record.

    Nested creation is supported for author links.
    """

    authors: list[LiteratureAuthorCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_authors(self) -> Self:
        ids = [a.author_id for a in self.authors]
        if len(set(ids)) != len(ids):
            raise ValueError("Literature authors must be unique by author_id.")
        orders = [a.author_order for a in self.authors]
        if len(set(orders)) != len(orders):
            raise ValueError(
                "Literature authors must be unique by author_order."
            )
        return self


class LiteratureUpdate(SchemaBase):
    kind: LiteratureKind | None = None
    title: str | None = Field(default=None, min_length=1)

    journal: str | None = None
    year: int | None = Field(default=None, ge=1, le=3000)
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None

    doi: str | None = None
    isbn: str | None = None
    url: HttpUrl | None = None

    publisher: str | None = None
    institution: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.journal = normalize_optional_text(self.journal)
        self.volume = normalize_optional_text(self.volume)
        self.issue = normalize_optional_text(self.issue)
        self.pages = normalize_optional_text(self.pages)
        self.doi = normalize_optional_text(self.doi)
        self.isbn = normalize_optional_text(self.isbn)
        self.publisher = normalize_optional_text(self.publisher)
        self.institution = normalize_optional_text(self.institution)
        return self


class LiteratureRead(LiteratureBase, TimestampedReadSchema):
    """Read schema for a literature record."""

    authors: list[LiteratureAuthorRead] = Field(default_factory=list)
