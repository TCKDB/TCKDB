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
    calculation_paths.router
                            → /scientific/calculations/{calculation_ref_or_id}/scan
                              /irc and /path-search
    calculations.router     → /scientific/calculations/{calculation_ref_or_id}
    transition_states.ts_router
                            → /scientific/transition-states/search +
                              /scientific/transition-states/{ref_or_id}
    transition_states.tse_router
                            → /scientific/transition-state-entries/{ref_or_id}
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.scientific import (
    calculation_paths,
    calculations,
    conformers,
    geometries,
    kinetics,
    kinetics_search,
    provenance,
    reactions,
    species,
    species_calculations_search,
    thermo,
    thermo_search,
    transition_states,
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
# Specialized full-data path endpoints registered before the detail
# router. Both share the ``/calculations`` prefix; FastAPI routes by
# path structure (``/{handle}/scan`` is a deeper segment than
# ``/{handle}``) so this ordering is for OpenAPI grouping rather than
# correctness.
scientific_router.include_router(calculation_paths.router)
scientific_router.include_router(calculations.router)
scientific_router.include_router(transition_states.ts_router)
scientific_router.include_router(transition_states.tse_router)
scientific_router.include_router(conformers.cg_router)
scientific_router.include_router(conformers.co_router)

__all__ = ["scientific_router"]
