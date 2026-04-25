"""Calculation read endpoints — Phase 1 (Tier A + B) and Phase 2 (Tier C)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
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
    CalculationNEBImageResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
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
    CalculationNEBImageResultRead,
    CalculationOptResultRead,
    CalculationOutputGeometryDetailRead,
    CalculationParameterRead,
    CalculationRead,
    CalculationScanCoordinateRead,
    CalculationScanPointRead,
    CalculationScanResultRead,
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
# Tier C — Phase 2: NEB sub-resources
# ---------------------------------------------------------------------------


@router.get(
    "/{calculation_id}/neb-images",
    response_model=list[CalculationNEBImageResultRead],
)
def list_neb_images(
    calculation_id: int, session: Session = Depends(get_db)
):
    _get_calculation_or_404(calculation_id, session)
    rows = session.scalars(
        select(CalculationNEBImageResult)
        .where(CalculationNEBImageResult.calculation_id == calculation_id)
        .order_by(CalculationNEBImageResult.image_index.asc())
    ).all()
    return [CalculationNEBImageResultRead.model_validate(r) for r in rows]


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


@router.get(
    "/{calculation_id}/geometry-validation",
    response_model=CalculationGeometryValidationRead,
)
def get_geometry_validation(
    calculation_id: int, session: Session = Depends(get_db)
):
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
