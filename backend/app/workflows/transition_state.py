"""Workflow orchestrator for standalone transition-state uploads.

Coordinates reaction resolution, identity resolution, geometry resolution,
and calculation persistence for a transition state described by scientific
content (reactants/products + TS geometry + calculations).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  — ensure all mappers are loaded
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.workflows.reaction_upload import (
    ReactionUploadRequest,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.transition_state_resolution import (
    create_transition_state_and_entry,
    persist_ts_calculations,
)
from app.workflows.reaction import persist_reaction_upload


def persist_transition_state_upload(
    session: Session,
    request: TransitionStateUploadRequest,
    *,
    created_by: int | None = None,
) -> TransitionStateEntry:
    """Persist a complete transition-state upload workflow.

    Steps:
    1. Resolve the reaction from the embedded content (resolve-or-create).
    2. Create ``TransitionState`` (concept) and ``TransitionStateEntry``.
    3. Resolve the saddle-point geometry.
    4. Persist the primary opt calculation and additional calculations,
       linking output geometries and dependency edges.

    :param session: Active SQLAlchemy session.
    :param request: Upload-facing transition-state payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``TransitionStateEntry`` row.
    """

    # 1. Resolve reaction from embedded content
    rxn = request.reaction
    reaction_entry = persist_reaction_upload(
        session,
        ReactionUploadRequest(
            reversible=rxn.reversible,
            reaction_family=rxn.reaction_family,
            reaction_family_source_note=rxn.reaction_family_source_note,
            reactants=[
                {
                    "species_entry": participant.species_entry,
                    "note": participant.note,
                }
                for participant in rxn.reactants
            ],
            products=[
                {
                    "species_entry": participant.species_entry,
                    "note": participant.note,
                }
                for participant in rxn.products
            ],
        ),
        created_by=created_by,
    )

    # 2. Create TS concept + candidate entry
    _ts, ts_entry = create_transition_state_and_entry(
        session,
        reaction_entry_id=reaction_entry.id,
        charge=request.charge,
        multiplicity=request.multiplicity,
        unmapped_smiles=request.unmapped_smiles,
        label=request.label,
        note=request.note,
        created_by=created_by,
    )

    # 3. Resolve saddle-point geometry
    geometry = resolve_geometry_payload(session, request.geometry)

    # 4. Persist calculations (primary opt + additional)
    persist_ts_calculations(
        session,
        primary_opt_upload=request.primary_opt,
        additional_uploads=request.additional_calculations,
        transition_state_entry_id=ts_entry.id,
        geometry_id=geometry.id,
        created_by=created_by,
    )

    session.flush()
    return ts_entry
