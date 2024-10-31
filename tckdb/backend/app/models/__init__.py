"""
Initialize the TCKDB backend app models module for SQLAlchemy models

The SQLAlchemy Object Relational Mapper (ORM) presents a method of associating user-defined Python classes
with database tables, and instances of those classes (objects) with rows in their corresponding tables.
"""

import tckdb.backend.app.models.associations
from tckdb.backend.app.models.audit import AuditLog
from tckdb.backend.app.models.author import Author
from tckdb.backend.app.models.bot import Bot
from tckdb.backend.app.models.encorr import EnCorr
from tckdb.backend.app.models.ess import ESS
from tckdb.backend.app.models.freqscale import FreqScale
from tckdb.backend.app.models.level import Level
from tckdb.backend.app.models.literature import Literature
from tckdb.backend.app.models.LJ import LJ
from tckdb.backend.app.models.np_species import NonPhysicalSpecies
from tckdb.backend.app.models.person import Person
from tckdb.backend.app.models.species import Species
from tckdb.backend.app.models.trans import Trans
