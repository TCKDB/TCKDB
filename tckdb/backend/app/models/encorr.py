"""
TCKDB backend app models energy correction (encorr) module
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class EnCorr(Base):
    """
    A class for representing a TCKDB EnCorr item

    **AEC and BAC** Example::

        EnCorr(level_id=1, supported_elements=['H', 'C', 'N', 'O', 'S'], energy_unit='Hartree',
               aec={'H': -0.499459, 'C': -37.786694, 'N': -54.524279,
                    'O': -74.992097, 'S': -397.648733},
               bac={'C-H': -0.46, 'C-C': -0.68, 'C=C': -1.9, 'C#C': -3.13, 'O-H': -0.51,
                    'C-O': -0.23, 'C=O': -0.69, 'O-O': -0.02, 'C-N': -0.67, 'C=N': -1.46,
                    'C#N': -2.79, 'N-O': 0.74, 'N_O': -0.23, 'N=O': -0.51, 'N-H': -0.69,
                    'N-N': -0.47, 'N=N': -1.54, 'N#N': -2.05, 'S-H': 0.87, 'C-S': 0.42,
                    'C=S': 0.51, 'S-S': 0.86, 'O-S': 0.23, 'O=S': -0.53})

    **Isodesmic reactions** Example::

        EnCorr(level_id=1, supported_elements=['H', 'C', 'N', 'O', 'S'], energy_unit='kcal/mol',
               isodesmic_reactions=([{'reactants': ['[CH2]CCCC', '[CH]'],
                                      'products': ['[C]C', 'C[CH]CC'],
                                      'stoichiometry': [1, 1, 1, 1], 'DHrxn298': 17.076},
                                     {'reactants': ['[CH2]CCCC', '[CH3]'],
                                      'products': ['C[CH2]', '[CH2]C(C)C'],
                                      'stoichiometry': [1, 1, 1, 1], 'DHrxn298': 14.507}]),
               isodesmic_high_level_id=2)

    Attributes:
        id (int)
            The primary key (not a user input)
        level_id (int)
            The level of theory key for the :ref:`Level table <level_model>`.

            Note:
                This argument is facilitated by querying the
                :ref:`Level table <level_model>`.
        supported_elements (List[str])
            Entries are atomic symbols of elements supported by this energy correction instance.
        energy_unit (str)
            The energy units, default: 'Hartree'
        aec (Dict[str, float])
            Atomic energy corrections (including SOC). Keys are element symbols, values are energy
            corrections in the specified ``energy_unit``.
        bac (Dict[str, float])
            Bond additivity corrections. Keys are strings representing two elements and the bond between them
            (e.g., `'C=O'`). Values are energy corrections in the specified ``energy_unit``.
            Allowed bond descriptors are ``'-'``, ``'='``, ``'#'``, ``'--'``, and ``'&'`` for single, double, triple,
            hydrogen, and aromatic bonds, respectively.
        isodesmic_reactions (List[Dict[str, Union[List[Union[int, str]], float]]])
            The isodesmic reactions used for the energy correction. If specified, 'AEC' and 'BAC' must be ``None``,
            and vice versa. Entries are dictionaries representing reactions. Each reaction dict has 'reactants',
            'products', 'stoichiometry', and 'DHrxn298' keys. The values of the 'reactants' and 'products' are lists
            of string species identifier (SMILES or InChI). The value of 'stoichiometry' is a list with stoichiometric
            coefficients of the reactants and products (in that order). The value of 'DHrxn298' is the enthalpy change
            of reaction at the "low level" in the specified ``energy_unit``. See example above.
        isodesmic_high_level_id (int)
            The high level of theory used for all other species. A level of theory key for the
            :ref:`Level table <level_model>`. Required if ``isodesmic_reactions`` is specified, ``None`` otherwise.

        species (relationship)
            A One to Many relationship between EnCorr and Species.

        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input).
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    level_id = Column(Integer, ForeignKey('level.id'), nullable=False, unique=False)
    supported_elements = Column(ARRAY(String, as_tuple=False, zero_indexes=True), nullable=False)
    energy_unit = Column(String(255), nullable=False)
    aec = Column(MsgpackExt, nullable=True)
    bac = Column(MsgpackExt, nullable=True)
    isodesmic_reactions = Column(MsgpackExt, nullable=True)
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
