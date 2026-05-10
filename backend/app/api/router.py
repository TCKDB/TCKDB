"""Aggregate API router — collects all sub-routers under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

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
api_router.include_router(
    calculations.router, prefix="/calculations", tags=["calculations"]
)
api_router.include_router(species.router, prefix="/species", tags=["species"])
api_router.include_router(
    species.entries_router, prefix="/species-entries", tags=["species-entries"]
)
api_router.include_router(reactions.router, prefix="/reactions", tags=["reactions"])
api_router.include_router(
    reactions.entries_router, prefix="/reaction-entries", tags=["reaction-entries"]
)
api_router.include_router(kinetics.router, prefix="/kinetics", tags=["kinetics"])
api_router.include_router(thermo.router, prefix="/thermo", tags=["thermo"])
api_router.include_router(
    transition_states.router,
    prefix="/transition-states",
    tags=["transition-states"],
)
api_router.include_router(
    geometries.router, prefix="/geometries", tags=["geometries"]
)
api_router.include_router(
    levels_of_theory.router, prefix="/levels-of-theory", tags=["levels-of-theory"]
)
api_router.include_router(software.router, prefix="/software", tags=["software"])
api_router.include_router(
    software.releases_router,
    prefix="/software-releases",
    tags=["software-releases"],
)
api_router.include_router(
    literature.router, prefix="/literature", tags=["literature"]
)
api_router.include_router(
    conformers.groups_router,
    prefix="/conformer-groups",
    tags=["conformer-groups"],
)
api_router.include_router(
    conformers.observations_router,
    prefix="/conformer-observations",
    tags=["conformer-observations"],
)
api_router.include_router(
    energy_corrections.schemes_router,
    prefix="/energy-correction-schemes",
    tags=["energy-correction-schemes"],
)
api_router.include_router(
    energy_corrections.scale_factors_router,
    prefix="/frequency-scale-factors",
    tags=["frequency-scale-factors"],
)
api_router.include_router(
    energy_corrections.applied_router,
    prefix="/applied-energy-corrections",
    tags=["applied-energy-corrections"],
)
api_router.include_router(
    workflow_tools.router, prefix="/workflow-tools", tags=["workflow-tools"]
)
api_router.include_router(
    workflow_tools.releases_router,
    prefix="/workflow-tool-releases",
    tags=["workflow-tool-releases"],
)
api_router.include_router(statmech.router, prefix="/statmech", tags=["statmech"])
api_router.include_router(transport.router, prefix="/transport", tags=["transport"])
api_router.include_router(networks.router, prefix="/networks", tags=["networks"])
