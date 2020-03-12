"""
TCKDB backend app schemas LJ module
"""

from typing import Tuple

from pydantic import BaseModel


class LJBase(BaseModel):
    """
    A LJBase class (shared properties)
    """
    sigma: Tuple[float, str]
    epsilon: Tuple[float, str]


class LJCreate(LJBase):
    """Create an LJ item: Properties to receive on item creation"""
    name: str
    version: str = None
    url: str


class LJUpdate(LJBase):
    """Update an LJ item: Properties to receive on item update"""
    name: str
    version: str
    url: str


class LJInDBBase(LJBase):
    """Properties shared by models stored in DB"""
    id: int
    name: str
    version: str
    url: int

    class Config:
        orm_mode = True


class LJ(LJInDBBase):
    """Properties to return to client"""
    pass


class LJInDB(LJInDBBase):
    """Properties properties stored in DB"""
    pass
