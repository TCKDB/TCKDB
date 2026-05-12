"""FastAPI route wrappers for the /api/v1/scientific/* read surface.

Thin handlers that translate HTTP requests into Phase 3 service calls and
back. All business logic (filter, sort, collapse, pagination, evidence,
provenance) lives in ``app/services/scientific_read``.

Sub-routers:
    species.router          → /scientific/species/search
    reactions.router        → /scientific/reactions/search (GET, POST)
    kinetics.router         → /scientific/reaction-entries/{id}/kinetics
    thermo.router           → /scientific/species-entries/{id}/thermo
    provenance.router       → /scientific/reaction-entries/{id}/full
    thermo_search.router    → /scientific/thermo/search (GET, POST)
    kinetics_search.router  → /scientific/kinetics/search (GET, POST)
    species_calculations_search.router
                            → /scientific/species-calculations/search (GET, POST)
    geometries.router       → /scientific/geometries/{geometry_handle}
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.scientific import (
    geometries,
    kinetics,
    kinetics_search,
    provenance,
    reactions,
    species,
    species_calculations_search,
    thermo,
    thermo_search,
)

scientific_router = APIRouter()
scientific_router.include_router(species.router)
scientific_router.include_router(reactions.router)
scientific_router.include_router(kinetics.router)
scientific_router.include_router(thermo.router)
scientific_router.include_router(provenance.router)
scientific_router.include_router(thermo_search.router)
scientific_router.include_router(kinetics_search.router)
scientific_router.include_router(species_calculations_search.router)
scientific_router.include_router(geometries.router)

__all__ = ["scientific_router"]
