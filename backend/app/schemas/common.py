"""Backend hybrid module — ``SchemaBase`` is re-exported from
``tckdb_schemas.common``; ORM-read base schemas remain backend-side
because they carry ``from_attributes=True`` for SQLAlchemy mapping and
have no role in the upload wire contract.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from tckdb_schemas.common import SchemaBase  # noqa: F401  (re-exported)


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
