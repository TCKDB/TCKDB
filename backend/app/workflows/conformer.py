from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.chemistry.geometry import parse_xyz
from app.db.models.common import CalculationType, SubmissionRecordType
from app.db.models.species import ConformerObservation
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.services.calculation_resolution import (
    attach_calculation_input_geometries,
    attach_calculation_output_geometries,
    persist_additional_calculations,
    resolve_and_persist_calculation_with_results,
)
from app.services.conformer_resolution import resolve_conformer_group
from app.services.energy_correction_resolution import create_applied_energy_correction
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry
from app.services.statmech_resolution import resolve_or_create_statmech
from app.services.transport_resolution import resolve_and_create_transport


@dataclass(frozen=True)
class ConformerUploadCalculationRef:
    """Internal handle to a calculation created by the conformer workflow.

    The route maps each ref onto the API's ``CalculationUploadRef``
    Pydantic model. ``request_index`` is left ``None`` for the primary
    calculation and populated with the original
    ``additional_calculations[]`` index for additional refs.
    """

    calculation_id: int
    type: CalculationType
    role: Literal["primary", "additional"]
    request_index: int | None = None


@dataclass
class ConformerUploadOutcome:
    """Structured return value of :func:`persist_conformer_upload`.

    Carries the freshly-created ``ConformerObservation`` plus structured
    refs to every ``Calculation`` row produced by the workflow so that
    callers (route handler, async worker) can return calc IDs to clients
    for second-phase artifact upload.
    """

    observation: ConformerObservation
    primary_calculation: ConformerUploadCalculationRef
    additional_calculations: list[ConformerUploadCalculationRef] = field(
        default_factory=list
    )


def persist_conformer_upload(
    session: Session,
    request: ConformerUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> ConformerUploadOutcome:
    """Persist a complete conformer upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Upload-facing conformer payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: :class:`ConformerUploadOutcome` carrying the new observation
        row and structured refs for every calculation created by the
        workflow. The upload always creates a new observation row; only
        the basin-level group may be reused.
    :raises ValueError:
        If species identity or geometry parsing fails during upload resolution.
    """

    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by,
        xyz_text=request.geometry.xyz_text,
    )
    geometry = resolve_geometry_payload(session, request.geometry)

    calculation = resolve_and_persist_calculation_with_results(
        session,
        request.calculation,
        species_entry_id=species_entry.id,
        created_by=created_by,
    )

    # Producer-explicit output_geometries take precedence. Otherwise the
    # narrowed fallback only fires for opt (the one calc type whose
    # converged output IS the conformer geometry); freq, sp, and all
    # other types now produce zero output_geometry rows unless the
    # producer declares them explicitly.
    attach_calculation_output_geometries(
        session,
        calc=calculation,
        explicit_output_geometries=request.calculation.output_geometries,
        fallback_geometry_id=geometry.id,
        context=(
            f"primary calculation (type='{calculation.type.value}', "
            f"id={calculation.id})"
        ),
    )
    # Producer-explicit input_geometries take precedence; otherwise the
    # freq/sp fallback links the conformer geometry. opt skips the
    # fallback (its real input is the pre-opt xyz, not the conformer
    # geometry) and only gets a row when the producer declares one.
    attach_calculation_input_geometries(
        session,
        calc=calculation,
        explicit_input_geometries=request.calculation.input_geometries,
        fallback_geometry_id=geometry.id,
        context=(
            f"primary calculation (type='{calculation.type.value}', "
            f"id={calculation.id})"
        ),
    )

    additional_calcs = []
    if request.additional_calculations:
        additional_calcs = persist_additional_calculations(
            session,
            primary_calc=calculation,
            additional_uploads=request.additional_calculations,
            geometry_id=geometry.id,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )

    # Parse XYZ for torsion fingerprinting
    parsed = parse_xyz(GeometryPayload(xyz_text=request.geometry.xyz_text))
    smiles = request.species_entry.smiles

    conformer_group, fingerprint, scheme = resolve_conformer_group(
        session,
        species_entry,
        label=request.label,
        created_by=created_by,
        smiles=smiles,
        xyz_atoms=parsed.atoms,
    )
    observation = ConformerObservation(
        conformer_group_id=conformer_group.id,
        scientific_origin=request.scientific_origin,
        note=request.note,
        created_by=created_by,
        assignment_scheme_id=scheme.id if scheme is not None else None,
        torsion_fingerprint_json=fingerprint.to_dict() if fingerprint is not None else None,
    )
    session.add(observation)
    session.flush()

    # Anchor ALL calculations (primary + additional) to this conformer
    # observation so the structure context is unambiguous.
    calculation.conformer_observation_id = observation.id
    for child_calc in additional_calcs:
        child_calc.conformer_observation_id = observation.id

    statmech_row = None
    if request.statmech is not None:
        statmech_row = resolve_or_create_statmech(
            session,
            request.statmech,
            species_entry_id=species_entry.id,
            uploaded_calculation_id=calculation.id,
            created_by=created_by,
        )

    transport_row = None
    if request.transport is not None:
        transport_row = resolve_and_create_transport(
            session,
            request.transport,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )

    applied_corrections: list = []
    for correction_payload in request.applied_energy_corrections:
        # Resolve local string keys to IDs.
        # source_conformer_key always resolves to the observation just created.
        source_conf_id = (
            observation.id
            if correction_payload.source_conformer_key is not None
            else None
        )
        source_calc_id = (
            calculation.id
            if correction_payload.source_calculation_key is not None
            else None
        )
        applied_corrections.append(
            create_applied_energy_correction(
                session,
                correction_payload,
                target_species_entry_id=species_entry.id,
                source_conformer_observation_id=source_conf_id,
                source_calculation_id=source_calc_id,
                created_by=created_by,
            )
        )

    session.flush()

    review_targets: list[RecordRef] = [
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
        RecordRef(SubmissionRecordType.conformer_group, conformer_group.id),
        RecordRef(SubmissionRecordType.conformer_observation, observation.id),
        RecordRef(SubmissionRecordType.calculation, calculation.id),
    ]
    review_targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id) for c in additional_calcs
    )
    if statmech_row is not None:
        review_targets.append(
            RecordRef(SubmissionRecordType.statmech, statmech_row.id)
        )
    if transport_row is not None:
        review_targets.append(
            RecordRef(SubmissionRecordType.transport, transport_row.id)
        )
    review_targets.extend(
        RecordRef(SubmissionRecordType.applied_energy_correction, aec.id)
        for aec in applied_corrections
    )
    apply_review_policy(
        session,
        targets=review_targets,
        policy=review_policy,
        created_by=created_by,
    )

    return ConformerUploadOutcome(
        observation=observation,
        primary_calculation=ConformerUploadCalculationRef(
            calculation_id=calculation.id,
            type=calculation.type,
            role="primary",
            request_index=None,
        ),
        additional_calculations=[
            ConformerUploadCalculationRef(
                calculation_id=child.id,
                type=child.type,
                role="additional",
                request_index=i,
            )
            for i, child in enumerate(additional_calcs)
        ],
    )
