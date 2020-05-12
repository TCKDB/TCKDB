"""
TCKDB backend app models freq module
"""

from sqlalchemy import Column, Float, ForeignKey, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Freq(Base):
    """
    A class for representing a TCKDB Freq item (frequency scaling factor)

    Attributes:
        id (int): The primary key.
        factor (float): The frequency scaling factor.
        level_id (int): The level of theory key for the ``level`` table.
        source (str): The source for the determine frequency scaling factor.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    factor = Column(Float(), nullable=False)
    level_id = Column(Integer, ForeignKey('level.id'), nullable=False, unique=True)
    source = Column(String(255), nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        str_ += f"factor={self.factor}, "
        str_ += f"level_id={self.level_id}, "
        str_ += f"source='{self.source}'"
        str_ += f")>"
        return str_

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"factor={self.factor}, "
        str_ += f"level_id={self.level_id}, "
        str_ += f"source='{self.source}'"
        str_ += f")>"
        return str_
