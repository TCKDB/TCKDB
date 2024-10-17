
from datetime import datetime
from enum import Enum

from typing import Optional, List

from pydantic import BaseModel, Field, HttpUrl, validator, root_validator

from tckdb.backend.app.schemas.author import AuthorCreate, AuthorReadLiterature
from tckdb.backend.app.schemas.connection_schema import ConnectionBase

class LiteratureType(str, Enum):
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
    type: Optional[LiteratureType] = Field(None, title='The literature type, either article, book, or thesis')
    title: Optional[str] = Field(None, max_length=255, title='The literature source title')
    year: Optional[int] = Field(None, ge=1500, le=9999, title='The publication year')
    journal: Optional[str] = Field(None, title='The journal name')
    publisher: Optional[str] = Field(None, title='The publisher name')
    volume: Optional[int] = Field(None, title='The volume number')
    issue: Optional[int] = Field(None, title='The issue number')
    page_start: Optional[int] = Field(None, title='The first page')
    page_end: Optional[int] = Field(None, title='The last page')
    editors: Optional[str] = Field(None, title='The editor names for a book')
    edition: Optional[str] = Field(None, title='The edition for a book')
    chapter_title: Optional[str] = Field(None, title='The chapter title for a book')
    publication_place: Optional[str] = Field(None, title='The publication place for a book')
    advisor: Optional[str] = Field(None, title='The dissertation advisor for a thesis')
    doi: Optional[str] = Field(None, title='The DOI')
    isbn: Optional[str] = Field(None, title='The ISBN')
    url: Optional[HttpUrl] = Field(None, title='The publication URL address')
    
    class Config:
        orm_mode = True
        extra = "forbid"

    @root_validator
    def check_required_fields(cls, values):
        lit_type = values.get('type')
        if not lit_type:
            raise ValueError('Literature type is required')
        if lit_type == LiteratureType.thesis:
            if not values.get('advisor'):
                raise ValueError('Advisor name is required for a thesis')
        elif lit_type == LiteratureType.article:
            required_fields = ['journal', 'volume', 'issue', 'page_start', 'page_end']
            for field in required_fields:
                if not values.get(field):
                    raise ValueError(f'{field} is required for an article')
        elif lit_type == LiteratureType.book:
            required_fields = ['publisher', 'editors', 'publication_place']
            for field in required_fields:
                if not values.get(field):
                    raise ValueError(f'{field} is required for a book')

        return values

    @validator('title')
    def check_title(cls, v):
        if not v:
            raise ValueError('Title is required')
        if '_' in v:
            raise ValueError('Title cannot contain underscores')
        return v
    
    @validator('year')
    def check_year(cls, v):
        current_year = datetime.now().year
        if v > current_year:
            raise ValueError(f'The year {v} is in the future. It must be <= {current_year}.')
        if v < 1500:
            raise ValueError('The year must be greater than or equal to 1500.')
        return v

    
    @validator('page_start')
    def check_page_start(cls, v, values):
        if values['type'] == LiteratureType.article and not v:
            raise ValueError('Page start is required for an article')
        return v

    @validator('page_end')
    def check_page_end(cls, v, values):
        if values['type'] == LiteratureType.article and not v:
            raise ValueError('Page end is required for an article')
        # Must be greater than or equal to page_start
        if values['page_start'] and v < values['page_start']:
            raise ValueError('Page end must be greater than or equal to page start.'
                             f'Received page_start={values["page_start"]}, page_end={v}')
        return v
    
    @validator('doi')
    def check_doi(cls, v, values):
        if not v:
            return v
        if not v.startswith('10.'):
            raise ValueError('DOI must start with 10.')
        return v
    
    @validator('isbn')
    def check_isbn(cls, v):
        if not v:
            return v
        if not v.replace('-', '').isdigit():
            raise ValueError('ISBN must contain only digits and hyphens')
        return v


class LiteratureCreate(LiteratureBase):
    """
    A LiteratureCreate class (properties to receive on literature creation)
    """
    type: LiteratureType = Field(..., title='The literature type, either article, book, or thesis')
    title: str = Field(..., max_length=255, title='The literature source title')
    authors: List[AuthorCreate] = Field(None, title='Authors for the literature source')
    year: int = Field(..., ge=1500, le=9999, title='The publication year')
    
    class Config:
        orm_mode = True
        extra = "forbid"

    @validator('authors', always=True)
    def validate_authors(cls, v):
        if not v:
            raise ValueError("Authors are required")
        return v

class LiteratureCreateBatch(LiteratureCreate, ConnectionBase):
    """
    A LiteratureCreateBatch class (properties to receive on literature creation)
    """
    pass

class LiteratureUpdate(LiteratureBase):
    pass

class LiteratureRead(LiteratureBase):
    id: int
    authors: List[AuthorReadLiterature]
