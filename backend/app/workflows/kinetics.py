from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    ReactionRole,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import (
    Kinetics,
    KineticsArrheniusEntry,
    KineticsChebyshev,
    KineticsFalloff,
    KineticsInterpretationAssignment,
    KineticsPlog,
    KineticsSourceCalculation,
    KineticsThirdBodyEfficiency,
    KineticsTunnelingApplication,
)
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.species import ConformerAssignmentScheme, ConformerGroup, ConformerSelection
from app.db.models.statmech import Statmech, StatmechSourceCalculation
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.workflows.kinetics_upload import (
    KineticsInterpretationAssignmentUpload,
    KineticsUploadRequest,
)
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.calculation_ownership import (
    W_KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH,
    W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
    assert_owned_by,
    assert_statmech_owned_by,
)
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.kinetics_resolution import persist_kinetics, resolve_kinetics_upload
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species, resolve_species_entry
from app.workflows.reaction import persist_reaction_upload


def _find_sp_for_species(
    session: Session,
    *,
    species_entry_id: int,
    lot_id: int,
) -> Calculation:
    """Find exactly one SP calculation for a species entry at a given LOT.

    :raises ValueError: If zero or multiple matches are found.
    """
    results = session.scalars(
        select(Calculation)
        .where(
            Calculation.species_entry_id == species_entry_id,
            Calculation.type == CalculationType.sp,
            Calculation.lot_id == lot_id,
        )
        .order_by(Calculation.id)
        .limit(2)
    ).all()

    if len(results) == 0:
        raise ValueError(
            "No SP calculation found for the requested species entry "
            "at the declared energy level of theory. "
            "Upload the conformer with the SP as an additional calculation first."
        )
    if len(results) > 1:
        raise ValueError(
            "Multiple SP calculations found for the requested species entry "
            "at the declared energy level of theory. "
            "Cannot auto-resolve: multi-conformer disambiguation not yet supported."
        )
    return results[0]


def _resolve_ts_anchored_reaction_entry(
    session: Session,
    request: KineticsUploadRequest,
    *,
    created_by: int | None,
) -> ReactionEntry | None:
    """Resolve a rate's existing reaction entry from its declared TS evidence.

    A transition-state entry is specific to a structured reaction entry, not
    merely to graph-level stoichiometry.  When an upload names a TS in either
    its interpretation or tunneling evidence, that public reference therefore
    anchors the kinetics record to the already-deposited reaction entry.  The
    submitted reaction content remains mandatory and is checked before a
    ``Kinetics`` row is created.
    """

    ts_refs = {
        assignment.transition_state_entry_ref
        for assignment in request.interpretation_assignments
        if assignment.role == "transition_state"
    }
    if request.tunneling_application is not None:
        ts_refs.add(request.tunneling_application.transition_state_entry_ref)
    ts_refs.discard(None)
    if not ts_refs:
        return None
    if len(ts_refs) != 1:
        raise ValueError(
            "transition-state interpretation and tunneling evidence must reference the same TS entry."
        )

    ts_ref = next(iter(ts_refs))
    reaction_entry_id = session.scalar(
        select(TransitionState.reaction_entry_id)
        .join(TransitionStateEntry, TransitionStateEntry.transition_state_id == TransitionState.id)
        .where(TransitionStateEntry.public_ref == ts_ref)
    )
    if reaction_entry_id is None:
        raise ValueError("transition_state_entry_ref does not reference an existing TS entry.")
    reaction_entry = session.get(ReactionEntry, reaction_entry_id)
    assert reaction_entry is not None

    submitted = [
        (ReactionRole.reactant, index, resolve_species_entry(session, item.species_entry, created_by=created_by).id)
        for index, item in enumerate(request.reaction.reactants, start=1)
    ] + [
        (ReactionRole.product, index, resolve_species_entry(session, item.species_entry, created_by=created_by).id)
        for index, item in enumerate(request.reaction.products, start=1)
    ]
    expected = [
        (participant.role, participant.participant_index, participant.species_entry_id)
        for participant in session.scalars(
            select(ReactionEntryStructureParticipant)
            .where(ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry.id)
            .order_by(ReactionEntryStructureParticipant.role, ReactionEntryStructureParticipant.participant_index)
        )
    ]
    submitted.sort(key=lambda item: (item[0].value, item[1]))
    expected.sort(key=lambda item: (item[0].value, item[1]))
    if request.reaction.reversible != reaction_entry.reaction.reversible or submitted != expected:
        raise ValueError(
            "submitted reaction content and direction do not match the reaction entry owned by the declared TS."
        )
    return reaction_entry


