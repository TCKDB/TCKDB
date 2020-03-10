"""
TCKDB backend app schemas author module
"""

from pydantic import BaseModel, constr, validator


class AuthorBase(BaseModel):
    """
    An AuthorBase class (shared properties)
    """
    name: constr(max_length=255)
    email: constr(max_length=255)
    affiliation: constr(max_length=255)

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
        if ' ' in value.split('@')[1]:
            raise ValueError('email invalid (no spaces allowed)')
        return value


class AuthorCreate(AuthorBase):
    """Create an Author: Properties to receive on item creation"""
    name: str
    email: str
    affiliation: str


class AuthorUpdate(AuthorBase):
    """Update an Author: Properties to receive on item update"""
    name: str
    email: str
    affiliation: str


class AuthorInDBBase(AuthorBase):
    """Properties shared by models stored in DB"""
    id: int
    title: str
    owner_id: int

    class Config:
        orm_mode = True


class Author(AuthorInDBBase):
    """Properties to return to client"""
    pass


class AuthorInDB(AuthorInDBBase):
    """Properties properties stored in DB"""
    pass
