from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import SchemaBase, TimestampedReadSchema
from app.schemas.utils import (
    normalize_optional_text,
    normalize_orcid,
    normalize_required_text,
)


class AuthorBase(BaseModel):
    given_name: str | None = None
    family_name: str = Field(min_length=1)
    full_name: str | None = None
    orcid: str | None = Field(default=None, max_length=19)

    @field_validator("family_name")
    @classmethod
    def normalize_family_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, value: str | None) -> str | None:
        return normalize_orcid(value)

    @model_validator(mode="after")
    def normalize_and_derive_full_name(self) -> Self:
        self.given_name = normalize_optional_text(self.given_name)
        self.full_name = normalize_optional_text(self.full_name)

        if self.full_name is None:
            if self.given_name is not None:
                self.full_name = f"{self.given_name} {self.family_name}"
            else:
                self.full_name = self.family_name

        return self


class AuthorCreate(AuthorBase, SchemaBase):
    pass


class AuthorUpdate(SchemaBase):
    given_name: str | None = None
    family_name: str | None = Field(default=None, min_length=1)
    full_name: str | None = None
    orcid: str | None = Field(default=None, max_length=19)

    @field_validator("family_name")
    @classmethod
    def normalize_family_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_required_text(value)

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, value: str | None) -> str | None:
        return normalize_orcid(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.given_name = normalize_optional_text(self.given_name)
        self.full_name = normalize_optional_text(self.full_name)

        if self.full_name is None and self.family_name is not None:
            if self.given_name is not None:
                self.full_name = f"{self.given_name} {self.family_name}"
            else:
                self.full_name = self.family_name

        return self


class AuthorRead(AuthorBase, TimestampedReadSchema):
    pass
