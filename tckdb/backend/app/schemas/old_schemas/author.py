# tckdb/backend/app/schemas/author.py

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from tckdb.backend.app.schemas.temp_id import TempBase


class LiteratureTitle(BaseModel):
    title: str
    model_config = ConfigDict(from_attributes=True)


class AuthorBase(BaseModel):
    """Schema for an Author object"""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuthorCreate(AuthorBase):
    """Schema for creating an Author"""

    first_name: str
    last_name: str
    model_config = ConfigDict(from_attributes=True)

    @field_validator("first_name", "last_name")
    def names_cannot_be_empty(cls, v, field):
        if not v.strip():
            raise ValueError(f"{field.name.replace('_', ' ').title()} cannot be empty.")
        return v


class AuthorCreateBatch(TempBase, AuthorCreate):
    """Schema for creating a batch of Authors"""

    pass


class AuthorUpdate(AuthorBase):
    """Schema for updating an Author"""

    pass


class AuthorReadLiterature(BaseModel):
    """Schema for reading an Author with literature"""

    id: int
    first_name: str
    last_name: str
    model_config = ConfigDict(from_attributes=True)


class AuthorRead(BaseModel):
    """Schema for reading an Author"""

    id: int
    first_name: str
    last_name: str
    literatures: List[LiteratureTitle]
    model_config = ConfigDict(from_attributes=True)
