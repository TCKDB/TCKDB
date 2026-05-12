"""Aggregate API router — collects all sub-routers under /api/v1.

The legacy entity-read routers (``thermo``, ``kinetics``, ...) are
wrapped with the :func:`require_auth_for_legacy_reads` dependency
under a configurable setting. The public read surface remains
``/api/v1/scientific/*`` and is *not* gated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_auth_for_legacy_reads
from app.api.routes import (
    admin,
    auth,
    bundles,
    calculations,
    conformers,
    energy_corrections,
    geometries,
    health,
    jobs,
    kinetics,
    levels_of_theory,
    literature,
    lookup,
    networks,
    reactions,
    record_reviews,
    software,
    species,
    statmech,
    submissions,
    thermo,
    transition_states,
    transport,
    uploads,
    workflow_tools,
)
from app.api.routes.scientific import scientific_router

api_router = APIRouter()

# Routers that stay public regardless of the legacy-reads gate.
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(lookup.router, prefix="/lookup", tags=["lookup"])
api_router.include_router(
    scientific_router, prefix="/scientific", tags=["scientific"]
)
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(bundles.router, prefix="/bundles", tags=["bundles"])
api_router.include_router(
    submissions.router, prefix="/submissions", tags=["submissions"]
)
api_router.include_router(
    record_reviews.router, prefix="/record-reviews", tags=["record-reviews"]
)

# Legacy entity-read routers. Wrapped with the auth gate so a hosted
# deployment doesn't surface the pre-Phase-D shape anonymously. Local
# dev keeps the routes open by setting ``LEGACY_READS_REQUIRE_AUTH=false``.
_legacy_dependency = [Depends(require_auth_for_legacy_reads)]
api_router.include_router(
    calculations.router,
    prefix="/calculations",
    tags=["calculations"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    species.router,
    prefix="/species",
    tags=["species"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    species.entries_router,
    prefix="/species-entries",
    tags=["species-entries"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    reactions.router,
    prefix="/reactions",
    tags=["reactions"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    reactions.entries_router,
    prefix="/reaction-entries",
    tags=["reaction-entries"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    kinetics.router,
    prefix="/kinetics",
    tags=["kinetics"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    thermo.router,
    prefix="/thermo",
    tags=["thermo"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    transition_states.router,
    prefix="/transition-states",
    tags=["transition-states"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    geometries.router,
    prefix="/geometries",
    tags=["geometries"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    levels_of_theory.router,
    prefix="/levels-of-theory",
    tags=["levels-of-theory"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    software.router,
    prefix="/software",
    tags=["software"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    software.releases_router,
    prefix="/software-releases",
    tags=["software-releases"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    literature.router,
    prefix="/literature",
    tags=["literature"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    conformers.groups_router,
    prefix="/conformer-groups",
    tags=["conformer-groups"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    conformers.observations_router,
    prefix="/conformer-observations",
    tags=["conformer-observations"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    energy_corrections.schemes_router,
    prefix="/energy-correction-schemes",
    tags=["energy-correction-schemes"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    energy_corrections.scale_factors_router,
    prefix="/frequency-scale-factors",
    tags=["frequency-scale-factors"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    energy_corrections.applied_router,
    prefix="/applied-energy-corrections",
    tags=["applied-energy-corrections"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    workflow_tools.router,
    prefix="/workflow-tools",
    tags=["workflow-tools"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    workflow_tools.releases_router,
    prefix="/workflow-tool-releases",
    tags=["workflow-tool-releases"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    statmech.router,
    prefix="/statmech",
    tags=["statmech"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    transport.router,
    prefix="/transport",
    tags=["transport"],
    dependencies=_legacy_dependency,
)
api_router.include_router(
    networks.router,
    prefix="/networks",
    tags=["networks"],
    dependencies=_legacy_dependency,
)
