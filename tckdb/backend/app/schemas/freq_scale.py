from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from tckdb.backend.app.schemas.connection_schema import ConnectionBase
from tckdb.backend.app.schemas.level import LevelCreate, LevelRead


class FreqScaleBase(BaseModel):
    """
    A FreqScaleBase class (shared properties)
    """

    factor: Optional[float] = Field(
        None, gt=0, lt=2, title="The frequency scaling factor"
    )
    source: Optional[str] = Field(
        None,
        max_length=1600,
        title="The source of method used to derive this frequency scaling factor",
    )
    model_config = ConfigDict(extra="forbid")


class FreqScaleCreate(FreqScaleBase):
    """Create a FreqScale item: Properties to receive on item creation"""

    factor: float = Field(..., gt=0, lt=2, title="The frequency scaling factor")
    source: str = Field(
        ...,
        max_length=1600,
        title="The source of method used to derive this frequency scaling factor",
    )

    level: LevelCreate = Field(..., title="The level of theory")
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class FreqScaleCreateBatch(FreqScaleBase, ConnectionBase):
    """Create a batch of FreqScale items: Properties to receive on item creation"""

    factor: float = Field(..., gt=0, lt=2, title="The frequency scaling factor")
    source: str = Field(
        ...,
        max_length=1600,
        title="The source of method used to derive this frequency scaling factor",
    )

    level_connection_id: Optional[str] = Field(
        None, title="The level of theory connection id"
    )
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class FreqScaleUpdate(FreqScaleBase):
    """Update a FreqScale item: Properties to receive on item update"""

    pass


class FreqScaleRead(FreqScaleBase):
    """Properties to return to client"""

    id: int
    level: Optional[LevelRead] = Field(None, title="The level of theory")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(from_attributes=True)