@dataclass(frozen=True)
class _ResolvedInterpretation:
    """One validated interpretation assignment, ready to persist."""

    assignment: KineticsInterpretationAssignmentUpload
    subject_key: str
    statmech: Statmech
    transition_state_entry_id: int | None
    conformer_selection_id: int | None


def _resolve_interpretation_assignments(
    session: Session,
    request: KineticsUploadRequest,
    *,
    created_by: int | None,
) -> list[_ResolvedInterpretation]:
    """Validate and resolve every interpretation subject in one pass.

    Single source of truth for interpretation ownership: it runs before any
    kinetics row is written, and the persistence loop consumes its output
    rather than repeating the same checks.
    """

    resolved: list[_ResolvedInterpretation] = []
    for index, assignment in enumerate(request.interpretation_assignments):
        field = f"interpretation_assignments[{index}]"
        statmech = session.scalar(
            select(Statmech).where(Statmech.public_ref == assignment.statmech_ref)
        )
        if statmech is None:
            raise ValueError("interpretation statmech_ref does not reference an existing statmech record.")
        # This is the point at which a rate coefficient actually *depends* on
        # a partition function, so this is where the reproducibility claim is
        # made and enforced. Deposit-time statmech only warns about a missing
        # source link (a monatomic species has no frequencies, an experimental
        # record has no calculation); a computed statmech that a computed rate
        # is built from has no such excuse.
        if statmech.scientific_origin == ScientificOriginKind.computed and not session.scalar(
            select(StatmechSourceCalculation.statmech_id)
            .where(StatmechSourceCalculation.statmech_id == statmech.id)
            .limit(1)
        ):
            raise ValueError(
                "interpretation statmech_ref names a computed statmech record with no "
                "source calculations, so the rate it supports is not reproducible. "
                "Link the calculations that statmech was derived from first."
            )

        ts_entry_id: int | None = None
        if assignment.transition_state_entry_ref is not None:
            ts_entry_id = session.scalar(
                select(TransitionStateEntry.id).where(
                    TransitionStateEntry.public_ref == assignment.transition_state_entry_ref
                )
            )
            if ts_entry_id is None:
                raise ValueError("interpretation transition_state_entry_ref does not reference an existing TS entry.")

        species_entry_id: int | None = None
        if assignment.role in {"reactant", "product"}:
            participants = (
                request.reaction.reactants
                if assignment.role == "reactant"
                else request.reaction.products
            )
            assert assignment.participant_index is not None  # enforced by schema
            if assignment.participant_index > len(participants):
                raise ValueError("interpretation participant_index is outside the reaction participant list.")
            participant_entry = resolve_species_entry(
                session,
                participants[assignment.participant_index - 1].species_entry,
                created_by=created_by,
            )
            species_entry_id = participant_entry.id
            assert_statmech_owned_by(
                statmech,
                code=W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
                target=f"{assignment.role} {assignment.participant_index}",
                context=f"{field}.statmech_ref",
                species_entry_id=species_entry_id,
            )
            subject_key = f"{assignment.role}:{assignment.participant_index}"
        else:
            # ``ts_entry_id`` cannot be None here: the schema requires
            # ``transition_state_entry_ref`` for this role and the lookup
            # above refuses a ref that names nothing, so the guard always
            # has an owner to compare against.
            assert_statmech_owned_by(
                statmech,
                code=W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
                target="transition state",
                context=f"{field}.statmech_ref",
                transition_state_entry_id=ts_entry_id,
            )
            subject_key = "transition_state"

        selection_id: int | None = None
        if assignment.conformer_selection is not None:
            locator = assignment.conformer_selection
            selection_species_entry = resolve_species_entry(
                session, locator.species_entry, created_by=created_by
            )
            stmt = (
                select(ConformerSelection.id)
                .join(ConformerGroup, ConformerGroup.id == ConformerSelection.conformer_group_id)
                .where(
                    ConformerGroup.species_entry_id == selection_species_entry.id,
                    ConformerSelection.selection_kind == locator.selection_kind,
                )
            )
            if locator.assignment_scheme_ref is None:
                stmt = stmt.where(ConformerSelection.assignment_scheme_id.is_(None))
            else:
                stmt = stmt.join(
                    ConformerAssignmentScheme,
                    ConformerAssignmentScheme.id == ConformerSelection.assignment_scheme_id,
                ).where(ConformerAssignmentScheme.public_ref == locator.assignment_scheme_ref)
            selection_ids = session.scalars(stmt.limit(2)).all()
            if len(selection_ids) != 1:
                raise ValueError("conformer_selection content locator must resolve exactly one selection.")
            selection_id = selection_ids[0]
            # The role test is defence in depth, not a live branch:
            # ``validate_role_shape`` already refuses a conformer_selection
            # on a transition-state assignment, so nothing reaching here
            # can carry one. Kept because the guard below reads
            # ``statmech.species_entry_id``, which a TS statmech has as
            # NULL -- the comparison would be against nothing.
            if assignment.role != "transition_state":
                # The cited row is the *selection*; the subject it must
                # agree with is the statmech this assignment names. So the
                # selection's species entry is the row's owner and the
                # statmech's is the target's -- the reverse of the reading
                # the sentence invites, and the reason this calls the core
                # helper rather than a statmech-shaped wrapper.
                #
                # ``statmech.species_entry_id`` is non-null here: the
                # branch above has just held it equal to the participant's
                # species entry.
                assert_owned_by(
                    subject_noun="conformer selection",
                    row_id=selection_id,
                    row_species_entry_id=selection_species_entry.id,
                    row_transition_state_entry_id=None,
                    code=(
                        W_KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH
                    ),
                    target=f"{assignment.role} {assignment.participant_index}",
                    context=f"{field}.conformer_selection",
                    species_entry_id=statmech.species_entry_id,
                )

        resolved.append(
            _ResolvedInterpretation(
                assignment=assignment,
                subject_key=subject_key,
                statmech=statmech,
                transition_state_entry_id=ts_entry_id,
                conformer_selection_id=selection_id,
            )
        )
    return resolved


