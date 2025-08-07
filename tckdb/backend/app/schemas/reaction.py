"""Pydantic schemas for reactions and reaction entries"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReactionBase(BaseModel):
    """Shared properties for reactions"""

    formal_charge: int = Field(..., title="Overall formal charge")
    multiplicity: int = Field(..., title="Spin multiplicity")
    family: Optional[str] = Field(None, title="RMG reaction family tag")
    labels: Optional[List[str]] = Field(None, title="User labels")
    reactant_species_ids: Optional[List[int]] = Field(
        None, title="Reactant species identifiers"
    )
    reactant_vdw_ids: Optional[List[int]] = Field(
        None, title="Reactant VDW identifiers"
    )
    product_species_ids: Optional[List[int]] = Field(
        None, title="Product species identifiers"
    )
    product_vdw_ids: Optional[List[int]] = Field(None, title="Product VDW identifiers")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "labels",
        "reactant_species_ids",
        "reactant_vdw_ids",
        "product_species_ids",
        "product_vdw_ids",
        mode="before",
    )
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


class ReactionCreate(ReactionBase):
    """Create a Reaction item"""

    pass


class ReactionUpdate(ReactionBase):
    """Update a Reaction item"""

    pass


class ReactionInDBBase(ReactionBase):
    """Properties shared by models stored in DB"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class Reaction(ReactionInDBBase):
    """Properties to return to client"""

    pass


class ReactionInDB(ReactionInDBBase):
    """Properties stored in DB"""

    pass


class ReactionEntryBase(BaseModel):
    """Shared properties for reaction entry items"""

    reaction_id: int = Field(..., title="Parent reaction identifier")
    kinetics: Optional[Dict[str, object]] = Field(None, title="Kinetic data")
    model_config = ConfigDict(extra="forbid")


class ReactionEntryCreate(ReactionEntryBase):
    """Create a reaction entry"""

    pass


class ReactionEntryUpdate(ReactionEntryBase):
    """Update a reaction entry"""

    pass


class ReactionEntryInDBBase(ReactionEntryBase):
    """Properties shared by reaction entry models stored in DB"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class ReactionEntry(ReactionEntryInDBBase):
    """Properties to return to client"""

    pass


class ReactionEntryInDB(ReactionEntryInDBBase):
    """Properties stored in DB"""

    pass
