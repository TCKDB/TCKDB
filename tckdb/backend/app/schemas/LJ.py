"""
TCKDB backend app schemas Lennard-Jones (LJ) module
"""

from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LJBase(BaseModel):
    """
    A LJBase class (shared properties)
    """

    sigma: Tuple[float, str] = Field(
        ...,
        title="The L-J sigma parameter value-units tuple, e.g., (4.467, 'angstroms')",
    )
    epsilon: Tuple[float, str] = Field(
        ..., title="The L-J epsilon parameter value-units tuple, e.g., (387.557, 'K')"
    )
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewer_flags", mode="before")
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
    model_config = ConfigDict(from_attributes=True)


class LJ(LJInDBBase):
    """Properties to return to client"""

    pass


class LJInDB(LJInDBBase):
    """Properties stored in DB"""

    pass
