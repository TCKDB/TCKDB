"""
TCKDB backend app models author module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Author(Base):
    """
    A class for representing a TCKDB Author item

    Attributes:
        id (int): The primary key.
        name (str): The Author's full name.
        email (str): The Author's email address.
        affiliation (str): The Author's academic affiliation.
        uploaded_species (int): The number of Species entries uploaded.
        uploaded_non_physical_species (int): The number of NonPhysicalSpecies entries uploaded.
        uploaded_reactions (int): The number of Reaction entries uploaded.
        uploaded_networks (int): The number of Network entries uploaded.
        reviewed_species (int): The number of Species entries reviewed.
        reviewed_non_physical_species (int): The number of NonPhysicalSpecies entries reviewed.
        reviewed_reactions (int): The number of Reaction entries reviewed.
        reviewed_networks (int): The number of Network entries reviewed.
        reviewer_flags (dict): Backend flags to assist the review process.
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(255), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    affiliation = Column(String(255), nullable=False)
    uploaded_species = Column(Integer, nullable=True)
    uploaded_non_physical_species = Column(Integer, nullable=True)
    uploaded_reactions = Column(Integer, nullable=True)
    uploaded_networks = Column(Integer, nullable=True)
    reviewed_species = Column(Integer, nullable=True)
    reviewed_non_physical_species = Column(Integer, nullable=True)
    reviewed_reactions = Column(Integer, nullable=True)
    reviewed_networks = Column(Integer, nullable=True)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"id={self.id}, "
        str_ += f"name='{self.name}', "
        str_ += f"email='{self.email}', "
        str_ += f"affiliation='{self.affiliation}'"
        str_ += f", uploaded_species={self.uploaded_species}" if self.uploaded_species is not None else ""
        str_ += f", uploaded_non_physical_species={self.uploaded_non_physical_species}" \
            if self.uploaded_non_physical_species is not None else ""
        str_ += f", uploaded_reactions={self.uploaded_reactions}" if self.uploaded_reactions is not None else ""
        str_ += f", uploaded_networks={self.uploaded_networks}" if self.uploaded_networks is not None else ""
        str_ += f", reviewed_species={self.reviewed_species}" if self.reviewed_species is not None else ""
        str_ += f", reviewed_non_physical_species={self.reviewed_non_physical_species}" \
            if self.reviewed_non_physical_species is not None else ""
        str_ += f", reviewed_reactions={self.reviewed_reactions}" if self.reviewed_reactions is not None else ""
        str_ += f", reviewed_networks={self.reviewed_networks}" if self.reviewed_networks is not None else ""
        str_ += f")>"
        return str_

    def __str__(self) -> str:
        """
        A user-friendly string representation of the object.
        """
        str_ = f"<{self.__class__.__name__}("
        str_ += f"name='{self.name}', "
        str_ += f"email='{self.email}', "
        str_ += f"affiliation='{self.affiliation}', "
        uploads = sum([self.uploaded_species or 0,
                       self.uploaded_non_physical_species or 0,
                       self.uploaded_reactions or 0,
                       self.uploaded_networks or 0])
        reviewes = sum([self.reviewed_species or 0,
                        self.reviewed_non_physical_species or 0,
                        self.reviewed_reactions or 0,
                        self.reviewed_networks or 0])
        str_ += f"uploads={uploads}, "
        str_ += f"reviews={reviewes}"
        str_ += f")>"
        return str_
