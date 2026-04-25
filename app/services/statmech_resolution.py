"""Service helpers for creating statmech records.

Statmech is a result table — every upload creates a new row.
No deduplication against existing records.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.energy_correction_resolution import resolve_or_create_freq_scale_factor_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref


def resolve_or_create_statmech(
    session: Session,
    payload: ConformerUploadStatmechPayload,
    *,
    species_entry_id: int,
    uploaded_calculation_id: int | None = None,
    created_by: int | None = None,
) -> Statmech:
    """Create a statmech record and attach nested provenance.

    Always creates a new row — statmech records are provenance-bearing
    scientific results and multiple records per species entry are valid.

    The ``uploaded_calculation_id`` is only used when
    ``payload.uploaded_calculation_role`` is set (nested conformer upload
    path). Standalone statmech uploads leave both unset and declare any
    supporting calculations explicitly via ``payload.source_calculations``.

    :param session: Active SQLAlchemy session.
    :param payload: Workflow-facing statmech payload.
    :param species_entry_id: Resolved owner species-entry id.
    :param uploaded_calculation_id: Optional calculation id produced by
        the caller workflow; linked as a source calculation only when
        ``payload.uploaded_calculation_role`` is also set.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Statmech`` row with linked sources/torsions.
    :raises ValueError: If ``uploaded_calculation_role`` is set but
        ``uploaded_calculation_id`` is not supplied.
    """

    literature = (
        resolve_or_create_literature(session, payload.literature)
        if payload.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, payload.software_release)
        if payload.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, payload.workflow_tool_release
    )

    fsf_id = None
    if payload.freq_scale_factor is not None:
        fsf = resolve_or_create_freq_scale_factor_ref(
            session, payload.freq_scale_factor, created_by=created_by
        )
        fsf_id = fsf.id

    statmech = Statmech(
        species_entry_id=species_entry_id,
        scientific_origin=payload.scientific_origin,
        literature_id=literature.id if literature is not None else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        external_symmetry=payload.external_symmetry,
        point_group=payload.point_group,
        is_linear=payload.is_linear,
        rigid_rotor_kind=payload.rigid_rotor_kind,
        statmech_treatment=payload.statmech_treatment,
        frequency_scale_factor_id=fsf_id,
        uses_projected_frequencies=payload.uses_projected_frequencies,
        note=payload.note,
        created_by=created_by,
    )
    session.add(statmech)
    session.flush()

    # Attach source calculations
    if payload.uploaded_calculation_role is not None:
        if uploaded_calculation_id is None:
            raise ValueError(
                "uploaded_calculation_role is set but no uploaded_calculation_id "
                "was provided to resolve_or_create_statmech."
            )
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=uploaded_calculation_id,
                role=payload.uploaded_calculation_role,
            )
        )

    for source in payload.source_calculations:
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=source.calculation_id,
                role=source.role,
            )
        )

    # Attach torsions and coordinates
    for torsion_payload in payload.torsions:
        torsion = StatmechTorsion(
            statmech_id=statmech.id,
            torsion_index=torsion_payload.torsion_index,
            symmetry_number=torsion_payload.symmetry_number,
            treatment_kind=torsion_payload.treatment_kind,
            dimension=torsion_payload.dimension,
            top_description=torsion_payload.top_description,
            invalidated_reason=torsion_payload.invalidated_reason,
            note=torsion_payload.note,
            source_scan_calculation_id=torsion_payload.source_scan_calculation_id,
        )
        session.add(torsion)
        session.flush()

        for coordinate_payload in torsion_payload.coordinates:
            session.add(
                StatmechTorsionDefinition(
                    torsion_id=torsion.id,
                    coordinate_index=coordinate_payload.coordinate_index,
                    atom1_index=coordinate_payload.atom1_index,
                    atom2_index=coordinate_payload.atom2_index,
                    atom3_index=coordinate_payload.atom3_index,
                    atom4_index=coordinate_payload.atom4_index,
                )
            )

    session.flush()
    return statmech
