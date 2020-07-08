"""
Initialize the TCKDB backend app models module for SQLAlchemy models

The SQLAlchemy Object Relational Mapper (ORM) presents a method of associating user-defined Python classes
with database tables, and instances of those classes (objects) with rows in their corresponding tables.
"""

from tckdb.backend.app.models.author import Author
from tckdb.backend.app.models.bot import Bot
from tckdb.backend.app.models.encorr import EnCorr
from tckdb.backend.app.models.ess import ESS
from tckdb.backend.app.models.freq import Freq
from tckdb.backend.app.models.level import Level
from tckdb.backend.app.models.literature import Literature
from tckdb.backend.app.models.LJ import LJ
from tckdb.backend.app.models.species import Species, NonPhysicalSpecies
from tckdb.backend.app.models.trans import Trans
