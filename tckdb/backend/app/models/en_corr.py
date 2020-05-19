"""
TCKDB backend app models en_corr module
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class EnCorr(Base):
    """
    A class for representing a TCKDB EnCorr item

    Attributes:
        id (int): The primary key.
        level_id (int): The level of theory key for the ``level`` table.
        supported_elements (List[str]): Entries are atomic symbols of supported elements.
        energy_unit (str): The energy units, default: 'Hartree'.
        aec (Dict[str, float]): Atomic energy corrections (including SOC). Keys are element symbols, values are energy
                                corrections in the specified ``energy_unit``.
        bac (Dict[str, float]): Bond additivity corrections. Keys are strings representing two elements and the bond
                                between them (e.g., 'C=O'). Values are energy corrections in the specified
                                ``energy_unit``. Allowed bond descriptors are '-', '=', '#', '--', and '&'
                                for single, double, triple, hydrogen, and aromatic bonds, respectively.
        isodesmic_reactions (List[Dict[str, Union[List[Union[int, str]], float]]]): The isodesmic reactions used for the
            energy correction. If specified, 'AEC' and 'BAC' must be ``None``, and vice versa. Entries are dictionaries
            representing reactions. Each reaction dict has 'reactants', 'products', 'stoichiometry', and 'DHrxn298'
            keys. The values of the 'reactants' and 'products' are lists of string species identifier (SMILES or InChI).
            The value of 'stoichiometry' is a list with stoichiometric coefficients of the reactants and products
            (in that order). The value of 'DHrxn298' is the enthalpy change of reaction at the "low level" in the
            specified ``energy_unit``.
        isodesmic_high_level_id (int): The high level of theory used for all other species.
                                       The level of theory key for the ``level`` table.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    level_id = Column(Integer, ForeignKey('level.id'), nullable=False, unique=False)
    supported_elements = Column(ARRAY(item_type=str, as_tuple=False, zero_indexes=True), nullable=False)
    energy_unit = Column(String(255), nullable=False)
    aec = Column(MsgpackExt, nullable=True)
    bac = Column(MsgpackExt, nullable=True)
    isodesmic_reactions = Column(ARRAY(item_type=dict, as_tuple=False, zero_indexes=True), nullable=True)
    isodesmic_high_level_id = Column(Integer, ForeignKey('level.id'), nullable=True, unique=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        str_ += f"level_id={self.level_id}, "
        str_ += f"supported_elements={self.supported_elements}, "
        str_ += f"energy_unit='{self.energy_unit}'"
        str_ += f", aec={self.aec}" if self.aec is not None else ""
        str_ += f", bac={self.bac}" if self.bac is not None else ""
        str_ += f", isodesmic_reactions={self.isodesmic_reactions}" if self.isodesmic_reactions is not None else ""
        str_ += f", isodesmic_high_level_id={self.isodesmic_high_level_id}" \
            if self.isodesmic_high_level_id is not None else ""
        str_ += f")>"
        return str_

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"level_id='{self.level_id}', "
        str_ += f"supported_elements={self.supported_elements}"
        str_ += f")>"
        return str_
