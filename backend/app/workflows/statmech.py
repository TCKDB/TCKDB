"""Standalone statmech upload workflow orchestrator.

Persists statmech records submitted independently of a conformer
upload. Inline supporting calculations are persisted first and handed to
the canonical :func:`resolve_or_create_statmech` service as a local
key → ``calculation.id`` map; the service does the key resolution, the
same way it does for the nested conformer path.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.statmech import Statmech
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.services.calculation_ownership import (
    W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
)
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

logger = logging.getLogger(__name__)


def persist_statmech_upload(
    session: Session,
    request: StatmechUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> Statmech:
    """Persist a complete standalone statmech upload workflow.

    Resolves the target species entry, persists any inline supporting
    calculations, and routes the payload plus its local key →
    ``calculation.id`` map through the canonical statmech resolution
    service so there is a single persistence implementation.

    Statmech is append-only: repeated uploads against the same species
    entry create independent rows.

    A ``source_calculations`` entry may instead cite a calculation an
    *earlier* request deposited, by ``existing_calculation_id``. Nothing
    is persisted for it here — the row already exists, which is the point,
    since re-sending it would mint a duplicate calculation for the same
    job. Its existence, ownership and role/type compatibility are checked
    inside the resolution service alongside the inline path.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing standalone statmech upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Statmech`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the statmech target's species entry.
    :raises NotFoundError: If an ``existing_calculation_id`` names a
        calculation row that does not exist. 404 carrying
        ``unknown_calculation_ref`` since #230 — the same code the
        public-ref spelling on ``/uploads/kinetics`` uses, since the
        missing row and its repair are the same.
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
        assert_calculation_owned_by(
            calc_row,
            code=W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="statmech",
            context=f"statmech calculation '{calc_in.key}'",
            species_entry_id=species_entry.id,
        )
        calculations_by_key[calc_in.key] = calc_row

    # Build the canonical statmech payload and route through the shared
    # resolution service. The source-calculation links and torsions travel
    # as-is — only a key → id map is handed over.
    #
    # ``source_calculations`` entries are ``StatmechSourceCalculationIn``,
    # a subclass of the key-only component this payload's field is
    # annotated with, because the standalone upload also accepts
    # ``existing_calculation_id``. They survive the handover unnarrowed
    # (Pydantic v2 revalidates model instances never by default), and the
    # service reads the chained id back off them; nothing is translated
    # here, so there is only one place that decides what a citation means.
    # No uploaded_calculation_id here — standalone uploads have no
    # implicit "freshly uploaded" anchor calculation.
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
        source_calculations=request.source_calculations,
        torsions=request.torsions,
        electronic_levels=request.electronic_levels,
    )

    statmech = resolve_or_create_statmech(
        session,
        core_payload,
        species_entry_id=species_entry.id,
        uploaded_calculation_id=None,
        calculations_by_key={
            key: calc.id for key, calc in calculations_by_key.items()
        },
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
