
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from tckdb.backend.app.schemas.connection_schema import ConnectionBase


class ESSBase(BaseModel):
    name: Optional[str] = Field(None, max_length=100, title="The ESS name")
    version: Optional[str] = Field(None, max_length=100, title="The ESS version")
    revision: Optional[str] = Field(None, max_length=100, title="The ESS revision")
    url: Optional[HttpUrl] = Field(None, title="The ESS official website")
    model_config = ConfigDict(extra="forbid",
                              from_attributes=True)
    
    @field_validator("url")
    def convert_url_to_str(cls, v):
        """Convert the URL to a string"""
        return str(v) if v is not None else None
    


class ESSCreate(ESSBase):
    name: str
    version: Optional[str] = Field(None, max_length=100, title="The ESS version")
    revision: Optional[str] = Field(None, max_length=100, title="The ESS revision")
    url: HttpUrl = Field(..., title="The ESS official website")


class ESSCreateBatch(ESSCreate, ConnectionBase):
    pass


class ESSUpdate(ESSBase):
    pass


class ESSRead(ESSBase):
    id: int
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(from_attributes=True)
