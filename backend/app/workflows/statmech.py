"""Standalone statmech upload workflow orchestrator.

Persists statmech records submitted independently of a conformer
upload. Inline supporting calculations are resolved via their local
string keys to real calculation ids, and the resulting scientific
payload is routed through the canonical
:func:`resolve_or_create_statmech` service used by nested uploads.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.statmech import Statmech
from app.schemas.entities.statmech import (
    StatmechSourceCalculationCreate,
    StatmechTorsionCoordinateCreate,
    StatmechTorsionCreate,
)
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry
from app.services.statmech_resolution import resolve_or_create_statmech


def _assert_calculation_owned_by(
    calculation: Calculation,
    *,
    species_entry_id: int,
    context: str,
) -> None:
    """Defensive owner-consistency check for a resolved source calculation.

    Supporting calculations attached to a statmech record must belong to
    the same species entry as the statmech target; otherwise the
    provenance link would be scientifically meaningless.

    :raises ValueError: if the calculation does not belong to
        ``species_entry_id``.
    """
    if calculation.species_entry_id != species_entry_id:
        raise ValueError(
            f"{context}: calculation id={calculation.id} belongs to "
            f"species_entry_id={calculation.species_entry_id}, not to the "
            f"statmech target species_entry_id={species_entry_id}."
        )


def persist_statmech_upload(
    session: Session,
    request: StatmechUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> Statmech:
    """Persist a complete standalone statmech upload workflow.

    Resolves the target species entry, persists any inline supporting
    calculations, translates local calculation keys into real ids for
    both source-calculation links and torsion source scans, and routes
    the resulting payload through the canonical statmech resolution
    service so there is a single persistence implementation.

    Statmech is append-only: repeated uploads against the same species
    entry create independent rows.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing standalone statmech upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Statmech`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the statmech target's species entry.
    """
    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by
    )

    # Persist inline supporting calculations scoped to the target species
    # entry. Owner-consistency is enforced by construction but the
    # explicit check guards any future path that reuses existing calcs.
    calculations_by_key: dict[str, Calculation] = {}
    for calc_in in request.calculations:
        calc_row = resolve_and_persist_calculation_with_results(
            session,
            calc_in.calculation,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        _assert_calculation_owned_by(
            calc_row,
            species_entry_id=species_entry.id,
            context=f"statmech calculation '{calc_in.key}'",
        )
        calculations_by_key[calc_in.key] = calc_row

    # Resolve source-calculation links from local keys to real ids.
    resolved_sources = [
        StatmechSourceCalculationCreate(
            calculation_id=calculations_by_key[sc.calculation_key].id,
            role=sc.role,
        )
        for sc in request.source_calculations
    ]

    # Resolve torsion source scans from local keys and translate torsions
    # to the nested-create shape expected by the canonical service.
    resolved_torsions: list[StatmechTorsionCreate] = []
    for torsion_in in request.torsions:
        scan_id: int | None = None
        if torsion_in.source_scan_calculation_key is not None:
            scan_id = calculations_by_key[
                torsion_in.source_scan_calculation_key
            ].id
        resolved_torsions.append(
            StatmechTorsionCreate(
                torsion_index=torsion_in.torsion_index,
                symmetry_number=torsion_in.symmetry_number,
                treatment_kind=torsion_in.treatment_kind,
                dimension=torsion_in.dimension,
                top_description=torsion_in.top_description,
                invalidated_reason=torsion_in.invalidated_reason,
                note=torsion_in.note,
                source_scan_calculation_id=scan_id,
                coordinates=[
                    StatmechTorsionCoordinateCreate(
                        coordinate_index=c.coordinate_index,
                        atom1_index=c.atom1_index,
                        atom2_index=c.atom2_index,
                        atom3_index=c.atom3_index,
                        atom4_index=c.atom4_index,
                    )
                    for c in torsion_in.coordinates
                ],
            )
        )

    # Build the canonical statmech payload and route through the shared
    # resolution service. No uploaded_calculation_id here — standalone
    # uploads have no implicit "freshly uploaded" anchor calculation.
    core_payload = ConformerUploadStatmechPayload(
        scientific_origin=request.scientific_origin,
        literature=request.literature,
        workflow_tool_release=request.workflow_tool_release,
        software_release=request.software_release,
        external_symmetry=request.external_symmetry,
        point_group=request.point_group,
        is_linear=request.is_linear,
        rigid_rotor_kind=request.rigid_rotor_kind,
        statmech_treatment=request.statmech_treatment,
        freq_scale_factor=request.freq_scale_factor,
        uses_projected_frequencies=request.uses_projected_frequencies,
        optical_isomers=request.optical_isomers,
        note=request.note,
        uploaded_calculation_role=None,
        source_calculations=resolved_sources,
        torsions=resolved_torsions,
        electronic_levels=request.electronic_levels,
    )

    statmech = resolve_or_create_statmech(
        session,
        core_payload,
        species_entry_id=species_entry.id,
        uploaded_calculation_id=None,
        created_by=created_by,
    )

    # First-class rotational constants (cm⁻¹). These live only on the
    # standalone upload request (the shared ConformerUploadStatmechPayload
    # does not carry them), so they are applied directly to the created row.
    statmech.rotational_constant_a_cm1 = request.rotational_constant_a_cm1
    statmech.rotational_constant_b_cm1 = request.rotational_constant_b_cm1
    statmech.rotational_constant_c_cm1 = request.rotational_constant_c_cm1

    session.flush()

    targets = [
        RecordRef(SubmissionRecordType.statmech, statmech.id),
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id)
        for c in calculations_by_key.values()
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    return statmech
