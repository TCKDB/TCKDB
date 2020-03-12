"""
TCKDB backend app models trans module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import JSONEncodedDict, MutableDict


class Trans(Base):
    """
    A class for representing a TCKDB Trans item (energy transfer model)

    Attributes:
        id (int): The primary key.
        model (str): The energy transfer model, e.g., 'Single Exponential Down'.
        parameters (dict): The energy transfer model parameters.
                           Keys are parameter names ('alpha0',  'T0', and 'n' for the 'Single Exponential Down' model)
                           and values are tuples of values and units.
                           Example:
                               {'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': (0.52,)}
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    model = Column(String(100), nullable=False)
    parameters = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"model={self.model}, " \
               f"parameters={self.parameters}, " \
               f")>"

    def __str__(self) -> str:
        return f"<Freq(model='{self.model}'," \
               f"parameters={self.parameters}>"
