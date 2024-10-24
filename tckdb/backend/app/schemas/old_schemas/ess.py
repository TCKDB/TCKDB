"""
TCKDB backend app schemas electronic structure software (ess) module
"""

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ESSBase(BaseModel):
    """
    An ESSBase class (shared properties)
    """

    name: str = Field(..., max_length=100, title="The ESS name")
    version: Optional[str] = Field(None, max_length=100, title="The ESS version")
    revision: Optional[str] = Field(None, max_length=100, title="The ESS revision")
    url: str = Field(None, max_length=255, title="The ESS official website")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewer_flags", mode="before")
    def check_reviewer_flags(cls, value):
        """ESS.reviewer_flags validator"""
        return value or dict()

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        """ESS.url validator"""
        if "." not in value:
            raise ValueError('url invalid (expected a ".")')
        if " " in value:
            raise ValueError("url invalid (no spaces allowed)")
        return value


class ESSCreate(ESSBase):
    """Create an ESS item: Properties to receive on item creation"""

    name: str
    version: Optional[str] = None
    revision: Optional[str] = None
    url: str
    reviewer_flags: Optional[Dict[str, str]] = None


class ESSUpdate(ESSBase):
    """Update an ESS item: Properties to receive on item update"""

    name: str
    version: Optional[str] = None
    revision: Optional[str] = None
    url: str
    reviewer_flags: Optional[Dict[str, str]] = None


class ESSInDBBase(ESSBase):
    """Properties shared by models stored in DB"""

    id: int
    name: str
    version: Optional[str] = None
    revision: Optional[str] = None
    url: str
    reviewer_flags: Optional[Dict[str, str]] = None
    model_config = ConfigDict(from_attributes=True)


class ESS(ESSBase):
    """Properties to return to client"""

    pass


class ESSInDB(ESSInDBBase):
    """Properties stored in DB"""

    pass
