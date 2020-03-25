"""
TCKDB backend app models bot module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class Bot(Base):
    """
    A class for representing a TCKDB Bot item
    (A bot is a software used to automatically generate data for TCKDB)

    Attributes:
        id (int): The primary key.
        name (str): The software name.
        version (str): The software version.
        url (str): The official software web address.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100))
    url = Column(String(255), nullable=False)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"name='{self.name}', " \
               f"version='{self.version}', " \
               f"url='{self.url}'" \
               f")>"
