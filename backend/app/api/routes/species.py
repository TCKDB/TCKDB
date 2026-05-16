"""Species and species-entry read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    PaginationParams,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.api.errors import NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation, CalculationSPResult
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    MoleculeKind,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.entities.conformer import (
    ConformerGroupRead,
    ConformerGroupSummaryRead,
    ConformerObservationRead,
    LowestSPConformerObservationRead,
    LowestSPConformerObservationResultRead,
    SpeciesEntryConformerGroupsRead,
)
from app.schemas.entities.species import SpeciesRead
from app.schemas.entities.species_entry import (
    SpeciesEntryConformerSummaryRead,
    SpeciesEntryRead,
)
from app.schemas.entities.species_entry_review import (
    SpeciesEntryReviewCreate,
    SpeciesEntryReviewRead,
)
from app.schemas.entities.statmech import StatmechRead
from app.schemas.entities.thermo import ThermoRead
from app.schemas.entities.transport import TransportRead
from app.api.routes._pagination import PaginatedResponse
from app.services.species_entry_review import (
    create_species_entry_review,
    list_species_entry_reviews,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[SpeciesRead])
def list_species(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    smiles: str | None = Query(None),
    inchi_key: str | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None),
    kind: MoleculeKind | None = Query(None),
):
    base = select(Species.id)
    if smiles is not None:
        base = base.where(Species.smiles == smiles)
    if inchi_key is not None:
        base = base.where(Species.inchi_key == inchi_key)
    if charge is not None:
        base = base.where(Species.charge == charge)
    if multiplicity is not None:
        base = base.where(Species.multiplicity == multiplicity)
    if kind is not None:
        base = base.where(Species.kind == kind)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Species)
        .where(Species.id.in_(base))
        .order_by(Species.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[SpeciesRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{species_id}", response_model=SpeciesRead)
def get_species(species_id: int, session: Session = Depends(get_db)):
    species = session.get(Species, species_id)
    if species is None:
        raise NotFoundError("Species not found")
    return SpeciesRead.model_validate(species)


# ---------------------------------------------------------------------------
# Species entries (mounted under /species-entries in router.py)
# ---------------------------------------------------------------------------

entries_router = APIRouter()


def _species_entry_conformer_summary(
    session: Session, entry_id: int
) -> SpeciesEntryConformerSummaryRead:
    """Aggregate conformer-group and observation counts for a species entry.

    Uses two scalar aggregates so the top-level species-entry read stays cheap
    and does not trigger per-row lazy loads through the conformer collection.
    """
    group_count = session.scalar(
        select(func.count(ConformerGroup.id)).where(
            ConformerGroup.species_entry_id == entry_id
        )
    ) or 0
    observation_count = session.scalar(
        select(func.count(ConformerObservation.id))
        .join(ConformerGroup)
        .where(ConformerGroup.species_entry_id == entry_id)
    ) or 0
    return SpeciesEntryConformerSummaryRead(
        conformer_group_count=group_count,
        conformer_observation_count=observation_count,
    )


@entries_router.get("/{entry_id}", response_model=SpeciesEntryRead)
def get_species_entry(entry_id: int, session: Session = Depends(get_db)):
    """Return one species entry with a compact conformer summary.

    The summary is a count-only aggregate. Clients that want to browse the
    actual conformer basins call `/species-entries/{id}/conformer-groups`.
    """
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    read = SpeciesEntryRead.model_validate(entry)
    read.conformer_summary = _species_entry_conformer_summary(session, entry_id)
    return read


@entries_router.get(
    "/{entry_id}/conformer-groups",
    response_model=SpeciesEntryConformerGroupsRead,
)
def list_conformer_groups_for_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    """List conformer groups (basins) for a species entry, basin-first.

    Returns group summaries with per-group observation counts and total counts
    aggregated in a single subquery join to avoid N+1 queries. Observations are
    not inlined here — drill into a specific group for detail.
    """
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    obs_count_sq = (
        select(
            ConformerObservation.conformer_group_id.label("group_id"),
            func.count(ConformerObservation.id).label("obs_count"),
        )
        .group_by(ConformerObservation.conformer_group_id)
        .subquery()
    )

    rows = session.execute(
        select(ConformerGroup, func.coalesce(obs_count_sq.c.obs_count, 0))
        .outerjoin(obs_count_sq, obs_count_sq.c.group_id == ConformerGroup.id)
        .where(ConformerGroup.species_entry_id == entry_id)
        .options(selectinload(ConformerGroup.selections))
        .order_by(ConformerGroup.id)
    ).all()

    groups: list[ConformerGroupSummaryRead] = [
        ConformerGroupSummaryRead(
            **ConformerGroupRead.model_validate(group).model_dump(),
            observation_count=int(obs_count),
        )
        for group, obs_count in rows
    ]
    total_observations = sum(g.observation_count for g in groups)
    return SpeciesEntryConformerGroupsRead(
        species_entry_id=entry_id,
        conformer_group_count=len(groups),
        conformer_observation_count=total_observations,
        groups=groups,
    )


@entries_router.get(
    "/{entry_id}/conformers",
    response_model=list[ConformerObservationRead],
)
def list_conformers_for_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    observations = session.scalars(
        select(ConformerObservation)
        .join(ConformerGroup)
        .where(ConformerGroup.species_entry_id == entry_id)
        .order_by(ConformerObservation.id)
    ).all()
    return [ConformerObservationRead.model_validate(o) for o in observations]


# Quality-priority rank: smaller rank wins during per-observation collapse and,
# unless the caller fixed quality, also as a tiebreaker in the final ranking.
# Kept as module-level so it can be reused and its ordering is obvious.
_QUALITY_PRIORITY: dict[CalculationQuality, int] = {
    CalculationQuality.curated: 1,
    CalculationQuality.raw: 2,
    CalculationQuality.rejected: 3,
}


def _resolve_lowest_sp_conformer_observation(
    session: Session,
    *,
    species_entry_id: int,
    lot_id: int,
    calculation_quality: CalculationQuality | None,
) -> LowestSPConformerObservationRead | None:
    """Run the lowest-qualifying-SP conformer-observation query for one species entry.

    The query runs in one bounded SQL round trip, not per-row Python loops:

    1. Join conformer_group → conformer_observation → calculation → calc_sp_result
       filtered to SP calculations at the requested LoT with a populated energy,
       optionally narrowed to a caller-supplied `calculation_quality`.
    2. Use `ROW_NUMBER() OVER (PARTITION BY observation_id)` to collapse each
       observation's qualifying SP rows down to one canonical candidate. The
       within-partition order is `(quality_rank, energy, created_at, calc_id)`.
    3. Globally order the surviving one-per-observation rows and take the top
       with `(energy, [quality_rank if quality not fixed], created_at, calc_id)`.

    Returns `None` when no qualifying SP calculation exists. That is a valid
    query outcome, not an error.
    """
    quality_rank = case(
        *[
            (Calculation.quality == quality, rank)
            for quality, rank in _QUALITY_PRIORITY.items()
        ],
        else_=len(_QUALITY_PRIORITY) + 1,
    ).label("quality_rank")

    base = (
        select(
            Calculation.id.label("calculation_id"),
            ConformerObservation.id.label("observation_id"),
            ConformerGroup.id.label("group_id"),
            Calculation.quality.label("quality"),
            Calculation.created_at.label("created_at"),
            CalculationSPResult.electronic_energy_hartree.label("energy"),
            quality_rank,
        )
        .select_from(Calculation)
        .join(
            ConformerObservation,
            ConformerObservation.id == Calculation.conformer_observation_id,
        )
        .join(
            ConformerGroup,
            ConformerGroup.id == ConformerObservation.conformer_group_id,
        )
        .join(
            CalculationSPResult,
            CalculationSPResult.calculation_id == Calculation.id,
        )
        .where(
            ConformerGroup.species_entry_id == species_entry_id,
            Calculation.type == CalculationType.sp,
            Calculation.lot_id == lot_id,
            CalculationSPResult.electronic_energy_hartree.is_not(None),
        )
    )
    if calculation_quality is not None:
        base = base.where(Calculation.quality == calculation_quality)

    base_sq = base.subquery()

    # Stage 2: per-observation collapse via ROW_NUMBER partitioned by observation.
    collapse_rank = (
        func.row_number()
        .over(
            partition_by=base_sq.c.observation_id,
            order_by=(
                base_sq.c.quality_rank.asc(),
                base_sq.c.energy.asc(),
                base_sq.c.created_at.asc(),
                base_sq.c.calculation_id.asc(),
            ),
        )
        .label("collapse_rank")
    )
    ranked = select(base_sq, collapse_rank).subquery()

    # Stage 3: final global ranking over one-row-per-observation survivors.
    survivors = select(ranked).where(ranked.c.collapse_rank == 1).subquery()

    final_order: list = [survivors.c.energy.asc()]
    if calculation_quality is None:
        final_order.append(survivors.c.quality_rank.asc())
    final_order.extend([
        survivors.c.created_at.asc(),
        survivors.c.calculation_id.asc(),
    ])

    row = session.execute(
        select(survivors).order_by(*final_order).limit(1)
    ).first()
    if row is None:
        return None

    return LowestSPConformerObservationRead(
        species_entry_id=species_entry_id,
        lot_id=lot_id,
        conformer_group_id=row.group_id,
        conformer_observation_id=row.observation_id,
        calculation_id=row.calculation_id,
        electronic_energy_hartree=row.energy,
        calculation_quality=row.quality,
    )


@entries_router.get(
    "/{entry_id}/conformer-observations/lowest-sp",
    response_model=LowestSPConformerObservationResultRead,
)
def get_lowest_sp_conformer_observation_for_entry(
    entry_id: int,
    lot_id: int = Query(..., description="Level-of-theory id defining the comparison context"),
    calculation_quality: CalculationQuality | None = Query(
        default=None,
        description="Optional filter: restrict to this quality bucket",
    ),
    session: Session = Depends(get_db),
):
    """Return the lowest qualifying SP conformer observation for a species entry.

    This is a context-qualified ranking, not a stored "best conformer" claim.
    A calculation qualifies only if it is an SP at the requested `lot_id` with
    a non-null electronic energy on an observation belonging to this species
    entry. Multiple SPs on the same observation are first collapsed to one
    canonical candidate before observations are compared against each other.

    Returns 200 with `result: null` when no qualifying SP exists — that is a
    legitimate query outcome, not an error condition.
    """
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    if session.get(LevelOfTheory, lot_id) is None:
        raise NotFoundError("LevelOfTheory not found")

    result = _resolve_lowest_sp_conformer_observation(
        session,
        species_entry_id=entry_id,
        lot_id=lot_id,
        calculation_quality=calculation_quality,
    )
    return LowestSPConformerObservationResultRead(
        species_entry_id=entry_id,
        lot_id=lot_id,
        calculation_quality=calculation_quality,
        result=result,
    )


@entries_router.get(
    "/{entry_id}/thermo",
    response_model=list[ThermoRead],
)
def list_thermo_for_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    rows = session.scalars(
        select(Thermo)
        .where(Thermo.species_entry_id == entry_id)
        .order_by(Thermo.id)
    ).all()
    return [ThermoRead.model_validate(r) for r in rows]


@entries_router.get(
    "/{entry_id}/statmech",
    response_model=list[StatmechRead],
)
def list_statmech_for_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    rows = session.scalars(
        select(Statmech)
        .where(Statmech.species_entry_id == entry_id)
        .order_by(Statmech.id)
    ).all()
    return [StatmechRead.model_validate(r) for r in rows]


@entries_router.get(
    "/{entry_id}/transport",
    response_model=list[TransportRead],
)
def list_transport_for_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    rows = session.scalars(
        select(Transport)
        .where(Transport.species_entry_id == entry_id)
        .options(selectinload(Transport.source_calculations))
        .order_by(Transport.id)
    ).all()
    return [TransportRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Species entry reviews
# ---------------------------------------------------------------------------


@entries_router.post(
    "/{species_entry_id}/reviews",
    response_model=SpeciesEntryReviewRead,
    status_code=201,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_species_entry_review_endpoint(
    species_entry_id: int,
    body: SpeciesEntryReviewCreate,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(require_curator_or_admin),
):
    """Append a curation review to a species entry.

    Restricted to ``curator`` and ``admin`` app-user roles via the
    :func:`require_curator_or_admin` dependency. The reviewer is taken from
    the authenticated user — callers cannot supply a reviewer id. Reviews
    are append-only: creating one persists a new row and never mutates a
    prior one.
    """
    review = create_species_entry_review(
        session,
        species_entry_id=species_entry_id,
        user_id=current_user.id,
        role=body.role,
        note=body.note,
    )
    return SpeciesEntryReviewRead.model_validate(review)


@entries_router.get(
    "/{species_entry_id}/reviews",
    response_model=list[SpeciesEntryReviewRead],
)
def list_species_entry_reviews_endpoint(
    species_entry_id: int,
    session: Session = Depends(get_db),
):
    """List reviews for a species entry, newest first."""
    reviews = list_species_entry_reviews(
        session, species_entry_id=species_entry_id
    )
    return [SpeciesEntryReviewRead.model_validate(r) for r in reviews]
