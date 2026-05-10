"""Service layer for the /api/v1/scientific/* read surface.

Composite scientific read endpoints answer user-facing chemistry questions
(species/reaction lookup, kinetics, thermo, provenance) with built-in trust
and provenance summaries. See docs/specs/read_api_mvp.md for the contracts.

Public service entry points:
    search_species        — see species.py
    search_reactions      — see reactions.py
    get_reaction_kinetics — see kinetics.py
    get_species_thermo    — see thermo.py
    get_reaction_full     — see provenance.py
"""

from __future__ import annotations

from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.provenance import get_reaction_full
from app.services.scientific_read.reactions import search_reactions
from app.services.scientific_read.species import search_species
from app.services.scientific_read.thermo import get_species_thermo

__all__ = [
    "search_species",
    "search_reactions",
    "get_reaction_kinetics",
    "get_species_thermo",
    "get_reaction_full",
]
