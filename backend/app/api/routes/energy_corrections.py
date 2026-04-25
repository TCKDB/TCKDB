"""Energy correction scheme, frequency scale factor, and applied correction read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import (
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    FrequencyScaleKind,
)
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    EnergyCorrectionScheme,
    FrequencyScaleFactor,
)
from app.schemas.entities.energy_correction import (
    AppliedEnergyCorrectionRead,
    EnergyCorrectionSchemeRead,
    FrequencyScaleFactorRead,
)

schemes_router = APIRouter()
scale_factors_router = APIRouter()
applied_router = APIRouter()


# ---------------------------------------------------------------------------
# Energy correction schemes
# ---------------------------------------------------------------------------


@schemes_router.get("", response_model=PaginatedResponse[EnergyCorrectionSchemeRead])
def list_energy_correction_schemes(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    kind: EnergyCorrectionSchemeKind | None = Query(None),
    name: str | None = Query(None),
    level_of_theory_id: int | None = Query(None),
    source_literature_id: int | None = Query(None),
    version: str | None = Query(None),
):
    base = select(EnergyCorrectionScheme.id)
    if kind is not None:
        base = base.where(EnergyCorrectionScheme.kind == kind)
    if name is not None:
        base = base.where(EnergyCorrectionScheme.name == name)
    if level_of_theory_id is not None:
        base = base.where(
            EnergyCorrectionScheme.level_of_theory_id == level_of_theory_id
        )
    if source_literature_id is not None:
        base = base.where(
            EnergyCorrectionScheme.source_literature_id == source_literature_id
        )
    if version is not None:
        base = base.where(EnergyCorrectionScheme.version == version)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(EnergyCorrectionScheme)
        .where(EnergyCorrectionScheme.id.in_(base))
        .options(
            selectinload(EnergyCorrectionScheme.atom_params),
            selectinload(EnergyCorrectionScheme.bond_params),
            selectinload(EnergyCorrectionScheme.component_params),
        )
        .order_by(EnergyCorrectionScheme.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[EnergyCorrectionSchemeRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@schemes_router.get("/{scheme_id}", response_model=EnergyCorrectionSchemeRead)
def get_energy_correction_scheme(
    scheme_id: int, session: Session = Depends(get_db)
):
    row = session.scalar(
        select(EnergyCorrectionScheme)
        .where(EnergyCorrectionScheme.id == scheme_id)
        .options(
            selectinload(EnergyCorrectionScheme.atom_params),
            selectinload(EnergyCorrectionScheme.bond_params),
            selectinload(EnergyCorrectionScheme.component_params),
        )
    )
    if row is None:
        raise NotFoundError(f"EnergyCorrectionScheme {scheme_id} not found")
    return EnergyCorrectionSchemeRead.model_validate(row)


# ---------------------------------------------------------------------------
# Frequency scale factors
# ---------------------------------------------------------------------------


@scale_factors_router.get(
    "", response_model=PaginatedResponse[FrequencyScaleFactorRead]
)
def list_frequency_scale_factors(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    level_of_theory_id: int | None = Query(None),
    software_id: int | None = Query(None),
    scale_kind: FrequencyScaleKind | None = Query(None),
    source_literature_id: int | None = Query(None),
    workflow_tool_release_id: int | None = Query(None),
):
    base = select(FrequencyScaleFactor.id)
    if level_of_theory_id is not None:
        base = base.where(
            FrequencyScaleFactor.level_of_theory_id == level_of_theory_id
        )
    if software_id is not None:
        base = base.where(FrequencyScaleFactor.software_id == software_id)
    if scale_kind is not None:
        base = base.where(FrequencyScaleFactor.scale_kind == scale_kind)
    if source_literature_id is not None:
        base = base.where(
            FrequencyScaleFactor.source_literature_id == source_literature_id
        )
    if workflow_tool_release_id is not None:
        base = base.where(
            FrequencyScaleFactor.workflow_tool_release_id == workflow_tool_release_id
        )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(FrequencyScaleFactor)
        .where(FrequencyScaleFactor.id.in_(base))
        .order_by(FrequencyScaleFactor.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[FrequencyScaleFactorRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@scale_factors_router.get("/{fsf_id}", response_model=FrequencyScaleFactorRead)
def get_frequency_scale_factor(
    fsf_id: int, session: Session = Depends(get_db)
):
    row = session.get(FrequencyScaleFactor, fsf_id)
    if row is None:
        raise NotFoundError(f"FrequencyScaleFactor {fsf_id} not found")
    return FrequencyScaleFactorRead.model_validate(row)


# ---------------------------------------------------------------------------
# Applied energy corrections
# ---------------------------------------------------------------------------


@applied_router.get(
    "", response_model=PaginatedResponse[AppliedEnergyCorrectionRead]
)
def list_applied_energy_corrections(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    target_species_entry_id: int | None = Query(None),
    target_reaction_entry_id: int | None = Query(None),
    scheme_id: int | None = Query(None),
    frequency_scale_factor_id: int | None = Query(None),
    application_role: EnergyCorrectionApplicationRole | None = Query(None),
):
    base = select(AppliedEnergyCorrection.id)
    if target_species_entry_id is not None:
        base = base.where(
            AppliedEnergyCorrection.target_species_entry_id
            == target_species_entry_id
        )
    if target_reaction_entry_id is not None:
        base = base.where(
            AppliedEnergyCorrection.target_reaction_entry_id
            == target_reaction_entry_id
        )
    if scheme_id is not None:
        base = base.where(AppliedEnergyCorrection.scheme_id == scheme_id)
    if frequency_scale_factor_id is not None:
        base = base.where(
            AppliedEnergyCorrection.frequency_scale_factor_id
            == frequency_scale_factor_id
        )
    if application_role is not None:
        base = base.where(
            AppliedEnergyCorrection.application_role == application_role
        )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(AppliedEnergyCorrection)
        .where(AppliedEnergyCorrection.id.in_(base))
        .options(selectinload(AppliedEnergyCorrection.components))
        .order_by(AppliedEnergyCorrection.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[AppliedEnergyCorrectionRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@applied_router.get(
    "/{correction_id}", response_model=AppliedEnergyCorrectionRead
)
def get_applied_energy_correction(
    correction_id: int, session: Session = Depends(get_db)
):
    row = session.scalar(
        select(AppliedEnergyCorrection)
        .where(AppliedEnergyCorrection.id == correction_id)
        .options(selectinload(AppliedEnergyCorrection.components))
    )
    if row is None:
        raise NotFoundError(
            f"AppliedEnergyCorrection {correction_id} not found"
        )
    return AppliedEnergyCorrectionRead.model_validate(row)
