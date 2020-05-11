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
        parameters (Dict[str, Union[Tuple[float, str], float]]): The energy transfer model parameters.
                           Keys are parameter names ('alpha0',  'T0', and 'n' for the 'Single Exponential Down' model)
                           and values are either Tuple[float, str] with the  value and unit,
                           or just a float for a dimensionless parameter.
                           Example:
                               {'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': 0.52}
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    model = Column(String(100), nullable=False)
    parameters = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=False)
    reviewer_flags = Column(MutableDict.as_mutable(JSONEncodedDict), nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"model={self.model}, " \
               f"parameters={self.parameters}" \
               f")>"

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        return f"<{self.__class__.__name__}(model='{self.model}', parameters={self.parameters})>"
