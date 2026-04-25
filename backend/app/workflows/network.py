from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.network import Network
from app.services.species_resolution import resolve_species_entry
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.network_resolution import persist_network, resolve_network_upload
from app.workflows.reaction import persist_reaction_upload


def persist_network_upload(
    session: Session,
    request: NetworkUploadRequest,
    *,
    created_by: int | None = None,
) -> Network:
    """Persist a complete network upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing network upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Network`` row with backend-resolved links.
    """

    species_entry_ids = [
        (
            resolve_species_entry(
                session, species_link.species_entry, created_by=created_by
            ).id,
            species_link.role,
        )
        for species_link in request.species_links
    ]

    reaction_entry_ids = [
        persist_reaction_upload(
            session,
            ReactionUploadRequest(
                reversible=reaction.reaction.reversible,
                reaction_family=reaction.reaction.reaction_family,
                reaction_family_source_note=(
                    reaction.reaction.reaction_family_source_note
                ),
                reactants=[
                    {
                        "species_entry": participant.species_entry,
                        "note": participant.note,
                    }
                    for participant in reaction.reaction.reactants
                ],
                products=[
                    {
                        "species_entry": participant.species_entry,
                        "note": participant.note,
                    }
                    for participant in reaction.reaction.products
                ],
            ),
            created_by=created_by,
        ).id
        for reaction in request.reactions
    ]

    network_create = resolve_network_upload(
        session,
        request,
        species_entry_ids=species_entry_ids,
        reaction_entry_ids=reaction_entry_ids,
    )
    return persist_network(session, network_create, created_by=created_by)
