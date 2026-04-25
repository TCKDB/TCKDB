from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.common import ReactionRole
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.services.reaction_resolution import (
    compress_species_stoichiometry,
    resolve_chem_reaction,
)
from app.services.species_resolution import resolve_species_entry_reference
from app.schemas.workflows.reaction_upload import (
    ReactionParticipantUpload,
    ReactionUploadRequest,
)


def _resolve_participant_upload(
    session: Session,
    participant: ReactionParticipantUpload,
    *,
    created_by: int | None = None,
):
    """Resolve one workflow participant slot into a stored species entry.

    :param session: Active SQLAlchemy session.
    :param participant: Workflow-facing participant reference.
    :param created_by: Optional application user id for newly created rows.
    :returns: Resolved ``SpeciesEntry`` row for the participant slot.
    :raises ValueError: If the participant reference is missing or invalid.
    """

    return resolve_species_entry_reference(
        session,
        species_entry_id=participant.species_entry_id,
        payload=participant.species_entry,
        created_by=created_by,
    )


def persist_reaction_upload(
    session: Session,
    request: ReactionUploadRequest,
    *,
    created_by: int | None = None,
) -> ReactionEntry:
    """Persist a complete reaction upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing reaction upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``ReactionEntry`` row linked to a resolved graph reaction.
    :raises ValueError: If any participant reference cannot be resolved.
    """

    reactant_entries = [
        _resolve_participant_upload(session, participant, created_by=created_by)
        for participant in request.reactants
    ]
    product_entries = [
        _resolve_participant_upload(session, participant, created_by=created_by)
        for participant in request.products
    ]

    chem_reaction = resolve_chem_reaction(
        session,
        reversible=request.reversible,
        reaction_family=request.reaction_family,
        reaction_family_source_note=request.reaction_family_source_note,
        reactant_stoichiometry=compress_species_stoichiometry(reactant_entries),
        product_stoichiometry=compress_species_stoichiometry(product_entries),
    )

    reaction_entry = ReactionEntry(
        reaction_id=chem_reaction.id,
        created_by=created_by,
    )
    session.add(reaction_entry)
    session.flush()

    for participant_index, participant in enumerate(request.reactants, start=1):
        species_entry = reactant_entries[participant_index - 1]
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=reaction_entry.id,
                species_entry_id=species_entry.id,
                role=ReactionRole.reactant,
                participant_index=participant_index,
                note=participant.note,
                created_by=created_by,
            )
        )

    for participant_index, participant in enumerate(request.products, start=1):
        species_entry = product_entries[participant_index - 1]
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=reaction_entry.id,
                species_entry_id=species_entry.id,
                role=ReactionRole.product,
                participant_index=participant_index,
                note=participant.note,
                created_by=created_by,
            )
        )

    session.flush()
    return reaction_entry
