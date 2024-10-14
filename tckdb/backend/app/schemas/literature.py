"""
TCKDB backend app schemas literature module
"""

from enum import Enum
from typing import Dict, Optional, List
from datetime import datetime

from pydantic import BaseModel, Field, root_validator, validator

from tckdb.backend.app.schemas.author import AuthorRead, AuthorCreate, AuthorReadLiterature


class LiteratureTypeEnum(str, Enum):
    """
    The supported literature reference types
    """
    article = 'article'
    book = 'book'
    thesis = 'thesis'


class LiteratureBase(BaseModel):
    """
    A LiteratureBase class (shared properties)
    """
    type: Optional[LiteratureTypeEnum] = Field(None, title='The literature type, either article, book, or thesis')
    #authors: Optional[List[AuthorCreate]] = Field(None, title='A list of authors for the literature source')
    title: Optional[str] = Field(None, max_length=255, title='The literature source title')
    year: Optional[int] = Field(None, ge=1500, le=9999, title='The publication year')
    journal: Optional[str] = Field(None, max_length=255, title='The Journal name (for an article)')
    publisher: Optional[str] = Field(None, max_length=255, title='The publisher name (for a book)')
    volume: Optional[int] = Field(None, gt=0, title='The volume number (for an article, optional for a book)')
    issue: Optional[int] = Field(None, gt=0, title='The issue number (for an article)')
    page_start: Optional[int] = Field(None, gt=0, title='The first page (for an article)')
    page_end: Optional[int] = Field(None, gt=0, title='The last page (for an article)')
    editors: Optional[str] = Field(None, max_length=255, title='The editor names (for a book)')
    edition: Optional[str] = Field(None, max_length=50, title='The edition (for a book)')
    chapter_title: Optional[str] = Field(None, max_length=255, title='The chapter title (for a book)')
    publication_place: Optional[str] = Field(None, max_length=255, title='The publication place (for a book)')
    advisor: Optional[str] = Field(None, max_length=255, title='The dissertation advisor (for a thesis)')
    doi: Optional[str] = Field(None, max_length=255, title='The DOI')
    isbn: Optional[str] = Field(None, max_length=255, title='The ISBN (for a book)')
    url: Optional[str] = Field(None, max_length=255, title='The publication URL address')
    reviewer_flags: Optional[Dict[str, str]] = Field(None)

    class Config:
        orm_mode = True
        extra = "forbid"

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """Literature.reviewer_flags validator"""
        return value or dict()

    @validator('journal', always=True)
    def check_journal(cls, value, values):
        """Literature.journal validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.article and (value is None or not value):
            raise ValueError(f'The journal argument is missing for a literature type {values["type"]}')
        return value

    @validator('title')
    def check_title(cls, value, values):
        """Literature.title validator"""
        # Check if title is empty
        if not value:
            raise ValueError('The title is missing')
        if '_' in value:
            raise ValueError(f'The title appears to contain underscores. Got: {value}. Please replace underscores with spaces.')

        return value

    @validator('year')
    def check_year(cls, value, values):
        """
        Validate the year input. Ensure it is not in the future and is after a reasonable minimum year (e.g., 1500).
        """
        current_year = datetime.now().year
        if value > current_year:
            raise ValueError(f'The year {value} is in the future. It must be <= {current_year}.')
        if value < 1500:
            raise ValueError('The year must be greater than or equal to 1500.')
        return value

    @validator('publisher', always=True)
    def check_publisher(cls, value, values):
        """Literature.publisher validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.book and (value is None or not value):
            raise ValueError(f'The publisher argument is missing for a literature type {values["type"]}')
        return value

    @validator('volume', always=True)
    def check_volume(cls, value, values):
        """Literature.volume validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.article and (value is None or not value):
            raise ValueError(f'The volume argument is missing for a literature type {values["type"]}')
        return value

    @validator('page_start', always=True)
    def check_page_start(cls, value, values):
        """Literature.page_start validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.article and (value is None or not value):
            raise ValueError(f'The page_start argument is missing for a literature type "{values["type"]}"')
        return value

    @validator('page_end', always=True)
    def check_page_end(cls, value, values):
        """Literature.page_end validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.article and (value is None or not value):
            raise ValueError(f'The page_end argument is missing for a literature type "{values["type"]}"')
        if 'page_start' in values and isinstance(values['page_start'], int) and isinstance(value, int) \
                and value < values['page_start']:
            raise ValueError(f'The starting page cannot be less than the ending page, '
                             f'got page_start={values["page_start"]} and page_end={value}')
        return value

    @validator('editors', always=True)
    def check_editors(cls, value, values):
        """Literature.editors validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.book and (value is None or not value):
            raise ValueError(f'The editors argument is missing for a literature type {values["type"]}')
        return value

    @validator('publication_place', always=True)
    def check_publication_place(cls, value, values):
        """Literature.publication_place validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.book and (value is None or not value):
            raise ValueError(f'The publication_place argument is missing for a literature type {values["type"]}')
        return value

    @validator('advisor', always=True)
    def check_advisor(cls, value, values):
        """Literature.advisor validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.thesis and (value is None or not value):
            raise ValueError(f'The advisor argument is missing for a literature type {values["type"]}')
        return value

    @validator('doi', always=True)
    def check_doi(cls, value, values):
        """Literature.doi validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.article and (value is None or not value):
            raise ValueError(f'The doi argument is missing for a literature type {values["type"]}')
        return value

    @validator('isbn', always=True)
    def check_isbn(cls, value, values):
        """Literature.isbn validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.book and (value is None or not value):
            raise ValueError(f'The isbn argument is missing for a literature type {values["type"]}')
        return value

    @validator('url')
    def validate_url(cls, value, values):
        """Literature.url validator"""
        if 'type' in values and values['type'] == LiteratureTypeEnum.thesis or value is not None:
            if '.' not in value:
                raise ValueError('url invalid (expected a ".")')
            if ' ' in value:
                raise ValueError('url invalid (no spaces allowed)')
        return value


