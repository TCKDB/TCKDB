"""Base Pydantic schema(s) used by all wire-contract models.

Only ``SchemaBase`` lives in the standalone package — backend ORM/read
base classes (``ORMBaseSchema``, ``ORMStrictSchema``,
``TimestampedReadSchema``, ``TimestampedCreatedByReadSchema``) remain
backend-side because they exist for ``from_attributes=True`` ORM reads
and have no role in the upload wire contract.
"""

from pydantic import BaseModel, ConfigDict


class SchemaBase(BaseModel):
    """Base Pydantic schema with strict input handling."""

    model_config = ConfigDict(extra="forbid")
