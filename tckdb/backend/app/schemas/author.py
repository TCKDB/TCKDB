import re
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tckdb.backend.app.conversions.converter import generate_check_digit
from tckdb.backend.app.schemas.connection_schema import ConnectionBase


class LiteratureTitle(BaseModel):
    title: str
    model_config = ConfigDict(from_attributes=True)


class AuthorBase(BaseModel):
    """Schema for an Author object"""

    first_name: Optional[str] = Field(None, title="The first name of the author")
    last_name: Optional[str] = Field(None, title="The last name of the author")
    orcid: Optional[str] = Field(
        None,
        title="The ORCID of the author",
        description="More information about ORCID can be found at https://orcid.org",
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("first_name", "last_name")
    @classmethod
    def names_cannot_be_empty(cls, v, field):
        if not v.strip():
            raise ValueError(f"{field.name.replace('_', ' ').title()} cannot be empty.")
        return v

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, v):
        if v is None:
            return v

        orcid_regex = r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$"
        if not isinstance(v, str):
            raise ValueError(f"ORCID must be a string, got {v} of type {type(v)}")

        if not re.match(orcid_regex, v):
            raise ValueError(
                'ORCID iD must be in the format XXXX-XXXX-XXXX-XXXX where X is a digit or the last character can be "X".'
            )

        digits = v.replace("-", "")
        if len(digits) != 16:
            raise ValueError("ORCID iD must contain 16 digits.")

        base_digits = digits[:-1]
        provided_check_digit = digits[-1]

        if not base_digits.isdigit():
            raise ValueError("ORCID iD must contain 15 digits and one check digit.")

        computed_check_digit = generate_check_digit(base_digits)

        if computed_check_digit != provided_check_digit:
            raise ValueError(
                f'The provided ORCID iD "{v}" has an invalid check digit. Expected "{computed_check_digit}".'
            )

        return v


class AuthorCreate(AuthorBase):
    """Schema for creating an Author"""

    first_name: str = Field(..., title="The first name of the author")
    last_name: str = Field(..., title="The last name of the author")
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AuthorCreateBatch(AuthorCreate, ConnectionBase):
    """Schema for creating a batch of Authors"""

    pass


class AuthorUpdate(AuthorBase):
    """Schema for updating an Author"""

    pass


class AuthorReadLiterature(BaseModel):
    """Schema for reading an Author with literature"""

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
