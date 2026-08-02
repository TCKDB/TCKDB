"""FastAPI route wrappers for the /api/v1/scientific/* read surface.

Thin handlers that translate HTTP requests into Phase 3 service calls and
back. All business logic (filter, sort, collapse, pagination, evidence,
provenance) lives in ``app/services/scientific_read``.

Sub-routers:
    species.router          → /scientific/species/search
    reactions.router        → /scientific/reactions/search (GET, POST)
    kinetics.router         → /scientific/reaction-entries/{id}/kinetics
    thermo.router           → /scientific/species-entries/{id}/thermo
    species_subresources.router
                            → /scientific/species-entries/{id}/statmech
                              /scientific/species-entries/{id}/transport
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
    releases.router         → /scientific/releases (+ /{handle}/manifest,
                              /selections, /artifacts/{path})
    analytics.router        → /scientific/analytics/{kinetics,thermo,
                              statmech,calculations}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes.scientific import (
    analytics,
    artifacts,
    calculation_paths,
    calculations,
    conformers,
    corrections,
    export,
    geometries,
    kinetics,
    kinetics_search,
    literature,
    meta,
    networks,
    provenance,
    reactions,
    releases,
    species,
    species_calculations_search,
    species_subresources,
    statmech,
    structure,
    thermo,
    thermo_search,
    transition_states,
    transport,
)
from app.api.routes.scientific._profile import resolve_profile_dependency

# The ``?profile=`` dependency is attached to the whole router, not to
# individual operations, so that *every* scientific endpoint declares and
# honours the curated/exploratory contract. A profile that only some
# endpoints support is worse than none: it teaches consumers to trust it.
scientific_router = APIRouter(dependencies=[Depends(resolve_profile_dependency)])
# Register the RDKit-backed structure search router before the
# identity-search router so that /species/structure-search resolves
# before any future generic /species/{handle} catch-all that might be
# added under the species namespace.
scientific_router.include_router(structure.router)
scientific_router.include_router(species.router)
scientific_router.include_router(reactions.router)
scientific_router.include_router(kinetics.router)
scientific_router.include_router(thermo.router)
# Sibling per-entry subresource reads (statmech / transport) sharing the
# ``/species-entries`` prefix with the thermo per-entry endpoint.
scientific_router.include_router(species_subresources.router)
scientific_router.include_router(provenance.router)
scientific_router.include_router(thermo_search.router)
scientific_router.include_router(kinetics_search.router)
scientific_router.include_router(meta.router)
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
scientific_router.include_router(conformers.search_router)
scientific_router.include_router(conformers.cg_router)
scientific_router.include_router(conformers.co_router)
scientific_router.include_router(statmech.router)
scientific_router.include_router(transport.router)
scientific_router.include_router(networks.router)
scientific_router.include_router(networks.solve_router)
scientific_router.include_router(networks.kinetics_router)
scientific_router.include_router(literature.router)
scientific_router.include_router(corrections.fsf_router)
scientific_router.include_router(corrections.ecs_router)
scientific_router.include_router(artifacts.router)
scientific_router.include_router(export.router)
# Quantitative analytics reads. Their ``/analytics`` prefix collides with
# nothing above, but they are registered after the transactional searches
# so the OpenAPI document lists the chemistry-first surface first — that
# is the one a consumer should reach for before dropping to numeric
# filtering over the whole corpus.
scientific_router.include_router(analytics.router)
# Citable dataset releases. Registered last so the ``/releases`` prefix
# cannot shadow an earlier, more specific route.
scientific_router.include_router(releases.router)

__all__ = ["scientific_router"]
