from typing import List, Optional
from click import Option
from pydantic import BaseModel, Field


class AuthorBase(BaseModel):
    """Schema for an Author object"""
    first_name: str
    last_name: str
    
    class Config:
        orm_mode = True

class AuthorCreate(AuthorBase):
    """Schema for creating an Author"""
    pass

class AuthorUpdate(AuthorBase):
    """Schema for updating an Author"""
    first_name: Optional[str]
    last_name: Optional[str]

    class Config:
        orm_mode = True

class Author(AuthorBase):
    id: int
    
    class Config:
        orm_mode = True
