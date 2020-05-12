"""
TCKDB backend app models ess module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class ESS(Base):
    """
    A class for representing a TCKDB ESS item
    (Electronic structure software)

    Attributes:
        id (int): The primary key.
        name (str): The software name.
        version (str): The software version.
        revision (str): The software revision.
        url (str): The official software web address.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100), nullable=False)
    revision = Column(String(100), nullable=False)
    url = Column(String(255), nullable=False)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)

    def __str__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return f"{self.name} {self.version}"

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        str_ += f"name='{self.name}'"
        if self.version is not None:
            str_ += f", version='{self.version}'"
        if self.revision is not None:
            str_ += f", revision='{self.revision}'"
        if self.url is not None:
            str_ += f", url='{self.url}'"
        str_ += f")>"
        return str_
