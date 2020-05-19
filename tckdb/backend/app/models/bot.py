"""
TCKDB backend app models bot module
"""

from sqlalchemy import Column, Integer, String

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class Bot(Base):
    """
    A class for representing a TCKDB Bot item

    (A bot is a software used to automatically generate data for TCKDB)

    Example::

        Bot(name='ARC',
            version='1.1.0',
            url='https://github.com/ReactionMechanismGenerator/ARC',
            git_commit='7ba4d74c73198c76c70742de8c254e075200a582',
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
        git_commit (str, optional)
            The git commit hash used for this run
        git_branch (str, optional)
            The git branch used for this run
        reviewer_flags (Dict[str, str])
            Backend flags to assist the review process (not a user input)
    """
    id = Column(Integer, primary_key=True, index=True, nullable=False)
    name = Column(String(100), unique=True, nullable=False)
    version = Column(String(100), nullable=False)
    url = Column(String(255), nullable=False)
    git_commit = Column(String(500), nullable=True)
    git_branch = Column(String(100), nullable=True)
    reviewer_flags = Column(MsgpackExt, nullable=True)

    def __str__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return f"<{self.__class__.__name__}(" \
               f"name='{self.name}', " \
               f"version='{self.version}', " \
               f"url='{self.url}'" \
               f")>"

    def __repr__(self) -> str:
        """
        A string representation from which the object can be reconstructed.
        """
        return f"<{self.__class__.__name__}(" \
               f"id={self.id}, " \
               f"name='{self.name}', " \
               f"version='{self.version}', " \
               f"url='{self.url}', " \
               f"git_commit='{self.git_commit}', " \
               f"git_branch='{self.git_branch}'" \
               f")>"
