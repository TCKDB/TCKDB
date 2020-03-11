"""
TCKDB backend app models bot module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base


class Bot(Base):
    """
    A class for representing a TCKDB Bot
    (A bot is a software used to automatically generate data for TCKDB)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100))
    url = Column(String(255), nullable=False)

    def __repr__(self):
        return f'<{self.__class__.__name__}(' \
               f'id="{self.id}", ' \
               f'name="{self.name}", ' \
               f'version="{self.version}", ' \
               f'url="{self.url}"' \
               f')>'