def persist_kinetics_upload(
    session: Session,
    request: KineticsUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
    warnings: list[UploadWarning] | None = None,
) -> Kinetics:
    """Persist a complete kinetics upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param created_by: Optional application user id for newly created rows.
    :param warnings: Optional sink for non-blocking upload warnings.
    :returns: Newly created ``Kinetics`` row attached to a backend-resolved reaction entry.
    """
    warning_sink = warnings if warnings is not None else []

    # 1. Resolve reaction
    #    Pass the same review_policy so the reaction_entry created en route is
    #    captured in the same review state as the kinetics row this workflow
    #    is producing.
    reaction_entry = _resolve_ts_anchored_reaction_entry(
        session, request, created_by=created_by
    )
    if reaction_entry is not None and request.reaction.reaction_family is not None:
        # The TS-anchored path reuses an already-deposited reaction entry, so
        # a submitted family cannot silently overwrite the stored one. Say so
        # rather than dropping the field on the floor.
        existing_family = session.scalar(
            select(ReactionFamily.name)
            .join(ChemReaction, ChemReaction.reaction_family_id == ReactionFamily.id)
            .where(ChemReaction.id == reaction_entry.reaction_id)
        )
        canonical = find_canonical_reaction_family(request.reaction.reaction_family)
        submitted_family = canonical or request.reaction.reaction_family
        if existing_family is None:
            warning_sink.append(
                UploadWarning(
                    field="reaction.reaction_family",
                    code="reaction_family_not_applied",
                    message=(
                        f"reaction_family '{submitted_family}' was not applied: this "
                        "rate is anchored to an existing reaction entry, whose "
                        "family is set on the shared reaction identity and is not "
                        "modified by a kinetics upload."
                    ),
                )
            )
        elif existing_family != submitted_family:
            raise ValueError(
                "submitted reaction_family disagrees with the reaction family already "
                "recorded for the reaction entry owned by the declared transition state."
            )
    if reaction_entry is None:
        reaction_entry = persist_reaction_upload(
            session,
            ReactionUploadRequest(
                reversible=request.reaction.reversible,
                reaction_family=request.reaction.reaction_family,
                reaction_family_source_note=request.reaction.reaction_family_source_note,
                reactants=[
                    {
                        "species_entry": participant.species_entry,
                        "note": participant.note,
                    }
                    for participant in request.reaction.reactants
                ],
                products=[
                    {
                        "species_entry": participant.species_entry,
                        "note": participant.note,
                    }
                    for participant in request.reaction.products
                ],
            ),
            created_by=created_by,
            review_policy=review_policy,
        )

    resolved_interpretations = _resolve_interpretation_assignments(
        session, request, created_by=created_by
    )

    # 2. Create kinetics record
    kinetics_create = resolve_kinetics_upload(
        session,
        request,
        reaction_entry_id=reaction_entry.id,
    )
    kinetics = persist_kinetics(session, kinetics_create, created_by=created_by)

    # 2b. Pressure-dependent falloff + third-body efficiencies (DR-0032 B).
    if request.falloff is not None:
        f = request.falloff
        session.add(
            KineticsFalloff(
                kinetics_id=kinetics.id,
                low_a=f.low_a,
                low_a_units=f.low_a_units,
                low_n=f.low_n,
                low_ea_kj_mol=f.low_ea_kj_mol,
                troe_alpha=f.troe_alpha,
                troe_t3=f.troe_t3,
                troe_t1=f.troe_t1,
                troe_t2=f.troe_t2,
                sri_a=f.sri_a,
                sri_b=f.sri_b,
                sri_c=f.sri_c,
                sri_d=f.sri_d,
                sri_e=f.sri_e,
                note=f.note,
            )
        )
    for tb in request.third_body_efficiencies:
        collider = resolve_species(session, tb.collider)
        session.add(
            KineticsThirdBodyEfficiency(
                kinetics_id=kinetics.id,
                collider_species_id=collider.id,
                efficiency=tb.efficiency,
            )
        )
    # 2c. Standalone PLOG / Chebyshev fits (DR-0032 Part C).
    for entry in request.plog_entries:
        session.add(
            KineticsPlog(
                kinetics_id=kinetics.id,
                entry_index=entry.entry_index,
                pressure_bar=entry.pressure_bar,
                a=entry.a,
                a_units=entry.a_units,
                n=entry.n,
                ea_kj_mol=entry.ea_kj_mol,
            )
        )
    # 2d. Sum-of-Arrhenius (Chemkin DUPLICATE) terms (DR-0036).
    for term in request.arrhenius_entries:
        session.add(
            KineticsArrheniusEntry(
                kinetics_id=kinetics.id,
                entry_index=term.entry_index,
                a=term.a,
                a_units=term.a_units,
                n=term.n,
                ea_kj_mol=(
                    convert_ea_to_kj_mol(term.reported_ea, term.reported_ea_units)
                    if term.reported_ea is not None
                    else None
                ),
            )
        )
    if request.chebyshev is not None:
        c = request.chebyshev
        session.add(
            KineticsChebyshev(
                kinetics_id=kinetics.id,
                n_temperature=c.n_temperature,
                n_pressure=c.n_pressure,
                tmin_k=c.tmin_k,
                tmax_k=c.tmax_k,
                pmin_bar=c.pmin_bar,
                pmax_bar=c.pmax_bar,
                coefficients=c.coefficients,
            )
        )
    if (
        request.falloff is not None
        or request.third_body_efficiencies
        or request.plog_entries
        or request.arrhenius_entries
        or request.chebyshev is not None
    ):
        session.flush()

    # 2e. Preserve the exact interpretations used to build a rate rather
    # than merely relying on species-level provenance inferred later.
    # Ownership was already validated in one pass above; this loop only writes.
    for resolved in resolved_interpretations:
        assignment = resolved.assignment
        session.add(KineticsInterpretationAssignment(
            kinetics_id=kinetics.id, subject_key=resolved.subject_key, role=assignment.role,
            statmech_id=resolved.statmech.id,
            conformer_selection_id=resolved.conformer_selection_id,
            transition_state_entry_id=resolved.transition_state_entry_id,
            ensemble_policy=assignment.ensemble_policy,
            standard_state_convention=assignment.standard_state_convention,
            degeneracy_interpretation=assignment.degeneracy_interpretation,
            convention_note=assignment.convention_note,
        ))

    if request.tunneling_application is not None:
        tunneling = request.tunneling_application
        ts_entry_id = session.scalar(
            select(TransitionStateEntry.id).where(
                TransitionStateEntry.public_ref == tunneling.transition_state_entry_ref
            )
        )
        if ts_entry_id is None:
            raise ValueError("tunneling transition_state_entry_ref does not reference an existing TS entry.")
        def resolve_artifact(calculation_ref: str | None, sha256: str | None, label: str) -> int | None:
            if calculation_ref is None:
                return None
            artifact_id = session.scalar(
                select(CalculationArtifact.id)
                .join(Calculation, Calculation.id == CalculationArtifact.calculation_id)
                .where(
                    Calculation.public_ref == calculation_ref,
                    CalculationArtifact.sha256 == sha256,
                )
            )
            if artifact_id is None:
                raise ValueError(f"tunneling {label} artifact locator does not reference an existing artifact.")
            return artifact_id
        result_artifact_id = resolve_artifact(
            tunneling.result_artifact_calculation_ref, tunneling.result_artifact_sha256, "result"
        )
        sct_path_artifact_id = resolve_artifact(
            tunneling.sct_path_integral_artifact_calculation_ref,
            tunneling.sct_path_integral_artifact_sha256,
            "SCT path-integral",
        )
        # A rate's tunneling TS must be one of this reaction's TS concepts.
        ts_reaction_id = session.scalar(
            select(TransitionState.reaction_entry_id)
            .join(TransitionStateEntry, TransitionStateEntry.transition_state_id == TransitionState.id)
            .where(TransitionStateEntry.id == ts_entry_id)
        )
        if ts_reaction_id != reaction_entry.id:
            raise ValueError("tunneling transition_state_entry_ref does not belong to this reaction entry.")
        tunneling_source_calculation_id: int | None = None
        if tunneling.source_calculation_ref is not None:
            tunneling_source_calculation_id = session.scalar(
                select(Calculation.id).where(
                    Calculation.public_ref == tunneling.source_calculation_ref
                )
            )
            if tunneling_source_calculation_id is None:
                raise ValueError(
                    "tunneling source_calculation_ref does not reference an existing calculation."
                )
        session.add(KineticsTunnelingApplication(
            kinetics_id=kinetics.id, model=tunneling.model,
            model_identifier=tunneling.model_identifier,
            transition_state_entry_id=ts_entry_id,
            source_calculation_id=tunneling_source_calculation_id,
            imaginary_frequency_cm1=tunneling.imaginary_frequency_cm1,
            frequency_sign_convention=tunneling.frequency_sign_convention,
            reactant_energy_kj_mol=tunneling.reactant_energy_kj_mol,
            product_energy_kj_mol=tunneling.product_energy_kj_mol,
            forward_barrier_kj_mol=tunneling.forward_barrier_kj_mol,
            reverse_barrier_kj_mol=tunneling.reverse_barrier_kj_mol,
            energy_zero_convention=tunneling.energy_zero_convention,
            energy_correction_convention=tunneling.energy_correction_convention,
            convention_note=tunneling.convention_note,
            result_artifact_id=result_artifact_id,
            sct_path_integral_artifact_id=sct_path_artifact_id,
        ))
    session.flush()

    # 3. Auto-resolve source calculations from energy_level_of_theory
    #    For each reaction participant, find the SP at that LOT and link it.
    if request.energy_level_of_theory is not None:
        lot = resolve_level_of_theory_ref(session, request.energy_level_of_theory)

        # Reactant SPs
        for participant in request.reaction.reactants:
            species_entry = resolve_species_entry(
                session, participant.species_entry, created_by=created_by
            )
            calc = _find_sp_for_species(
                session, species_entry_id=species_entry.id, lot_id=lot.id
            )
            session.add(
                KineticsSourceCalculation(
                    kinetics_id=kinetics.id,
                    calculation_id=calc.id,
                    role=KineticsCalculationRole.reactant_energy,
                )
            )

        # Product SPs
        for participant in request.reaction.products:
            species_entry = resolve_species_entry(
                session, participant.species_entry, created_by=created_by
            )
            calc = _find_sp_for_species(
                session, species_entry_id=species_entry.id, lot_id=lot.id
            )
            session.add(
                KineticsSourceCalculation(
                    kinetics_id=kinetics.id,
                    calculation_id=calc.id,
                    role=KineticsCalculationRole.product_energy,
                )
            )

        session.flush()

    apply_review_policy(
        session,
        targets=[
            RecordRef(SubmissionRecordType.kinetics, kinetics.id),
            RecordRef(SubmissionRecordType.reaction_entry, reaction_entry.id),
        ],
        policy=review_policy,
        created_by=created_by,
    )

    return kinetics
