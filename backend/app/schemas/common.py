from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SchemaBase(BaseModel):
    """Base Pydantic schema with strict input handling."""

    model_config = ConfigDict(extra="forbid")


class ORMBaseSchema(BaseModel):
    """Base schema for reading from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class ORMStrictSchema(BaseModel):
    """Base schema for ORM reads plus strict field handling."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class TimestampedReadSchema(ORMBaseSchema):
    """Read schema with common timestamped identity fields."""

    id: int
    created_at: datetime


class TimestampedCreatedByReadSchema(TimestampedReadSchema):
    """Read schema with common timestamped identity fields."""

    created_by: int | None = None
