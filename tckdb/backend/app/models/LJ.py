"""
TCKDB backend app models Lennard-Jones (LJ) module
"""

from typing import Union

from sqlalchemy import Column, Integer
from sqlalchemy.dialects.postgresql import ARRAY

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class LJ(Base):
    """
    A class for representing a TCKDB LJ (Lennard-Jones coefficients)

    Example::

        LJ(sigma=(4.467, 'angstroms'), epsilon=(387.557, 'K'))

    Attributes:
        id (int)
            The primary key (not a user input)
        sigma (Tuple[float, str])
            The L-J sigma parameter
        epsilon (Tuple[float, str])
            The L-J epsilon parameter
        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    sigma = Column(MsgpackExt, nullable=False)
    epsilon = Column(MsgpackExt, nullable=False)
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
