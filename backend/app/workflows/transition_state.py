"""Workflow orchestrator for standalone transition-state uploads.

Coordinates reaction resolution, identity resolution, geometry resolution,
and calculation persistence for a transition state described by scientific
content (reactants/products + TS geometry + calculations).
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.common import CalculationType, SubmissionRecordType
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.workflows.reaction_upload import (
    ReactionUploadRequest,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.services.calculation_resolution import collect_converged_opt_energy_warnings
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.reaction_atom_map import persist_reaction_atom_map
from app.services.reaction_resolution import (
    validate_transition_state_composition,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.transition_state_resolution import (
    create_transition_state_and_entry,
    persist_ts_calculations,
)
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from app.workflows.reaction import persist_reaction_upload

#: Closing sentence of the atom-map absence warning on this path.
#:
#: A map indexes into a geometry per participant (ADR 0011: geometry-relative,
#: with the geometries named explicitly), and this payload describes its
#: reactants and products by *identity* alone -- a ``SpeciesEntryIdentityPayload``
#: carries no coordinates. So there is nothing here for a participant leg to be
#: written against, and the gap cannot be closed on this path, only reported.
#: The wording follows ``network_pdep._PDEP_ABSENCE_REMEDY`` for the same
#: reason it exists there: a remedy naming a field this schema does not have
#: would send a depositor looking for somewhere to put the map that does not
#: exist.
_STANDALONE_TS_ABSENCE_REMEDY = (
    "The standalone transition-state upload describes its reactants and "
    "products by identity and carries no geometry for them, so it cannot "
    "carry a map: to record one for this micro reaction, deposit it through "
    "the computed-reaction upload, which accepts 'atom_map' (ADR 0011)."
)


def persist_transition_state_upload(
    session: Session,
    request: TransitionStateUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
    warnings: list[UploadWarning] | None = None,
) -> TransitionStateEntry:
    """Persist a complete transition-state upload workflow.

    Steps:
    1. Resolve the reaction from the embedded content (resolve-or-create).
    2. Create ``TransitionState`` (concept) and ``TransitionStateEntry``.
    3. Resolve the saddle-point geometry.
    4. Persist the primary opt calculation and additional calculations,
       linking output geometries and dependency edges.
    5. Persist structured IRC validation evidence, or report its absence.
    6. Report the absence of an atom map, which this path cannot carry.

    :param session: Active SQLAlchemy session.
    :param request: Upload-facing transition-state payload.
    :param created_by: Optional application user id for newly created rows.
    :param warnings: Optional sink for non-blocking upload warnings: a TS
        deposited without passing IRC validation evidence, and the atom map
        this path can report the absence of but cannot carry.
    :returns: Newly created ``TransitionStateEntry`` row.
    """

    # 1. Resolve reaction from embedded content. Thread the same review
    #    policy so the reaction_entry created en route lands in the same
    #    state as the TS records this workflow is about to write.
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
        review_policy=review_policy,
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

    # The saddle point must be made of this reaction's atoms, at this
    # reaction's charge (ADR 0008: definitional, therefore blocking).
    validate_transition_state_composition(
        session,
        reaction_entry_id=reaction_entry.id,
        transition_state_charge=request.charge,
        transition_state_smiles=request.unmapped_smiles,
        transition_state_geometry_id=geometry.id,
        subject_label=request.label or "transition state",
    )

    # 4. Persist calculations (primary opt + additional)
    primary_calc, additional_calcs = persist_ts_calculations(
        session,
        primary_opt_upload=request.primary_opt,
        additional_uploads=request.additional_calculations,
        transition_state_entry_id=ts_entry.id,
        geometry_id=geometry.id,
        created_by=created_by,
    )

    session.flush()

    # 5. Structured IRC evidence, bound to the single irc calculation the
    #    schema guarantees is present when evidence was supplied.
    irc_calculation_ids = [
        calc.id for calc in additional_calcs if calc.type == CalculationType.irc
    ]
    persist_transition_state_validation_evidence(
        session,
        request.validation_evidence,
        transition_state_entry_id=ts_entry.id,
        reconstruction_calculation_ids=[
            irc_calculation_ids[0] if irc_calculation_ids else None
            for _ in request.validation_evidence
        ],
        subject_label=request.label or "transition state",
        field_path="validation_evidence",
        reaction_entry_id=reaction_entry.id,
        transition_state_geometry_id=geometry.id,
        created_by=created_by,
        warnings=warnings,
    )

    # 6. Atom map (ADR 0011). This path cannot carry one -- see
    #    ``_STANDALONE_TS_ABSENCE_REMEDY`` -- so the call passes ``None``
    #    unconditionally and exists to report the gap, exactly as the
    #    pressure-dependent network bundle does. A saddle point deposited here
    #    is as unmapped as one deposited anywhere else, and the ADR requires
    #    the absence be loud enough that a depositor who *has* the mapping
    #    notices they are being asked for it; reporting it on two of the three
    #    paths that can carry a transition state would make the warning a
    #    property of the route rather than of the record.
    persist_reaction_atom_map(
        session,
        None,
        reaction_entry_id=reaction_entry.id,
        transition_state_entry_id=ts_entry.id,
        transition_state_geometry_id=geometry.id,
        participants=(),
        geometry_id_by_key={},
        # The map belongs to the micro reaction (ADR 0011), and ``reaction``
        # is the field on this payload that describes it. Naming a real field
        # matters for a client that highlights ``field``; there is no
        # ``atom_map`` here to point at.
        field_path="reaction",
        absence_remedy=_STANDALONE_TS_ABSENCE_REMEDY,
        created_by=created_by,
        warnings=warnings,
    )
    session.flush()

    targets: list[RecordRef] = [
        RecordRef(SubmissionRecordType.transition_state_entry, ts_entry.id),
        RecordRef(
            SubmissionRecordType.transition_state, ts_entry.transition_state_id
        ),
        RecordRef(SubmissionRecordType.calculation, primary_calc.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id) for c in additional_calcs
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    # Evaluated last, once every calculation this request touched (primary
    # opt + additional) is flushed, so an sp deposited later in
    # ``additional_calculations`` already counts (#292).
    if warnings is not None:
        warnings.extend(
            collect_converged_opt_energy_warnings(
                session,
                [primary_calc.id, *(c.id for c in additional_calcs)],
            )
        )

    return ts_entry
