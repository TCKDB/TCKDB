"""
TCKDB backend app schemas literature module
"""

from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, conint, constr, validator


class LiteratureTypeEnum(str, Enum):
    """
    The supported literature reference types
    """
    article = 'article'
    book = 'book'
    thesis = 'thesis'

    class Config:
        extra = "forbid"


class LiteratureBase(BaseModel):
    """
    A LiteratureBase class (shared properties)
    """
    type: LiteratureTypeEnum
    authors: constr(max_length=255)
    title: constr(max_length=255)
    year: conint(ge=1600, le=9999)
    journal: Optional[constr(max_length=255)] = None
    publisher: Optional[constr(max_length=255)] = None
    volume: Optional[conint(gt=0)] = None
    issue: Optional[conint(gt=0)] = None
    page_start: Optional[conint(gt=0)] = None
    page_end: Optional[conint(gt=0)] = None
    editors: Optional[constr(max_length=255)] = None
    edition: Optional[constr(max_length=50)] = None
    chapter_title: Optional[constr(max_length=255)] = None
    publication_place: Optional[constr(max_length=255)] = None
    advisor: Optional[constr(max_length=255)] = None
    doi: Optional[constr(max_length=255)] = None
    isbn: Optional[constr(max_length=255)] = None
    url: Optional[constr(max_length=500)] = None
    reviewer_flags: Optional[Dict[str, str]] = None

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

    @validator('authors')
    def check_authors(cls, value, values):
        """Literature.authors validator"""
        if ' ' not in value:
            raise ValueError(f'The authors argument seems incomplete. Got: {value}')
        return value

    @validator('title')
    def check_title(cls, value, values):
        """Literature.title validator"""
        if ' ' not in value:
            raise ValueError(f'The title argument seems incomplete. Got: {value}')
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
            raise ValueError(f'The starting page cannot be lesser than the ending page, '
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
    type: str
    authors: str
    title: str
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


class LiteratureUpdate(LiteratureBase):
    """Update a Literature item: Properties to receive on item update"""
    authors: str
    title: str
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


class LiteratureInDBBase(LiteratureBase):
    """Properties shared by models stored in DB"""
    id: int
    authors: str
    title: str
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
