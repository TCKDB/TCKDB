"""
TCKDB backend app schemas bot module
"""

from pydantic import BaseModel, constr, validator


class BotBase(BaseModel):
    """
    A BotBase class (shared properties)
    """
    name: constr(max_length=100)
    version: constr(max_length=100) = None
    url: constr(max_length=255)

    @validator('url')
    def validate_url(cls, value):
        """Bot.url validator"""
        if '.' not in value:
            raise ValueError('url invalid (expected a ".")')
        if ' ' in value:
            raise ValueError('url invalid (no spaces allowed)')
        return value


class BotCreate(BotBase):
    """Create a Bot item: Properties to receive on item creation"""
    name: str
    version: str = None
    url: str


class BotUpdate(BotBase):
    """Update a Bot item: Properties to receive on item update"""
    name: str
    version: str
    url: str


class BotInDBBase(BotBase):
    """Properties shared by models stored in DB"""
    id: int
    name: str
    version: str
    url: int

    class Config:
        orm_mode = True


class Bot(BotInDBBase):
    """Properties to return to client"""
    pass


class BotInDB(BotInDBBase):
    """Properties properties stored in DB"""
    pass
