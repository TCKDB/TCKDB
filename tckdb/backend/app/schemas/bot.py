"""
TCKDB backend app schemas bot module
"""
from typing import Dict, Optional

from pydantic import BaseModel, Field, validator, HttpUrl


class BotBase(BaseModel):
    """
    A BotBase class (shared properties)
    """

    name: Optional[str] = Field(None, max_length=100, title="The Bot's name")
    version: Optional[str] = Field(None, max_length=100, title="The Bot's version")
    url: Optional[HttpUrl] = Field(None, title="The Bot's official website")
    git_commit: Optional[str] = Field(
        None, min_length=40, max_length=40, title="The latest git commit "
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")

    class Config:
        extra = "forbid"


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""
    name: str = Field(..., max_length=100, title="The Bot's name")
    version: str = Field(..., max_length=100, title="The Bot's version")
    url: HttpUrl = Field(..., title="The Bot's official website")
    git_commit: Optional[str] = Field(
        ..., min_length=40, max_length=40, title="The latest git commit"
    )
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch")
    reviewer_flags: Optional[Dict[str, str]] = Field(None, title="Reviewer flags")

    class Config:
        orm_mode = True
        extra = "forbid"

    @validator("reviewer_flags", always=True)
    def check_reviewer_flags(cls, value):
        """Bot.reviewer_flags validator"""
        return value or dict()

    @validator("git_commit")
    def validate_git_commit(cls, value):
        """Bot.git_commit validator"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotFullUpdate(BotBase):
    """Schema for updating a Bot with all fields"""

    pass


class BotUpdate(BotBase):
    """Schema for updating a Bot with optional fields"""


    class Config:
        extra = "forbid"

    @validator("reviewer_flags", always=False)
    def check_reviewer_flags(cls, value):
        """Ensure reviewer_flags is a dictionary"""
        return value or {}

    @validator("git_commit", always=False)
    def validate_git_commit(cls, value):
        """Validate git_commit is alphanumeric"""
        if value and not value.isalnum():
            raise ValueError("The git commit seems wrong, got {value}")
        return value


class BotOut(BotBase):
    """Properties to return to client"""
    id: int
    
    
    class Config:
        orm_mode = True


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
