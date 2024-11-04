from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from tckdb.backend.app.schemas.connection_schema import ConnectionBase


class BotBase(BaseModel):
    """
    A BotBase class (shared properties)
    """

    name: Optional[str] = Field(None, max_length=100, title="The Bot's name")
    version: Optional[str] = Field(None, max_length=100, title="The Bot's version")
    url: Optional[HttpUrl] = Field(None, title="The Bot's official website")
    git_hash: Optional[str] = Field(
        None, min_length=40, max_length=40, title="The latest git hash "
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("git_hash")
    @classmethod
    def check_git_hash(cls, v):
        """Check if the git hash is a valid SHA-1 hash"""
        if v is not None and not v.isalnum():
            raise ValueError(f"The git hash seems wrong, got {v}")
        return v

    @field_validator("url")
    @classmethod
    def convert_url_to_str(cls, v):
        """Convert the URL to a string"""
        return str(v) if v is not None else None


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""

    name: str = Field(..., max_length=100, title="The Bot's name")
    version: str = Field(..., max_length=100, title="The Bot's version")
    url: HttpUrl = Field(..., title="The Bot's official website")
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            HttpUrl: lambda v: str(v),  # Ensure HttpUrl is serialized as string
        },
    )


class BotCreateBatch(BotCreate, ConnectionBase):
    """Create a batch of Bot items: Properties to receive on item creation"""

    pass


class BotUpdate(BotBase):
    """Update a Bot item: Properties to receive on item update"""

    pass


class BotRead(BotBase):
    """Properties to return to client"""

    id: int

    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(from_attributes=True)
