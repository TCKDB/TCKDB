"""
TCKDB backend app schemas bot module
"""

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from tckdb.backend.app.schemas.temp_id import TempBase


class BotBase(BaseModel):
    """
    A BotBase class (shared properties)
    """

    name: Optional[str] = Field(None, max_length=100, title="The Bot's name")
    version: Optional[str] = Field(None, max_length=100, title="The Bot's version")
    url: Optional[HttpUrl] = Field(None, title="The Bot's official website")
    git_hash: Optional[str] = Field(
        None, min_length=40, max_length=40, title="The latest git commit "
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(extra="forbid")


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""

    name: str = Field(..., max_length=100, title="The Bot's name")
    version: str = Field(..., max_length=100, title="The Bot's version")
    url: HttpUrl = Field(..., title="The Bot's official website")
    git_hash: Optional[str] = Field(
        ..., min_length=40, max_length=40, title="The latest git commit"
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_validator("reviewer_flags", mode="before")
    def check_reviewer_flags(cls, value):
        """Bot.reviewer_flags validator"""
        return value or dict()

    @field_validator("git_hash")
    @classmethod
    def validate_git_commit(cls, value):
        """Bot.git_hash validator"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotCreateBatch(BotCreate, TempBase):
    """Create a batch of Bot items: Properties to receive on item creation"""

    pass


class BotFullUpdate(BotBase):
    """Schema for updating a Bot with all fields"""

    pass


class BotUpdate(BotBase):
    """Schema for updating a Bot with optional fields"""

    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewer_flags")
    def check_reviewer_flags(cls, value):
        """Ensure reviewer_flags is a dictionary"""
        return value or {}

    @field_validator("git_hash")
    def validate_git_commit(cls, value):
        """Validate git_hash is alphanumeric"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotRead(BotBase):
    """Properties to return to client"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class BotInDBBase(BotBase):
    """Properties shared by models stored in DB"""

    id: int
    model_config = ConfigDict(from_attributes=True)


class Bot(BotInDBBase):
    """Properties to return to client"""

    pass


class BotInDB(BotInDBBase):
    """Properties stored in DB"""

    pass
