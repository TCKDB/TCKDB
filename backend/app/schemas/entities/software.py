from datetime import date

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.schemas.common import SchemaBase, TimestampedReadSchema
from app.schemas.utils import normalize_required_text


class SoftwareBase(BaseModel):
    name: str = Field(min_length=1)
    website: HttpUrl | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)


class SoftwareCreate(SoftwareBase, SchemaBase):
    pass


class SoftwareUpdate(SchemaBase):
    name: str | None = Field(default=None, min_length=1)
    website: HttpUrl | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_required_text(value)


class SoftwareRead(SoftwareBase, TimestampedReadSchema):
    id: int


# ---------------------------
# Software Release
# ---------------------------


class SoftwareReleaseBase(BaseModel):
    software_id: int
    version: str | None = None
    revision: str | None = None
    build: str | None = None
    release_date: date | None = None
    notes: str | None = None

    @field_validator("version", "revision", "build")
    @classmethod
    def normalize_version_fields(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()


class SoftwareReleaseCreate(SoftwareReleaseBase, SchemaBase):
    pass


class SoftwareReleaseUpdate(SchemaBase):
    software_id: int | None = None
    version: str | None = None
    revision: str | None = None
    build: str | None = None
    release_date: date | None = None
    notes: str | None = None


class SoftwareReleaseRead(SoftwareReleaseBase, TimestampedReadSchema):
    id: int
