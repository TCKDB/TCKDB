"""
Initialize the TCKDB backend app models module for SQLAlchemy models

The SQLAlchemy Object Relational Mapper (ORM) presents a method of associating user-defined Python classes
with database tables, and instances of those classes (objects) with rows in their corresponding tables.
"""

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
import tckdb.backend.app.models.associations
# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.audit import AuditLog
# trunk-ignore(flake8/F401)
# trunk-ignore(ruff/F401)
from tckdb.backend.app.models.author import Author
# trunk-ignore(flake8/F401)
# trunk-ignore(ruff/F401)
from tckdb.backend.app.models.bot import Bot

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.encorr import EnCorr

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.ess import ESS
# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.freqscale import FreqScale
# trunk-ignore(flake8/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.level import Level

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.literature import Literature

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.LJ import LJ

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.np_species import NonPhysicalSpecies

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.person import Person

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.species import Species

# trunk-ignore(ruff/F401)
# trunk-ignore(flake8/F401)
from tckdb.backend.app.models.trans import Trans
