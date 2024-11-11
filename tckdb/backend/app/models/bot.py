"""
TCKDB backend app models bot module
"""

from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import AuditMixin, Base
from tckdb.backend.app.models.common import MsgpackExt


class Bot(Base, AuditMixin):
    """
    A class for representing a TCKDB Bot item

    (A bot is a software used to automatically generate data for TCKDB)

    Example::

        Bot(name='ARC',
            version='1.1.0',
            url='https://github.com/ReactionMechanismGenerator/ARC',
            git_hash='7ba4d74c73198c76c70742de8c254e075200a582',
            git_branch='master')

    Attributes:
        id (int)
            The primary key (not a user input)
        name (str)
            The software name
        version (str, optional)
            The software version
        url (str)
            The official software web address
        git_hash (str, optional)
            The git commit hash used for this run
        git_branch (str, optional)
            The git branch used for this run

        species (relationship)
            A One to Many relationship between Bot and Species.
        non_physical_species (relationship)
            A One to Many relationship between Bot and NonPhysicalSpecies.

        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input)
    """

    __tablename__ = "bot"

    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100), nullable=False)
    url = Column(String(255), nullable=False)
    git_hash = Column(String(500), nullable=True)
    git_branch = Column(String(100), nullable=True)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    __table_args__ = (UniqueConstraint("name", "version", name="_bot_name_version_uc"),)

    species = relationship(
        "Species",
        back_populates="bot",
        cascade="save-update",
        passive_deletes=False,
        lazy="select",
    )
    non_physical_species = relationship(
        "NonPhysicalSpecies",
        back_populates="bot",
        cascade="save-update",
        passive_deletes=False,
        lazy="select",
    )

    def __str__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return (
            f"<{self.__class__.__name__}("
            f"name='{self.name}', "
            f"version='{self.version}', "
            f"url='{self.url}'"
            f")>"
        )

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return (
            f"<{self.__class__.__name__}("
            f"id={self.id}, "
            f"name='{self.name}', "
            f"version='{self.version}', "
            f"url='{self.url}', "
            f"git_hash='{self.git_hash}', "
            f"git_branch='{self.git_branch}'"
            f")>"
        )
