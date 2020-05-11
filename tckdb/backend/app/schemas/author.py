"""
TCKDB backend app schemas author module
"""

from typing import Dict, Optional

from pydantic import BaseModel, conint, constr, validator


class AuthorBase(BaseModel):
    """
    An AuthorBase class (shared properties)
    """
    name: constr(max_length=255)
    email: constr(max_length=255)
    affiliation: constr(max_length=255)
    uploaded_species: Optional[conint(gt=0)] = None
    uploaded_non_physical_species: Optional[conint(gt=0)] = None
    uploaded_reactions: Optional[conint(gt=0)] = None
    uploaded_networks: Optional[conint(gt=0)] = None
    reviewed_species: Optional[conint(gt=0)] = None
    reviewed_non_physical_species: Optional[conint(gt=0)] = None
    reviewed_reactions: Optional[conint(gt=0)] = None
    reviewed_networks: Optional[conint(gt=0)] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    @validator('reviewer_flags', always=True)
    def check_reviewer_flags(cls, value):
        """Author.reviewer_flags validator"""
        return value or dict()

    @validator('name')
    def name_must_contain_space(cls, value):
        """Author.name validator"""
        if ' ' not in value:
            raise ValueError('provide a full name')
        return value.title()

    @validator('email')
    def validate_email(cls, value):
        """Author.email validator"""
        if '@' not in value:
            raise ValueError('email must contain a "@"')
        if value.count('@') > 1:
            raise ValueError('email must contain only one "@"')
        if '.' not in value.split('@')[1]:
            raise ValueError('email invalid (expected a "." after the "@" sign)')
        if ' ' in value:
            raise ValueError('email invalid (no spaces allowed)')
        return value


class AuthorCreate(AuthorBase):
    """Create an Author item: Properties to receive on item creation"""
    name: str
    email: str
    affiliation: str
    uploaded_species: Optional[int] = None
    uploaded_non_physical_species: Optional[int] = None
    uploaded_reactions: Optional[int] = None
    uploaded_networks: Optional[int] = None
    reviewed_species: Optional[int] = None
    reviewed_non_physical_species: Optional[int] = None
    reviewed_reactions: Optional[int] = None
    reviewed_networks: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class AuthorUpdate(AuthorBase):
    """Update an Author item: Properties to receive on item update"""
    name: str
    email: str
    affiliation: str
    uploaded_species: Optional[int] = None
    uploaded_non_physical_species: Optional[int] = None
    uploaded_reactions: Optional[int] = None
    uploaded_networks: Optional[int] = None
    reviewed_species: Optional[int] = None
    reviewed_non_physical_species: Optional[int] = None
    reviewed_reactions: Optional[int] = None
    reviewed_networks: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None


class AuthorInDBBase(AuthorBase):
    """Properties shared by models stored in DB"""
    id: int
    name: str
    email: int
    affiliation: int
    uploaded_species: Optional[int] = None
    uploaded_non_physical_species: Optional[int] = None
    uploaded_reactions: Optional[int] = None
    uploaded_networks: Optional[int] = None
    reviewed_species: Optional[int] = None
    reviewed_non_physical_species: Optional[int] = None
    reviewed_reactions: Optional[int] = None
    reviewed_networks: Optional[int] = None
    reviewer_flags: Optional[Dict[str, str]] = None

    class Config:
        orm_mode = True


class Author(AuthorInDBBase):
    """Properties to return to client"""
    pass


class AuthorInDB(AuthorInDBBase):
    """Properties properties stored in DB"""
    pass
