"""Workflow orchestrator for standalone transition-state uploads.

Coordinates reaction resolution, identity resolution, geometry resolution,
and calculation persistence for a transition state described by scientific
content (reactants/products + TS geometry + calculations).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.common import CalculationType, SubmissionRecordType
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.workflows.reaction_upload import (
    ReactionUploadRequest,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.services.calculation_resolution import collect_converged_opt_energy_warnings
from app.services.energy_correction_resolution import (
    assert_bac_total_has_required_components,
    create_applied_energy_correction,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.reaction_atom_map import (
    ResolvedAtomMapParticipant,
    persist_reaction_atom_map,
)
from app.services.reaction_resolution import (
    validate_transition_state_composition,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import (
    assert_geometry_composition_matches_identity,
    assert_geometry_isotopes_match_identity,
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
#: reactants and products by *identity*, so a participant's geometry is
#: optional and a map can only be written against the ones a depositor chose to
#: send. The remedy therefore names every field the map needs, because
#: 'atom_map' alone would send a depositor to write a map with nothing to count
#: its indices into.
_STANDALONE_TS_ABSENCE_REMEDY = (
    "To record one for this micro reaction, supply 'atom_map' together with "
    "'geometry_key' (naming the saddle-point geometry) and a 'key' and "
    "'geometry' on each reaction participant it maps (ADR 0011); the "
    "computed-reaction upload accepts the same map."
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
    5. Persist structured validation evidence, or report the absence of IRC
       evidence.
    6. Persist the applied energy corrections.
    7. Persist the atom map, or report its absence.

    :param session: Active SQLAlchemy session.
    :param request: Upload-facing transition-state payload.
    :param created_by: Optional application user id for newly created rows.
    :param warnings: Optional sink for non-blocking upload warnings: a TS
        deposited without passing IRC validation evidence, and a reaction
        deposited without an atom map.
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

    # 5. Structured evidence, each record bound to the single additional
    #    calculation of the type it is about (irc for an irc record, freq for
    #    an imaginary_mode record) -- the schema guarantees exactly one is
    #    present when a record of that kind was supplied, and refuses
    #    energy_ordering, which needs calculations this payload does not carry.
    bound_calculation_id_by_kind = {
        "irc": next(
            (c.id for c in additional_calcs if c.type == CalculationType.irc), None
        ),
        "imaginary_mode": next(
            (c.id for c in additional_calcs if c.type == CalculationType.freq), None
        ),
    }
    persist_transition_state_validation_evidence(
        session,
        request.validation_evidence,
        transition_state_entry_id=ts_entry.id,
        reconstruction_calculation_ids=[
            bound_calculation_id_by_kind.get(record.kind)
            for record in request.validation_evidence
        ],
        subject_label=request.label or "transition state",
        field_path="validation_evidence",
        reaction_entry_id=reaction_entry.id,
        transition_state_geometry_id=geometry.id,
        created_by=created_by,
        warnings=warnings,
    )

    # 6. Applied energy corrections targeting this saddle point. The schema
    #    has already refused every key this payload has no namespace for, so
    #    there is no source calculation or conformer to resolve.
    for index, correction in enumerate(request.applied_energy_corrections):
        assert_bac_total_has_required_components(
            session,
            correction,
            field=f"applied_energy_corrections[{index}]",
            target_transition_state_entry_id=ts_entry.id,
        )
        create_applied_energy_correction(
            session,
            correction,
            target_transition_state_entry_id=ts_entry.id,
            source_conformer_observation_id=None,
            source_calculation_id=None,
            created_by=created_by,
            warnings_out=warnings,
        )

    # 7. Atom map (ADR 0011). Participant geometries are stored as plain
    #    geometries and checked against the species they claim to be: a map is
    #    counted into them, so coordinates that are not the participant's own
    #    would make every index in it point at the wrong atom.
    geometry_id_by_key: dict[str, int] = {}
    if request.geometry_key is not None:
        geometry_id_by_key[request.geometry_key] = geometry.id
    participant_keys: dict[tuple[str, int], str] = {}
    for side, members in (("reactant", rxn.reactants), ("product", rxn.products)):
        for position, member in enumerate(members, start=1):
            if member.key is not None:
                participant_keys[(side, position)] = member.key
            if member.geometry is None:
                continue
            payload = member.geometry.to_payload()
            assert_geometry_composition_matches_identity(member.species_entry, payload)
            assert_geometry_isotopes_match_identity(member.species_entry, payload)
            geometry_id_by_key[member.geometry.key] = resolve_geometry_payload(
                session, payload
            ).id
    structure_participants = session.scalars(
        select(ReactionEntryStructureParticipant).where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry.id
        )
    ).all()
    persist_reaction_atom_map(
        session,
        request.atom_map,
        reaction_entry_id=reaction_entry.id,
        transition_state_entry_id=ts_entry.id,
        transition_state_geometry_id=geometry.id,
        participants=[
            ResolvedAtomMapParticipant(
                side=row.role,
                species_key=participant_keys[(row.role.value, row.participant_index)],
                participant_index=row.participant_index,
                structure_participant_id=row.id,
            )
            for row in structure_participants
            # A participant with no key cannot be named by a map, and a request
            # with a map refuses that at the schema. Without a map the list is
            # only used to count what the absence warning reports, which needs
            # none of them.
            if (row.role.value, row.participant_index) in participant_keys
        ],
        geometry_id_by_key=geometry_id_by_key,
        field_path="atom_map",
        absence_remedy=_STANDALONE_TS_ABSENCE_REMEDY,
        subject_label=request.label or "transition state",
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
