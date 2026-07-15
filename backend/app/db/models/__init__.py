"""Eagerly import ORM model modules so mapper configuration sees the full graph."""

# Install the global before_insert listener that auto-populates ``public_ref``
# on every PublicRefMixin row. Done here so any code path that imports the
# models package wires the listener exactly once. Idempotent.
from app.services.public_refs import (
    install_public_ref_listener as _install_public_ref_listener,
)

from . import (
    api_key,
    app_user,
    author,
    calculation,
    energy_correction,
    geometry,
    group_additivity,
    idempotency,
    kinetics,
    level_of_theory,
    literature,
    literature_author,
    machine_review_curator_task,
    molecular_property_observation,
    network,
    network_pdep,
    reaction,
    record_machine_review,
    record_review,
    software,
    species,
    statmech,
    submission,
    thermo,
    transition_state,
    transport,
    upload_job,
    user_session,
    workflow,
)

_install_public_ref_listener()


__all__ = [
    "api_key",
    "app_user",
    "author",
    "calculation",
    "energy_correction",
    "geometry",
    "group_additivity",
    "idempotency",
    "kinetics",
    "level_of_theory",
    "literature",
    "literature_author",
    "machine_review_curator_task",
    "molecular_property_observation",
    "network",
    "network_pdep",
    "reaction",
    "record_machine_review",
    "record_review",
    "software",
    "species",
    "statmech",
    "submission",
    "thermo",
    "transition_state",
    "transport",
    "upload_job",
    "user_session",
    "workflow",
]
