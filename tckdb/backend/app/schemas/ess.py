
from typing import Optional, Dict

from pydantic import BaseModel, Field, HttpUrl

from tckdb.backend.app.schemas.connection_schema import ConnectionBase

class ESSBase(BaseModel):
    name: Optional[str] = Field(None, max_length=100, title='The ESS name')
    version: Optional[str] = Field(None, max_length=100, title='The ESS version')
    revision: Optional[str] = Field(None, max_length=100, title='The ESS revision')
    url: Optional[HttpUrl] = Field(None, title='The ESS official website')

    class Config:
        extra = "forbid"


class ESSCreate(ESSBase):
    name: str
    version: Optional[str] = Field(None, max_length=100, title='The ESS version')
    revision: Optional[str] = Field(None, max_length=100, title='The ESS revision')
    url: HttpUrl = Field(..., title='The ESS official website')

class ESSCreateBatch(ESSCreate, ConnectionBase):
    pass


class ESSUpdate(ESSBase):
    pass


class ESSRead(ESSBase):
    id: int
    reviewer_flags: Optional[Dict[str,str]] = Field(None, title='Reviewer flags')
    class Config:
        orm_mode = True
