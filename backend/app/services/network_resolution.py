from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.network import Network, NetworkReaction, NetworkSpecies
from app.schemas.entities.network import NetworkCreate
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


def resolve_network_upload(
    session: Session,
    request: NetworkUploadRequest,
    *,
    species_entry_ids: list[tuple[int, object]],
    reaction_entry_ids: list[int],
) -> NetworkCreate:
    """Resolve workflow-facing network upload data into an internal create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing network upload payload.
    :param species_entry_ids: Resolved species-entry ids paired with their network roles.
    :param reaction_entry_ids: Resolved reaction-entry ids for the network.
    :returns: Internal ``NetworkCreate`` payload with resolved foreign-key ids.
    """

    literature = (
        resolve_or_create_literature(session, request.literature)
        if request.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, request.software_release)
        if request.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session,
        request.workflow_tool_release,
    )

    return NetworkCreate(
        name=request.name,
        description=request.description,
        literature_id=literature.id if literature is not None else None,
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        species_links=[
            {"species_entry_id": species_entry_id, "role": role}
            for species_entry_id, role in species_entry_ids
        ],
        reactions=[
            {"reaction_entry_id": reaction_entry_id}
            for reaction_entry_id in reaction_entry_ids
        ],
    )


def persist_network(
    session: Session,
    network_create: NetworkCreate,
    *,
    created_by: int | None = None,
) -> Network:
    """Persist a resolved network create payload.

    :param session: Active SQLAlchemy session.
    :param network_create: Internal resolved network payload.
    :param created_by: Optional application user id for the created row.
    :returns: Newly created ``Network`` row.
    """

    network = Network(
        name=network_create.name,
        description=network_create.description,
        literature_id=network_create.literature_id,
        software_release_id=network_create.software_release_id,
        workflow_tool_release_id=network_create.workflow_tool_release_id,
        created_by=created_by,
    )
    session.add(network)
    session.flush()

    for species_link in network_create.species_links:
        session.add(
            NetworkSpecies(
                network_id=network.id,
                species_entry_id=species_link.species_entry_id,
                role=species_link.role,
            )
        )

    for reaction in network_create.reactions:
        session.add(
            NetworkReaction(
                network_id=network.id,
                reaction_entry_id=reaction.reaction_entry_id,
            )
        )

    session.flush()
    return network
