"""Calculation read endpoints — Phase 1 (Tier A + B) and Phase 2 (Tier C),
plus the calculation-targeted artifact upload endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import (
    PaginationParams,
    can_modify_calculation_artifacts,
    get_current_user,
    get_db,
    get_write_db,
)
from app.api.errors import NotFoundError
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.upload_warning import UploadWarning
from app.services.artifact_persistence import persist_artifact_batch
from app.services.calculation_parameter_extraction import (
    try_extract_parameters_from_input_upload,
)
from app.api.routes._pagination import PaginatedResponse
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationConstraint,
    CalculationDependency,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
    CalculationSCFStability,
    CalculationSPResult,
)
from app.db.models.geometry import Geometry
from app.db.models.common import CalculationQuality, CalculationType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.schemas.entities.calculation import (
    CalculationArtifactRead,
    CalculationConstraintRead,
    CalculationDependencyDirectionalRead,
    CalculationFreqResultRead,
    CalculationGeometryValidationRead,
    CalculationInputGeometryDetailRead,
    CalculationIRCPointRead,
    CalculationIRCResultRead,
    CalculationOptResultRead,
    CalculationPathSearchPointRead,
    CalculationPathSearchResultRead,
    CalculationOutputGeometryDetailRead,
    CalculationParameterRead,
    CalculationRead,
    CalculationScanCoordinateRead,
    CalculationScanPointRead,
    CalculationScanResultRead,
    CalculationSCFStabilityRead,
    CalculationSPResultRead,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_calculation_or_404(
    calculation_id: int, session: Session
) -> Calculation:
    calc = session.get(Calculation, calculation_id)
    if calc is None:
        raise NotFoundError(f"Calculation {calculation_id} not found")
    return calc


# ---------------------------------------------------------------------------
# Tier A — parent resource
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[CalculationRead])
def list_calculations(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    # MVP filters (parent-table columns)
    type: CalculationType | None = Query(None),
    quality: CalculationQuality | None = Query(None),
    species_entry_id: int | None = Query(None),
    transition_state_entry_id: int | None = Query(None),
    conformer_observation_id: int | None = Query(None),
    lot_id: int | None = Query(None),
    software_release_id: int | None = Query(None),
    workflow_tool_release_id: int | None = Query(None),
    literature_id: int | None = Query(None),
    # Optional joined filters
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software_name: str | None = Query(None),
):
    needs_join = method is not None or basis is not None or software_name is not None

    # Build base filter on parent-table columns.
    base = select(Calculation.id)
    if type is not None:
        base = base.where(Calculation.type == type)
    if quality is not None:
        base = base.where(Calculation.quality == quality)
    if species_entry_id is not None:
        base = base.where(Calculation.species_entry_id == species_entry_id)
    if transition_state_entry_id is not None:
        base = base.where(
            Calculation.transition_state_entry_id == transition_state_entry_id
        )
    if conformer_observation_id is not None:
        base = base.where(
            Calculation.conformer_observation_id == conformer_observation_id
        )
    if lot_id is not None:
        base = base.where(Calculation.lot_id == lot_id)
    if software_release_id is not None:
        base = base.where(Calculation.software_release_id == software_release_id)
    if workflow_tool_release_id is not None:
        base = base.where(
            Calculation.workflow_tool_release_id == workflow_tool_release_id
        )
    if literature_id is not None:
        base = base.where(Calculation.literature_id == literature_id)

    # Joined filters: LOT method/basis, software name.
    if method is not None or basis is not None:
        base = base.join(LevelOfTheory, Calculation.lot_id == LevelOfTheory.id)
        if method is not None:
            base = base.where(LevelOfTheory.method == method)
        if basis is not None:
            base = base.where(LevelOfTheory.basis == basis)
    if software_name is not None:
        base = base.join(
            SoftwareRelease,
            Calculation.software_release_id == SoftwareRelease.id,
        ).join(Software, SoftwareRelease.software_id == Software.id)
        base = base.where(Software.name == software_name)

    if needs_join:
        # Two-query pattern: count distinct parent IDs, then fetch.
        id_subquery = base.distinct().subquery()
        total = session.scalar(
            select(func.count()).select_from(id_subquery)
        ) or 0
        rows = session.scalars(
            select(Calculation)
            .where(Calculation.id.in_(select(id_subquery.c.id)))
            .order_by(Calculation.id)
            .offset(pagination.skip)
            .limit(pagination.limit)
        ).all()
    else:
        total = session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        rows = session.scalars(
            select(Calculation)
            .where(Calculation.id.in_(base))
            .order_by(Calculation.id)
            .offset(pagination.skip)
            .limit(pagination.limit)
        ).all()

    return PaginatedResponse(
        items=[CalculationRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{calculation_id}", response_model=CalculationRead)
def get_calculation(calculation_id: int, session: Session = Depends(get_db)):
    calc = _get_calculation_or_404(calculation_id, session)
    return CalculationRead.model_validate(calc)


# ---------------------------------------------------------------------------
# Tier B — SP / opt / freq results (one-to-one → 404 when absent)
# ---------------------------------------------------------------------------


@router.get("/{calculation_id}/sp-result", response_model=CalculationSPResultRead)
def get_sp_result(calculation_id: int, session: Session = Depends(get_db)):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationSPResult).where(
            CalculationSPResult.calculation_id == calculation_id
        )
    )
    if row is None:
        raise NotFoundError(
            f"SP result not found for calculation {calculation_id}"
        )
    return CalculationSPResultRead.model_validate(row)


@router.get("/{calculation_id}/opt-result", response_model=CalculationOptResultRead)
def get_opt_result(calculation_id: int, session: Session = Depends(get_db)):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationOptResult).where(
            CalculationOptResult.calculation_id == calculation_id
        )
    )
    if row is None:
        raise NotFoundError(
            f"Optimization result not found for calculation {calculation_id}"
        )
    return CalculationOptResultRead.model_validate(row)


@router.get("/{calculation_id}/freq-result", response_model=CalculationFreqResultRead)
def get_freq_result(calculation_id: int, session: Session = Depends(get_db)):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationFreqResult).where(
            CalculationFreqResult.calculation_id == calculation_id
        )
    )
    if row is None:
        raise NotFoundError(
            f"Frequency result not found for calculation {calculation_id}"
        )
    return CalculationFreqResultRead.model_validate(row)


# ---------------------------------------------------------------------------
# Tier B — input / output geometries (one-to-many → [] when empty)
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/input-geometries",
    response_model=list[CalculationInputGeometryDetailRead],
)
def list_input_geometries(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationInputGeometry)
        .where(CalculationInputGeometry.calculation_id == calculation_id)
        .options(joinedload(CalculationInputGeometry.geometry).selectinload(Geometry.atoms))
        .order_by(CalculationInputGeometry.input_order)
    ).unique().all()
    return [CalculationInputGeometryDetailRead.model_validate(r) for r in rows]


@router.get(
    "/{calculation_id}/output-geometries",
    response_model=list[CalculationOutputGeometryDetailRead],
)
def list_output_geometries(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationOutputGeometry)
        .where(CalculationOutputGeometry.calculation_id == calculation_id)
        .options(joinedload(CalculationOutputGeometry.geometry).selectinload(Geometry.atoms))
        .order_by(CalculationOutputGeometry.output_order)
    ).unique().all()
    return [CalculationOutputGeometryDetailRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier B — dependencies (one-to-many → [] when empty)
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/dependencies",
    response_model=list[CalculationDependencyDirectionalRead],
)
def list_dependencies(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    # Return both outgoing and incoming edges, annotated with direction.
    rows = session.scalars(
        select(CalculationDependency)
        .where(
            (CalculationDependency.parent_calculation_id == calculation_id)
            | (CalculationDependency.child_calculation_id == calculation_id)
        )
        .order_by(
            CalculationDependency.dependency_role.asc(),
            CalculationDependency.parent_calculation_id.asc(),
            CalculationDependency.child_calculation_id.asc(),
        )
    ).all()
    result = []
    for row in rows:
        direction = (
            "outgoing" if row.parent_calculation_id == calculation_id else "incoming"
        )
        result.append(
            CalculationDependencyDirectionalRead(
                parent_calculation_id=row.parent_calculation_id,
                child_calculation_id=row.child_calculation_id,
                dependency_role=row.dependency_role,
                direction=direction,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Tier B — constraints (one-to-many → [] when empty)
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/constraints",
    response_model=list[CalculationConstraintRead],
)
def list_constraints(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationConstraint)
        .where(CalculationConstraint.calculation_id == calculation_id)
        .order_by(CalculationConstraint.constraint_index)
    ).all()
    return [CalculationConstraintRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier C — Phase 2: scan sub-resources
# ---------------------------------------------------------------------------


@router.get("/{calculation_id}/scan-result", response_model=CalculationScanResultRead)
def get_scan_result(calculation_id: int, session: Session = Depends(get_db)):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationScanResult)
        .where(CalculationScanResult.calculation_id == calculation_id)
        .options(
            selectinload(CalculationScanResult.coordinates),
            selectinload(CalculationScanResult.constraints),
            selectinload(CalculationScanResult.points).selectinload(
                CalculationScanPoint.coordinate_values
            ),
        )
    )
    if row is None:
        raise NotFoundError(
            f"Scan result not found for calculation {calculation_id}"
        )
    return CalculationScanResultRead.model_validate(row)


@router.get(
    "/{calculation_id}/scan-coordinates",
    response_model=list[CalculationScanCoordinateRead],
)
def list_scan_coordinates(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationScanCoordinate)
        .where(CalculationScanCoordinate.calculation_id == calculation_id)
        .order_by(CalculationScanCoordinate.coordinate_index.asc())
    ).all()
    return [CalculationScanCoordinateRead.model_validate(r) for r in rows]


@router.get(
    "/{calculation_id}/scan-points",
    response_model=list[CalculationScanPointRead],
)
def list_scan_points(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationScanPoint)
        .where(CalculationScanPoint.calculation_id == calculation_id)
        .options(selectinload(CalculationScanPoint.coordinate_values))
        .order_by(CalculationScanPoint.point_index.asc())
    ).all()
    return [CalculationScanPointRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier C — Phase 2: IRC sub-resources
# ---------------------------------------------------------------------------


@router.get("/{calculation_id}/irc-result", response_model=CalculationIRCResultRead)
def get_irc_result(calculation_id: int, session: Session = Depends(get_db)):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationIRCResult)
        .where(CalculationIRCResult.calculation_id == calculation_id)
        .options(selectinload(CalculationIRCResult.points))
    )
    if row is None:
        raise NotFoundError(
            f"IRC result not found for calculation {calculation_id}"
        )
    return CalculationIRCResultRead.model_validate(row)


@router.get(
    "/{calculation_id}/irc-points",
    response_model=list[CalculationIRCPointRead],
)
def list_irc_points(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationIRCPoint)
        .where(CalculationIRCPoint.calculation_id == calculation_id)
        .order_by(CalculationIRCPoint.point_index.asc())
    ).all()
    return [CalculationIRCPointRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier C — Phase 2: path-search sub-resources (NEB / GSM / string methods)
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/path-search-result",
    response_model=CalculationPathSearchResultRead,
)
def get_path_search_result(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationPathSearchResult)
        .where(CalculationPathSearchResult.calculation_id == calculation_id)
        .options(selectinload(CalculationPathSearchResult.points))
    )
    if row is None:
        raise NotFoundError(
            f"Path-search result not found for calculation {calculation_id}"
        )
    return CalculationPathSearchResultRead.model_validate(row)


@router.get(
    "/{calculation_id}/path-search-points",
    response_model=list[CalculationPathSearchPointRead],
)
def list_path_search_points(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationPathSearchPoint)
        .where(CalculationPathSearchPoint.calculation_id == calculation_id)
        .order_by(CalculationPathSearchPoint.point_index.asc())
    ).all()
    return [CalculationPathSearchPointRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier C — Phase 2: parameters, artifacts, geometry validation
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/parameters",
    response_model=list[CalculationParameterRead],
)
def list_parameters(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationParameter)
        .where(CalculationParameter.calculation_id == calculation_id)
        .order_by(
            CalculationParameter.parameter_index.asc().nullslast(),
            CalculationParameter.id.asc(),
        )
    ).all()
    return [CalculationParameterRead.model_validate(r) for r in rows]


@router.get(
    "/{calculation_id}/artifacts",
    response_model=list[CalculationArtifactRead],
)
def list_artifacts(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationArtifact)
        .where(CalculationArtifact.calculation_id == calculation_id)
        .order_by(CalculationArtifact.id.asc())
    ).all()
    return [CalculationArtifactRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Artifact upload — POST /calculations/{calculation_id}/artifacts
# ---------------------------------------------------------------------------


class ArtifactsUploadRequest(BaseModel):
    """Inline batch artifact upload payload.

    All artifacts in the batch are validated end-to-end before any
    storage write fires. A single per-artifact failure rejects the
    whole batch with no DB rows and no S3 writes.
    """

    artifacts: list[ArtifactIn] = Field(min_length=1)


class ArtifactsUploadResult(BaseModel):
    calculation_id: int
    artifacts: list[CalculationArtifactRead]
    warnings: list[UploadWarning] = []


@router.post(
    "/{calculation_id}/artifacts",
    response_model=ArtifactsUploadResult,
    status_code=201,
)
def upload_calculation_artifacts(
    calculation_id: int,
    request: ArtifactsUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    """Attach one or more artifacts (logs, inputs, checkpoints) to a
    calculation.

    Second-phase upload: the calculation must already exist (typically
    created via ``POST /uploads/conformers`` or a contribution bundle).
    The upload is batch-atomic — any per-artifact validation failure in
    the request rejects the entire batch with 422 and no DB rows or S3
    writes are produced. If a storage write fails partway through pass-2,
    earlier objects in the batch are best-effort deleted and the route
    returns 503.

    Authorization (any one of):

    - the caller created the calculation (``calculation.created_by ==
      current_user.id``);
    - the caller owns a live submission linked to this calculation
      through ``submission_record_link`` (rejected/superseded
      submissions do not qualify);
    - the caller has the ``curator`` or ``admin`` role.

    Notes for future maintainers:

    - This endpoint assumes calculations are non-deletable. There is no
      ``DELETE /calculations/{id}`` route today; if one is added later
      it must hold a row lock on the calculation before deletion to
      avoid a TOCTOU race against in-flight artifact uploads.
    - ``CalculationArtifact`` rows are append-only artifact-metadata
      records. They intentionally do not deduplicate rows by content
      hash; same-bytes uploads from different events produce two rows
      pointing at one content-addressed object. Each row records
      ``filename`` and ``created_by`` so the audit trail is meaningful
      even when the bytes are opaque (e.g. binary checkpoints).
    """
    if (replay := idem.maybe_replay()) is not None:
        return replay

    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(
            status_code=404, detail="Calculation not found."
        )

    if not can_modify_calculation_artifacts(session, calculation, current_user):
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to attach artifacts to this calculation.",
        )

    # persist_artifact_batch flushes internally so SQL-layer errors are
    # caught by its compensation block (deletes already-stored S3 objects).
    # If it returns, rows are flushed; if it raises, no S3 leak.
    rows = persist_artifact_batch(
        session,
        calculation_id=calculation_id,
        artifacts=request.artifacts,
        created_by=current_user.id,
    )

    # Opportunistic calculation_parameter extraction for input artifacts.
    # The helper filters by ArtifactKind.input and is best-effort —
    # never aborts the upload.
    for art_in in request.artifacts:
        try_extract_parameters_from_input_upload(session, calculation, art_in)

    result = ArtifactsUploadResult(
        calculation_id=calculation_id,
        artifacts=[CalculationArtifactRead.model_validate(r) for r in rows],
    )
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.get(
    "/{calculation_id}/geometry-validation",
    response_model=CalculationGeometryValidationRead,
)
def get_geometry_validation(
    calculation_id: int, session: Session = Depends(get_db)
):
    """Return geometry-identity validation evidence for a calculation.

    Reports whether the calculation's output geometry preserves the declared
    molecular identity (graph isomorphism vs. species SMILES, optional
    Kabsch RMSD against the input geometry).

    This is **not** SCF/wavefunction stability — see
    ``GET /calculations/{id}/scf-stability`` for that — and it is not
    frequency/stationary-point validation.

    Unlike the SCF-stability endpoint, missing rows return 404 rather than
    a projected ``not_checked`` payload: geometry validation is not yet
    populated by the standard upload workflow, so most calculations
    legitimately have no record here today.
    """
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationGeometryValidation).where(
            CalculationGeometryValidation.calculation_id == calculation_id
        )
    )
    if row is None:
        raise NotFoundError(
            f"Geometry validation not found for calculation {calculation_id}"
        )
    return CalculationGeometryValidationRead.model_validate(row)


@router.get(
    "/{calculation_id}/scf-stability",
    response_model=CalculationSCFStabilityRead,
)
def get_scf_stability(
    calculation_id: int, session: Session = Depends(get_db)
):
    """Return SCF wavefunction stability evidence for a calculation.

    Unlike sibling result endpoints, this endpoint never 404s on a
    missing row: absence of a ``calc_scf_stability`` row is the
    canonical encoding of "not checked", and the response projects
    ``status = "not_checked"`` with all evidence fields ``null``.
    """
    _get_calculation_or_404(calculation_id, session)
    row = session.scalar(
        select(CalculationSCFStability).where(
            CalculationSCFStability.calculation_id == calculation_id
        )
    )
    if row is None:
        return CalculationSCFStabilityRead(
            calculation_id=calculation_id,
            status="not_checked",
        )
    return CalculationSCFStabilityRead.model_validate(row)
