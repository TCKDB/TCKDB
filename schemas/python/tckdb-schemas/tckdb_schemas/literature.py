"""Literature upload fragment — nested inside other upload requests.

This module is the wire-contract definition for the literature payload
embedded by the thermo, kinetics, conformer, network, transport,
transition-state, computed-reaction, and energy-correction upload flows.
There is no standalone ``/uploads/literature`` route — that is a
backend concern.
"""

from typing import Self

from pydantic import Field, HttpUrl, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import LiteratureKind
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text


class LiteratureUploadRequest(SchemaBase):
    """Literature fragment embedded in other upload requests.

    Not a standalone upload body — there is no ``/uploads/literature`` route.
    A request may be identifier-driven (DOI and/or ISBN) or manual. Identifier
    lookup can enrich missing metadata before a canonical literature row is
    created.

    :param kind: Optional literature kind. Can be inferred from DOI/ISBN when omitted.
    :param title: Optional manual title. Required only for fully manual submissions.
    :param journal: Optional journal or container title.
    :param year: Optional publication year.
    :param volume: Optional volume.
    :param issue: Optional issue.
    :param pages: Optional page range.
    :param doi: Optional DOI identifier.
    :param isbn: Optional ISBN identifier.
    :param url: Optional canonical URL.
    :param publisher: Optional publisher name.
    :param institution: Optional institution name.
    """

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

    @model_validator(mode="after")
    def validate_identifier_or_manual_fields(self) -> Self:
        has_identifier = self.doi is not None or self.isbn is not None
        has_manual_minimum = self.kind is not None and self.title is not None
        if not has_identifier and not has_manual_minimum:
            raise ValueError(
                "Provide DOI/ISBN for lookup, or provide both kind and title for manual literature submission"
            )
        return self
