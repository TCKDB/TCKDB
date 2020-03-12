"""
TCKDB backend app schemas literature module
"""

from enum import Enum

from pydantic import BaseModel, conint, constr, validator


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
    type: LiteratureTypeEnum
    authors: constr(max_length=255)
    title: constr(max_length=255)
    year: conint(ge=1600, le=9999)
    journal: constr(max_length=255) = None
    publisher: constr(max_length=255) = None
    volume: conint(gt=0) = None
    issue: conint(gt=0) = None
    page_start: conint(gt=0) = None
    page_end: conint(gt=0) = None
    editors: constr(max_length=255) = None
    edition: constr(max_length=50) = None
    chapter_title: constr(max_length=255) = None
    publication_place: constr(max_length=255) = None
    advisor: constr(max_length=255) = None
    doi: constr(max_length=255) = None
    isbn: constr(max_length=255) = None
    url: constr(max_length=500)

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
    journal: str = None
    publisher: str = None
    volume: int = None
    issue: int = None
    page_start: int = None
    page_end: int = None
    editors: str = None
    edition: str = None
    chapter_title: str = None
    publication_place: str = None
    doi: str = None
    isbn: str = None
    url: str


class LiteratureUpdate(LiteratureBase):
    """Update a Literature item: Properties to receive on item update"""
    authors: str
    title: str
    year: int
    journal: str
    publisher: str
    volume: int
    issue: int
    page_start: int
    page_end: int
    editors: str
    edition: str
    chapter_title: str
    publication_place: str
    doi: str
    isbn: str
    url: str


class LiteratureInDBBase(LiteratureBase):
    """Properties shared by models stored in DB"""
    id: int
    authors: str
    title: str
    year: int
    journal: str
    publisher: str
    volume: int
    issue: int
    page_start: int
    page_end: int
    editors: str
    edition: str
    chapter_title: str
    publication_place: str
    doi: str
    isbn: str
    url: str

    class Config:
        orm_mode = True


class Literature(LiteratureInDBBase):
    """Properties to return to client"""
    pass


class LiteratureInDB(LiteratureInDBBase):
    """Properties properties stored in DB"""
    pass
