from __future__ import annotations

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.db.models.common import ReactionRole, SubmissionRecordType
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.schemas.workflows.reaction_upload import (
    ReactionParticipantUpload,
    ReactionUploadRequest,
)
from app.services.reaction_resolution import (
    compress_species_stoichiometry,
    inherit_reversible,
    resolve_chem_reaction,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry, resolve_species_entry_reference

CODE_REACTION_REVERSIBLE_REQUIRED = "reaction_reversible_required"


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


def reversible_or_inherited(
    session: Session, reaction, *, created_by: int | None = None, field: str = "reaction.reversible"
) -> bool:
    """The ``reversible`` value of an embedded reaction block: as stated, else inherited, else refused.

    Whether a reaction is reversible is part of its graph identity, but a rate (or a network)
    need not say. Unstated is unknown and is never guessed: the block joins the single stored
    reaction with its participants. With none stored, or with both ``reversible`` twins stored,
    there is nothing to inherit, so the deposit is refused (``reaction_reversible_required``)
    and the producer states it. Every route whose reaction block may omit ``reversible``
    (standalone kinetics, networks) resolves it here.

    :param reaction: Anything with ``reversible``, ``reactants`` and ``products`` (participants
        carrying a ``species_entry`` identity payload).
    :param field: The path of the field in the request, named in the refusal.
    """
    if reaction.reversible is not None:
        return reaction.reversible
    reactants = compress_species_stoichiometry(
        [resolve_species_entry(session, p.species_entry, created_by=created_by) for p in reaction.reactants]
    )
    products = compress_species_stoichiometry(
        [resolve_species_entry(session, p.species_entry, created_by=created_by) for p in reaction.products]
    )
    value = inherit_reversible(session, reactant_stoichiometry=reactants, product_stoichiometry=products)
    if value is None:
        raise CodedValueError(
            CODE_REACTION_REVERSIBLE_REQUIRED,
            "reaction.reversible was not stated, and there is no single stored reaction with these "
            "participants to take it from (none is stored, or both a reversible and an irreversible one "
            "are). State reversible: true or false.",
            context={"field": field},
            message_prefix=False,
        )
    return value


def persist_reaction_upload(
    session: Session,
    request: ReactionUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
    warnings: list[UploadWarning] | None = None,
) -> ReactionEntry:
    """Persist a complete reaction upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing reaction upload payload.
    :param created_by: Optional application user id for newly created rows.
    :param warnings: Optional sink for non-blocking warnings (a reaction stored under both
        ``reversible`` values reports ``reaction_reversible_twin``).
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
        warnings_out=warnings,
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

    targets: list[RecordRef] = [
        RecordRef(SubmissionRecordType.reaction_entry, reaction_entry.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.species_entry, e.id)
        for e in (*reactant_entries, *product_entries)
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    return reaction_entry
