"""
TCKDB backend app models ess module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class ESS(Base):
    """
    A class for representing a TCKDB ESS item

    (ESS = electronic structure software)

    Examples::

        ESS(name='Psi4',
            version='1.1',
            url='http://www.psicode.org/')

        ESS(name='Gaussian',
            version='16',
            revision='C.01',
            url='https://gaussian.com/')

    Attributes:
        id (int): The primary key (not a user input).
        name (str): The software name.
        version (str, optional): The software version.
        revision (str, optional): The software revision.
        url (str): The official software web address.

        species_opt (relationship)
            A One to Many relationship between ESS and Species.
        species_freq (relationship)
            A One to Many relationship between ESS and Species.
        species_scan (relationship)
            A One to Many relationship between ESS and Species.
        species_irc (relationship)
            A One to Many relationship between ESS and Species.
        species_sp (relationship)
            A One to Many relationship between ESS and Species.

        np_species_opt (relationship)
            A One to Many relationship between ESS and NonPhysicalSpecies.
        np_species_freq (relationship)
            A One to Many relationship between ESS and NonPhysicalSpecies.
        np_species_scan (relationship)
            A One to Many relationship between ESS and NonPhysicalSpecies.
        np_species_irc (relationship)
            A One to Many relationship between ESS and NonPhysicalSpecies.
        np_species_sp (relationship)
            A One to Many relationship between ESS and NonPhysicalSpecies.

        reviewer_flags (Dict[str, str]): Backend flags to assist the review process (not a user input).
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100), nullable=False)
    revision = Column(String(100), nullable=False)
    url = Column(String(255), nullable=False)
    reviewer_flags = Column(MsgpackExt, nullable=True)

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