class LiteratureCreate(LiteratureBase):
    """Create a Literature item: Properties to receive on item creation"""
    type: LiteratureTypeEnum = Field(..., title='The literature type, either article, book, or thesis')
    authors: List[AuthorCreate] = Field(None, title='A list of new authors for the literature source')  # New authors
    title: str = Field(..., max_length=255, title='The literature source title')
    year: int = Field(..., ge=1500, le=9999, title='The publication year')
    
    class Config:
        orm_mode = True
        extra = "forbid"

    @root_validator
    def check_authors_and_author_ids(cls, values):
        authors = values.get('authors')
        author_ids = values.get('author_ids')

        if not authors and not author_ids:
            raise ValueError("Either 'authors' or 'author_ids' must be provided.")

        return values

    @validator('authors', always=True)
    def check_authors(cls, value):
        """Literature.authors validator"""
        if value:
            if not isinstance(value, list) or any(not author.first_name or not author.last_name for author in value):
                raise ValueError(f'The authors argument seems incomplete. Got: {value}')
        return value

class LiteratureUpdate(LiteratureBase):
    """Update a Literature item: Properties to receive on item update"""

    authors: Optional[List[AuthorCreate]] = Field(None, title='A list of new authors for the literature source')  # New authors

    class Config:
        orm_mode = True
        extra = "forbid"

class LiteratureRead(LiteratureBase):
    """Properties to return to client"""
    id: int
    authors: List[AuthorReadLiterature]
    author_ids: List[int]=Field(default_factory=list)

    class Config:
        orm_mode = True

    @validator('author_ids', pre=True, always=True)
    def populate_author_ids(cls, v, values):
        authors = values.get('authors')
        if authors:
            return [author.id for author in authors]
        return []


class LiteratureInDBBase(LiteratureBase):
    """Properties shared by models stored in DB"""
    id: int
    title: str
    authors: List[AuthorRead]
    author_ids: List[int] = Field(default_factory=list)
    year: int
    journal: Optional[str] = None
    publisher: Optional[str] = None
    volume: Optional[int] = None
    issue: Optional[int] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    editors: Optional[str] = None
    edition: Optional[str] = None
    chapter_title: Optional[str] = None
    publication_place: Optional[str] = None
    doi: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True

class Literature(LiteratureInDBBase):
    """Properties to return to client"""
    pass


class LiteratureInDB(LiteratureInDBBase):
    """Properties stored in DB"""
    pass
