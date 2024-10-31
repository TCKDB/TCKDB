import re
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from matplotlib.pylab import f
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
    ValidationInfo,
)

from tckdb.backend.app.schemas.author import AuthorCreate, AuthorReadLiterature
from tckdb.backend.app.schemas.connection_schema import ConnectionBase
from tckdb.backend.app.utils.doi_lookup import fetch_doi_metadata
from tckdb.backend.app.utils.isbn_lookup import fetch_isbn_metadata


class ISBN(str):
    """
    The ISBN class - Supports ISBN-10 and ISBN-13
    """

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, values):
        if not isinstance(v, str):
            raise TypeError("ISBN must be a string")

        # Remone hyphens and spaces
        isbn = re.sub(r"[\s-]", "", v)

        if len(isbn) == 10:
            if not re.match(r"^\d{9}[\dXx]$", isbn):
                raise ValueError("Invalid ISBN-10")
            if not cls.is_valid_isbn10(isbn):
                raise ValueError("Invalid ISBN-10 check digit")
            return cls(isbn.upper())
        elif len(isbn) == 13:
            if not isbn.isdigit():
                raise ValueError("Invalid ISBN-13")
            if not cls.is_valid_isbn13(isbn):
                raise ValueError("Invalid ISBN-13 check digit")
            return cls(isbn)
        else:
            raise ValueError("Invalid ISBN length - must be 10 or 13 digits")

    @staticmethod
    def is_valid_isbn10(isbn10: str) -> bool:
        """
        Checks if the ISBN-10 check digit is valid.

        Args:
            isbn10 (str): The ISBN-10 string.

        Returns:
            bool: True if valid, False otherwise.
        """
        total = 0
        for i in range(9):
            if not isbn10[i].isdigit():
                return False
            total += int(isbn10[i]) * (i + 1)
        check_digit = isbn10[9]
        if check_digit in ["X", "x"]:
            total += 10 * 10
        elif check_digit.isdigit():
            total += int(check_digit) * 10
        else:
            return False
        return total % 11 == 0

    @staticmethod
    def is_valid_isbn13(isbn13: str) -> bool:
        """
        Checks if the ISBN-13 check digit is valid.

        Args:
            isbn13 (str): The ISBN-13 string.

        Returns:
            bool: True if valid, False otherwise.
        """
        total = 0
        for i in range(12):
            digit = int(isbn13[i])
            if i % 2 == 0:
                total += digit
            else:
                total += 3 * digit
        check_digit = (10 - (total % 10)) % 10
        return check_digit == int(isbn13[12])


class LiteratureType(str, Enum):
    """
    The supported literature reference types
    """

    article = "article"
    book = "book"
    thesis = "thesis"


