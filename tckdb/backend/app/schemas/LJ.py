"""
TCKDB backend app schemas LJ module
"""

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, validator


class LJBase(BaseModel):
    """
    A LJBase class (shared properties)
    """
    sigma: Tuple[float, str]
    epsilon: Tuple[float, str]
    reviewer_flags: Optional[Dict[str, str]] = None

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """LJ.reviewer_flags validator"""
        return value or dict()


class LJCreate(LJBase):
    """Create an LJ item: Properties to receive on item creation"""
    sigma: Tuple[float, str]
    epsilon: Tuple[float, str]
    reviewer_flags: Optional[Dict[str, str]] = None


class LJUpdate(LJBase):
    """Update an LJ item: Properties to receive on item update"""
    sigma: Tuple[float, str]
    epsilon: Tuple[float, str]
    reviewer_flags: Optional[Dict[str, str]] = None


class LJInDBBase(LJBase):
    """Properties shared by models stored in DB"""
    id: int
    sigma: Tuple[float, str]
    epsilon: Tuple[float, str]
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class LJ(LJInDBBase):
    """Properties to return to client"""
    pass


class LJInDB(LJInDBBase):
    """Properties stored in DB"""
    pass
