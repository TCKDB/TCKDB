"""Species and species-entry read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    PaginationParams,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.api.errors import NotFoundError
from app.api.routes._legacy_reads import (
    EntityListResponse,
    LegacyReadRoute,
    LegacyVisibility,
    disclose_in_header,
    entity_page,
)
from app.db.models.app_user import AppUser
from app.db.models.common import (
    MoleculeKind,
    SubmissionRecordType,
)
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
from app.services.species_entry_review import (
    create_species_entry_review,
    list_species_entry_reviews,
)

router = APIRouter(route_class=LegacyReadRoute)


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


@router.get("", response_model=EntityListResponse[SpeciesRead])
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
    return entity_page(
        [SpeciesRead.model_validate(r) for r in rows],
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

entries_router = APIRouter(route_class=LegacyReadRoute)
#: The write route of the species-entries path: no read profile applies to it.
entries_write_router = APIRouter()


def _species_entry_conformer_summary(
    session: Session, entry_id: int, visibility: LegacyVisibility
) -> SpeciesEntryConformerSummaryRead:
    """Aggregate conformer-group and observation counts for a species entry.

    Two scalar aggregates, so the top-level species-entry read stays cheap. A count answers what the list endpoint
    would show (decision D5): the groups and observations the request shows (the default exclusions and the profile's
    floor), and an observation in a group the floor hides is not counted.
    """
    group_count = session.scalar(
        visibility.restrict(
            select(func.count(ConformerGroup.id)).where(ConformerGroup.species_entry_id == entry_id),
            ConformerGroup.id,
            SubmissionRecordType.conformer_group,
        )
    ) or 0
    observation_count = session.scalar(
        visibility.restrict(
            visibility.restrict(
                select(func.count(ConformerObservation.id))
                .join(ConformerGroup, ConformerGroup.id == ConformerObservation.conformer_group_id)
                .where(ConformerGroup.species_entry_id == entry_id),
                ConformerObservation.id,
                SubmissionRecordType.conformer_observation,
            ),
            ConformerObservation.conformer_group_id,
            SubmissionRecordType.conformer_group,
        )
    ) or 0
    return SpeciesEntryConformerSummaryRead(
        conformer_group_count=group_count,
        conformer_observation_count=observation_count,
    )


@entries_router.get("/{entry_id}", response_model=SpeciesEntryRead)
def get_species_entry(
    entry_id: int, session: Session = Depends(get_db), visibility: LegacyVisibility = Depends()
):
    """Return one species entry with a compact conformer summary.

    The summary is a count-only aggregate. Clients that want to browse the
    actual conformer basins call `/species-entries/{id}/conformer-groups`.
    """
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    # Entries are scoping parents: never gated here (nor on their sub-routes, filters or pointers).
    read = SpeciesEntryRead.model_validate(entry)
    read.conformer_summary = _species_entry_conformer_summary(session, entry_id, visibility)
    return read


@entries_router.get(
    "/{entry_id}/conformer-groups",
    response_model=SpeciesEntryConformerGroupsRead,
)
def list_conformer_groups_for_entry(
    entry_id: int,
    response: Response,
    session: Session = Depends(get_db),
    visibility: LegacyVisibility = Depends(),
):
    """List conformer groups (basins) for a species entry, basin-first.

    Returns group summaries with per-group observation counts and total counts
    aggregated in a single subquery join to avoid N+1 queries. Observations are
    not inlined here — drill into a specific group for detail.
    """
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")

    obs_count_sq = visibility.restrict(
        select(
            ConformerObservation.conformer_group_id.label("group_id"),
            func.count(ConformerObservation.id).label("obs_count"),
        ),
        ConformerObservation.id,
        SubmissionRecordType.conformer_observation,
    )
    obs_count_sq = obs_count_sq.group_by(ConformerObservation.conformer_group_id).subquery()

    disclose_in_header(
        response,
        visibility.hidden(
            session,
            SubmissionRecordType.conformer_group,
            select(ConformerGroup.id).where(ConformerGroup.species_entry_id == entry_id),
        ),
    )
    rows = session.execute(
        visibility.restrict(
            select(ConformerGroup, func.coalesce(obs_count_sq.c.obs_count, 0))
            .outerjoin(obs_count_sq, obs_count_sq.c.group_id == ConformerGroup.id)
            .where(ConformerGroup.species_entry_id == entry_id)
            .options(selectinload(ConformerGroup.selections)),
            ConformerGroup.id,
            SubmissionRecordType.conformer_group,
        ).order_by(ConformerGroup.id)
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
    entry_id: int,
    response: Response,
    session: Session = Depends(get_db),
    visibility: LegacyVisibility = Depends(),
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    matched = (
        select(ConformerObservation.id)
        .join(ConformerGroup, ConformerGroup.id == ConformerObservation.conformer_group_id)
        .where(ConformerGroup.species_entry_id == entry_id)
    )
    disclose_in_header(
        response, visibility.hidden_child(session, SubmissionRecordType.conformer_observation, matched)
    )
    observations = session.scalars(
        visibility.restrict(
            visibility.restrict(
                select(ConformerObservation)
                .join(ConformerGroup, ConformerGroup.id == ConformerObservation.conformer_group_id)
                .where(ConformerGroup.species_entry_id == entry_id),
                ConformerObservation.id,
                SubmissionRecordType.conformer_observation,
            ),
            # An observation is shown only with its group, under the same visibility (PARENT_OF).
            ConformerObservation.conformer_group_id,
            SubmissionRecordType.conformer_group,
        ).order_by(ConformerObservation.id)
    ).all()
    return [ConformerObservationRead.model_validate(o) for o in observations]


@entries_router.get(
    "/{entry_id}/thermo",
    response_model=list[ThermoRead],
)
def list_thermo_for_entry(
    entry_id: int,
    response: Response,
    session: Session = Depends(get_db),
    visibility: LegacyVisibility = Depends(),
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    disclose_in_header(
        response,
        visibility.hidden(
            session, SubmissionRecordType.thermo, select(Thermo.id).where(Thermo.species_entry_id == entry_id)
        ),
    )
    rows = session.scalars(
        visibility.restrict(
            select(Thermo).where(Thermo.species_entry_id == entry_id),
            Thermo.id,
            SubmissionRecordType.thermo,
        ).order_by(Thermo.id)
    ).all()
    return [ThermoRead.model_validate(r) for r in rows]


@entries_router.get(
    "/{entry_id}/statmech",
    response_model=list[StatmechRead],
)
def list_statmech_for_entry(
    entry_id: int,
    response: Response,
    session: Session = Depends(get_db),
    visibility: LegacyVisibility = Depends(),
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    disclose_in_header(
        response,
        visibility.hidden(
            session, SubmissionRecordType.statmech, select(Statmech.id).where(Statmech.species_entry_id == entry_id)
        ),
    )
    rows = session.scalars(
        visibility.restrict(
            select(Statmech).where(Statmech.species_entry_id == entry_id),
            Statmech.id,
            SubmissionRecordType.statmech,
        ).order_by(Statmech.id)
    ).all()
    return [StatmechRead.model_validate(r) for r in rows]


@entries_router.get(
    "/{entry_id}/transport",
    response_model=list[TransportRead],
)
def list_transport_for_entry(
    entry_id: int,
    response: Response,
    session: Session = Depends(get_db),
    visibility: LegacyVisibility = Depends(),
):
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise NotFoundError("SpeciesEntry not found")
    disclose_in_header(
        response,
        visibility.hidden(
            session, SubmissionRecordType.transport, select(Transport.id).where(Transport.species_entry_id == entry_id)
        ),
    )
    rows = session.scalars(
        visibility.restrict(
            select(Transport).where(Transport.species_entry_id == entry_id)
            .options(selectinload(Transport.source_calculations)),
            Transport.id,
            SubmissionRecordType.transport,
        ).order_by(Transport.id)
    ).all()
    return [TransportRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Species entry reviews
# ---------------------------------------------------------------------------


@entries_write_router.post(
    "/{species_entry_id}/reviews",
    response_model=SpeciesEntryReviewRead,
    status_code=201,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_species_entry_review_endpoint(
    species_entry_id: int,
    body: SpeciesEntryReviewCreate,
    session: Session = Depends(get_write_db, scope="function"),
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
