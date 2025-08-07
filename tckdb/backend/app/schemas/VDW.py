"""Pydantic schemas for VDW wells and entries"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VDWBase(BaseModel):
    """Shared properties for VDW wells"""

    inchi_augmented: str = Field(..., title="Augmented InChI of the VDW well")
    constituents: List[int] = Field(
        ..., title="List of constituent species identifiers"
    )
    charge: int = Field(..., title="Formal charge")
    multiplicity: int = Field(..., title="Multiplicity")
    molecular_formula: Optional[str] = Field(None, title="Molecular formula")
    molecular_weight: Optional[float] = Field(None, title="Molecular weight")
    labels: Optional[List[str]] = Field(None, title="User labels")
    fragment_orientation: Optional[List[Dict[str, object]]] = Field(
        None, title="Fragment orientation information"
    )
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator("labels", "constituents", "fragment_orientation", mode="before")
    @classmethod
    def ensure_list(cls, value):
        """Ensure list fields are lists"""
        if value is None:
            return []
        return list(value)

    @field_validator("reviewer_flags", mode="before")
    @classmethod
    def ensure_dict(cls, value):
        """Ensure dict fields are dictionaries"""
        return value or dict()


class VDWCreate(VDWBase):
    """Create a VDW item"""

    pass


class VDWUpdate(VDWBase):
    """Update a VDW item"""

    pass


class VDWInDBBase(VDWBase):
    """Properties shared by models stored in DB"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class VDW(VDWInDBBase):
    """Properties to return to client"""

    pass


class VDWInDB(VDWInDBBase):
    """Properties stored in DB"""

    pass


class VDWEntryBase(BaseModel):
    """Shared properties for VDW entry items"""

    vdw_id: int = Field(..., title="Parent VDW identifier")
    xyz: Optional[Dict[str, object]] = Field(None, title="XYZ data")
    energy: Optional[float] = Field(None, title="Energy")
    model_config = ConfigDict(extra="forbid")


class VDWEntryCreate(VDWEntryBase):
    """Create a VDW entry"""

    pass


class VDWEntryUpdate(VDWEntryBase):
    """Update a VDW entry"""

    pass


class VDWEntryInDBBase(VDWEntryBase):
    """Properties shared by VDW entry models stored in DB"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class VDWEntry(VDWEntryInDBBase):
    """Properties to return to client"""

    pass


class VDWEntryInDB(VDWEntryInDBBase):
    """Properties stored in DB"""

    pass
