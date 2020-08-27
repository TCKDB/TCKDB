"""
TCKDB backend app models energy transfer (trans) module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Trans(Base):
    """
    A class for representing a TCKDB Trans item (energy transfer model)

    Example::

        Trans(model='Single Exponential Down',
              parameters={'alpha0': (175, 'cm^-1'), 'T0': (300, 'K'), 'n': 0.52})

    Attributes:
        id (int)
            The primary key (not a user input)
        model (str)
            The energy transfer model, currently only ``'Single Exponential Down'`` is supported
        parameters (Dict[str, Union[Tuple[float, str], float]])
            The energy transfer model parameters. Keys are parameter names
            (e.g., ``'alpha0'``, ``'T0'``, and ``'n'`` for the common 'Single Exponential Down' model)
            and values are either Tuple[float, str] with the  value and unit,
            or just a float for a dimensionless parameter (such as ``'n'`` in the 'Single Exponential Down' model).
        reviewer_flags (Dict[str, str]): Backend flags to assist the review process (not a user input)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    model = Column(String(100), nullable=False)
    parameters = Column(MsgpackExt, nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

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