class LiteratureBase(BaseModel):
    """
    A LiteratureBase class (shared properties)
    """

    type: Optional[LiteratureType] = Field(
        None, title="The literature type, either article, book, or thesis"
    )
    title: Optional[str] = Field(
        None, max_length=255, title="The literature source title"
    )
    year: Optional[int] = Field(None, ge=1500, le=9999, title="The publication year")
    authors: Optional[List[AuthorCreate]] = Field(
        None, title="Authors for the literature source"
    )
    journal: Optional[str] = Field(None, title="The journal name")
    publisher: Optional[str] = Field(None, title="The publisher name")
    volume: Optional[int] = Field(None, title="The volume number")
    issue: Optional[int] = Field(None, title="The issue number")
    page_start: Optional[int] = Field(None, title="The first page")
    page_end: Optional[int] = Field(None, title="The last page")
    editors: Optional[str] = Field(None, title="The editor names for a book")
    edition: Optional[str] = Field(None, title="The edition for a book")
    chapter_title: Optional[str] = Field(None, title="The chapter title for a book")
    publication_place: Optional[str] = Field(
        None, title="The publication place for a book"
    )
    advisor: Optional[str] = Field(None, title="The dissertation advisor for a thesis")
    doi: Optional[str] = Field(None, title="The DOI")
    isbn: Optional[ISBN] = Field(None, title="The ISBN")
    url: Optional[HttpUrl] = Field(None, title="The publication URL address")
    model_config = ConfigDict(from_attributes=True, extra="forbid")


    @field_validator("advisor", mode="after")
    @classmethod
    def validate_advisor(cls, v, info: ValidationInfo):
        lit_type = info.data.get("type")
        if lit_type == LiteratureType.thesis and not v:
            raise ValueError("Value error, advisor is required for a thesis")
        return v

    # @field_validator("journal", mode="after")
    # @classmethod
    # def validate_journal(cls, v, info: ValidationInfo):
    #     lit_type = info.data.get("type")
    #     if lit_type == LiteratureType.article and not v:
    #         raise ValueError("Value error, journal is required for an article")
    #     return v
    @model_validator(mode="after")
    def validate_journal(cls, values):
        if values.type == LiteratureType.article and not values.journal:
            raise ValueError("Journal is required for an article")
        return values

    @field_validator("volume", mode="after")
    @classmethod
    def validate_volume(cls, v, info: ValidationInfo):
        lit_type = info.data.get("type")
        if lit_type == LiteratureType.article and not v:
            raise ValueError("Value error, volume is required for an article")
        return v

    @field_validator("issue", mode="after")
    @classmethod
    def validate_issue(cls, v, info: ValidationInfo):
        lit_type = info.data.get("type")
        if lit_type == LiteratureType.article and not v:
            raise ValueError("Value error, issue is required for an article")
        return v

    @field_validator("editors", mode="after")
    @classmethod
    def validate_editors(cls, v, info: ValidationInfo):
        lit_type = info.data.get("type")
        if lit_type == LiteratureType.book and not v:
            raise ValueError("Value error, editors are required for a book")
        return v

    @field_validator("edition", mode="after")
    @classmethod
    def validate_edition(cls, v, info: ValidationInfo):
        lit_type = info.data.get("type")
        if lit_type == LiteratureType.book and not v:
            raise ValueError("Value error, edition is required for a book")
        return v

    @field_validator("title")
    @classmethod
    def check_title(cls, v):
        if not v:
            raise ValueError("Title is required")
        if "_" in v:
            raise ValueError("Title cannot contain underscores")
        return v

    @field_validator("year")
    @classmethod
    def check_year(cls, v):
        current_year = datetime.now().year
        if v > current_year:
            raise ValueError(
                f"The year {v} is in the future. It must be <= {current_year}."
            )
        if v < 1500:
            raise ValueError("The year must be greater than or equal to 1500.")
        return v

    @field_validator("page_start")
    def check_page_start(cls, v, values: ValidationInfo):
        if values.data["type"] == LiteratureType.article and not v:
            raise ValueError("Page start is required for an article")
        return v

    @field_validator("page_end")
    def check_page_end(cls, v, values: ValidationInfo):
        if values.data["type"] == LiteratureType.article and not v:
            raise ValueError("Page end is required for an article")
        # Must be greater than or equal to page_start
        if values.data["page_start"] and v < values.data["page_start"]:
            raise ValueError(
                "Page end must be greater than or equal to page start."
                f'Received page_start={values.data.get["page_start"]}, page_end={v}'
            )
        return v

    @field_validator("doi")
    def check_doi(cls, v, values: ValidationInfo):
        if not v and values.data["type"] != LiteratureType.article:
            return v
        elif not v and values.data["type"] == LiteratureType.article:
            raise ValueError("DOI is required for an article")
        if not v.startswith("10."):
            raise ValueError("DOI must start with 10.")
        metadata = fetch_doi_metadata(v)
        if metadata:
            # Replace or set the title, year, and publisher if not already set
            values["title"] = metadata.get("title", values["title"])
            values["year"] = metadata.get("issued", values["year"])
            values["publisher"] = metadata.get("publisher", values["publisher"])
            values["volume"] = metadata.get("volume", values["volume"])
            pages = metadata.get("page")
            if pages:
                # split the pages into start and end
                pages = pages.split("-")
                values["page_start"] = values.data.get("page_start", int(pages[0]))
                values["page_end"] = values.data.get("page_end", int(pages[1]))
            values["journal"] = metadata.get("container-title", values["journal"])
            values["issue"] = metadata.get("issue", values["issue"])
            if metadata.get("author"):
                authors = []
                for author in metadata["author"]:
                    first_name, last_name = cls.parse_author_name(
                        author.get("given", ""), author.get("family", "")
                    )
                    # Check if orcid is available
                    orcid = None
                    if author.get("ORCID"):
                        orcid = author["ORCID"]
                        # transform the orcid into the proper format http://orcid.org/0000-0003-0019-8806' -> '0000-0003-0019-8806'
                        orcid = orcid.split("/")[-1]
                    authors.append(
                        AuthorCreate(
                            first_name=first_name, last_name=last_name, orcid=orcid
                        )
                    )
                    values["authors"] = authors

        return v

    @field_validator("isbn")
    def process_isbn(cls, v, values: ValidationInfo, **kwargs):
        if v:
            metadata = fetch_isbn_metadata(v)
            if metadata:
                # Replace or set the title, year, and publisher if not already set
                values.data["title"] = metadata.get("Title", values.data["title"])
                values.data["year"] = metadata.get("Year", values.data["year"])
                values.data["publisher"] = metadata.get(
                    "Publisher", values.data["publisher"]
                )
                if metadata.get("Authors"):
                    authors = []
                    for author in metadata["Authors"]:
                        first_name, last_name = cls.parse_author_name(author)
                        authors.append(
                            AuthorCreate(first_name=first_name, last_name=last_name)
                        )
                    values.data["authors"] = authors
        return v

    @field_validator("url")
    def convert_url_to_str(cls, v):
        """Convert the URL to a string"""
        return str(v) if v is not None else None

    @staticmethod
    def parse_author_name(full_name: str) -> Tuple[str, str]:
        """
        Parses a full author name into first and last names
        """
        parts = full_name.strip().split()
        if len(parts) == 1:
            return parts[0], ""
        first_name = " ".join(parts[:-1])
        last_name = parts[-1]
        return first_name, last_name


class LiteratureCreate(LiteratureBase):
    """
    A LiteratureCreate class (properties to receive on literature creation)
    """

    type: LiteratureType = Field(
        ..., title="The literature type, either article, book, or thesis"
    )
    title: str = Field(..., max_length=255, title="The literature source title")
    authors: List[AuthorCreate] = Field(..., title="Authors for the literature source")
    year: int = Field(..., ge=1500, le=9999, title="The publication year")
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("authors", mode="before")
    def check_authors(cls, v):
        if not v:
            raise ValueError("Authors are required")
        return v


class LiteratureCreateBatch(LiteratureBase, ConnectionBase):
    """
    A LiteratureCreateBatch class (properties to receive on literature creation)
    """

    type: LiteratureType = Field(
        ..., title="The literature type, either article, book, or thesis"
    )
    title: str = Field(..., max_length=255, title="The literature source title")
    authors: List[AuthorCreate] = Field(..., title="Authors for the literature source")
    year: int = Field(..., ge=1500, le=9999, title="The publication year")
    model_config = ConfigDict(from_attributes=True, extra="forbid")



class LiteratureUpdate(LiteratureBase):
    pass


class LiteratureRead(LiteratureBase):
    id: int
    authors: List[AuthorReadLiterature]
