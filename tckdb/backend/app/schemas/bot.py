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
    url: str = Field(..., max_length=255, title="The Bot's official website "
                                                "(documentation rather than source code, where possible)")
    git_commit: Optional[str] = Field(None, min_length=40, max_length=40,
                                      title="The latest git commit (useful when working on the "
                                            "master branch rather than a stable release)")
    git_branch: Optional[str] = Field(None, max_length=100, title="The git branch used if not 'master'")
    reviewer_flags: Optional[Dict[str, str]] = Field(None)

    class Config:
        extra = "forbid"

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """Bot.reviewer_flags validator"""
        return value or dict()

    @validator('url')
    def validate_url(cls, value):
        """Bot.url validator"""
        if '.' not in value:
            raise ValueError('url invalid (expected a ".")')
        if ' ' in value:
            raise ValueError('url invalid (no spaces allowed)')
        return value

    @validator('git_commit')
    def validate_git_commit(cls, value):
        """Bot.git_commit validator"""
        if not value.isalnum():  # is alpha-numerical?
            raise ValueError(f'The git commit seems wrong, got {value}')
        return value


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""
    name: str
    version: Optional[str] = None
    url: str
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class BotUpdate(BotBase):
    """Update a Bot item: Properties to receive on item update"""
    name: str
    version: Optional[str] = None
    url: str
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class BotInDBBase(BotBase):
    """Properties shared by models stored in DB"""
    id: int
    name: str
    version: Optional[str] = None
    url: int
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class Bot(BotInDBBase):
    """Properties to return to client"""
    pass


class BotInDB(BotInDBBase):
    """Properties stored in DB"""
    pass
