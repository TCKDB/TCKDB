"""
TCKDB backend app models author module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class Author(Base):
    """
    A class for representing a TCKDB Author item

    Attributes:
        id (int): The primary key.
        name (str): The Author's full name.
        email (str): The Author's email address.
        affiliation (str): The Author's academic affiliation.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(255), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    affiliation = Column(String(255), nullable=False)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"name='{self.name}', " \
               f"email='{self.email}', " \
               f"affiliation='{self.affiliation}'" \
               f")>"
