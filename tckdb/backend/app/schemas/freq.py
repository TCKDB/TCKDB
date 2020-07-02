"""
TCKDB backend app schemas freq module
"""

from typing import Dict, Optional

from pydantic import BaseModel, confloat, conint, constr, validator


class FreqBase(BaseModel):
    """
    A FreqBase class (shared properties)
    """
    factor: confloat(gt=0, lt=2)
    level_id: conint(gt=0)
    source: constr(max_length=1600)
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        extra = "forbid"

    @validator('reviewer_flags', always=True)
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

    class Config:
        orm_mode = True


class Freq(FreqInDBBase):
    """Properties to return to client"""
    pass


class FreqInDB(FreqInDBBase):
    """Properties stored in DB"""
    pass
