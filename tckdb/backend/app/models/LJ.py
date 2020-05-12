"""
TCKDB backend app models LJ module
"""

from typing import Union

from sqlalchemy import Column, Integer
from sqlalchemy.dialects.postgresql import ARRAY

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class LJ(Base):
    """
    A class for representing a TCKDB LJ (Lennard-Jones coefficients)

    Attributes:
        id (int): The primary key.
        sigma (tuple): The L-J sigma parameter.
        epsilon (tuple): The L-J epsilon parameter.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    sigma = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=2, zero_indexes=True), nullable=False)
    epsilon = Column(ARRAY(item_type=Union[float, str], as_tuple=True, dimensions=2, zero_indexes=True), nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"sigma={self.sigma}, " \
               f"epsilon={self.epsilon}" \
               f")>"
