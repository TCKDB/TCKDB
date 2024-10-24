"""
TCKDB backend app schemas frequencies (freq) module
"""

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FreqBase(BaseModel):
    """
    A FreqBase class (shared properties)
    """

    factor: float = Field(..., gt=0, lt=2, title="The frequency scaling factor")
    level_id: int = Field(
        ..., ge=0, title="The level of theory id from the Level table"
    )
    source: str = Field(
        ...,
        max_length=1600,
        title="The source of method used to derive this frequency scaling factor",
    )
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewer_flags", mode="before")
    def check_reviewer_flags(cls, value):
        """Freq.reviewer_flags validator"""
        return value or dict()


class FreqCreate(FreqBase):
    """Create a Freq item: Properties to receive on item creation"""

    factor: float
    level_id: int
    source: str
    reviewer_flags: Optional[Dict[str, str]] = None


class FreqUpdate(FreqBase):
    """Update a Freq item: Properties to receive on item update"""

    factor: float
    level_id: int
    source: str
    reviewer_flags: Optional[Dict[str, str]] = None


class FreqInDBBase(FreqBase):
    """Properties shared by models stored in DB"""

    id: int
    factor: float
    level_id: int
    source: str
    reviewer_flags: Optional[Dict[str, str]] = None
    model_config = ConfigDict(from_attributes=True)


class Freq(FreqInDBBase):
    """Properties to return to client"""

    pass


class FreqInDB(FreqInDBBase):
    """Properties stored in DB"""

    pass
