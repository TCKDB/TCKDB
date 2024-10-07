"""
TCKDB backend app schemas bot module
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field, validator


class BotBase(BaseModel):
    """
    A BotBase class (shared properties)
    """

    name: str = Field(..., max_length=100, title="The Bot's name")
    version: Optional[str] = Field(None, max_length=100, title="The Bot's version")
    url: str = Field(
        ...,
        max_length=255,
        title="The Bot's official website "
        "(documentation rather than source code, where possible)",
    )
    git_commit: Optional[str] = Field(
        None, min_length=40, max_length=40, title="The latest git commit "
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")

    class Config:
        extra = "forbid"

    @validator("reviewer_flags", always=True)
    def check_reviewer_flags(cls, value):
        """Bot.reviewer_flags validator"""
        return value or dict()

    @validator("url")
    def validate_url(cls, value):
        """Bot.url validator"""
        if "." not in value or " " in value:
            raise ValueError('URL invalid (expected a "." and no spaces)')
        return value

    @validator("git_commit")
    def validate_git_commit(cls, value):
        """Bot.git_commit validator"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""

    pass


class BotFullUpdate(BotBase):
    """Schema for updating a Bot with all fields"""

    pass


class BotPartialUpdate(BotBase):
    """Schema for updating a Bot with optional fields"""

    name: Optional[str] = Field(None, max_length=100, title="The Bot's name")
    url: Optional[str] = Field(None, max_length=255, title="The Bot's official website")
    version: Optional[str] = Field(None, max_length=100, title="The Bot's version")
    git_commit: Optional[str] = Field(
        None, min_length=40, max_length=40, title="Latest git commit"
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="Git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")

    class Config:
        extra = "forbid"

    @validator("reviewer_flags", always=True)
    def check_reviewer_flags(cls, value):
        """Ensure reviewer_flags is a dictionary"""
        return value or {}

    @validator("url")
    def validate_url(cls, value):
        """Validate URL format"""
        if value and ("." not in value or " " in value):
            raise ValueError('URL invalid (expected a "." and no spaces)')
        return value

    @validator("git_commit")
    def validate_git_commit(cls, value):
        """Validate git_commit is alphanumeric"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotInDBBase(BotBase):
    """Properties shared by models stored in DB"""

    id: int

    class Config:
        orm_mode = True


class Bot(BotInDBBase):
    """Properties to return to client"""

    pass


class BotInDB(BotInDBBase):
    """Properties stored in DB"""

    pass
