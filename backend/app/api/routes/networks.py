"""Network read endpoints.

Exposes networks and their pressure-dependent kinetics data through a
graph-shaped, frontend-friendly read API.  All endpoints are strictly
read-only; upload behaviour is not touched.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import DataIntegrityError, NotFoundError
from app.db.models.common import NetworkKineticsModelKind
from app.api.routes._pagination import PaginatedResponse
from app.db.models.network import Network, NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
    NetworkState,
    NetworkStateParticipant,
)
from app.schemas.entities.literature import LiteratureRead
from app.schemas.entities.network_pdep import (
    NetworkKineticsChebyshevRead,
    NetworkKineticsPlogRead,
    NetworkKineticsPointRead,
)
from app.schemas.entities.software import SoftwareReleaseRead
from app.schemas.entities.workflow import WorkflowToolReleaseRead
from app.schemas.reads.network import (
    NetworkChannelRead,
    NetworkDetailRead,
    NetworkKineticsRead,
    NetworkListItemRead,
    NetworkReactionLinkRead,
    NetworkSolveBathGasRead,
    NetworkSolveDetailRead,
    NetworkSolveEnergyTransferRead,
    NetworkSolveListItemRead,
    NetworkSolveSourceCalculationRead,
    NetworkSpeciesLinkRead,
    NetworkStateParticipantRead,
    NetworkStateRead,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_network_or_404(network_id: int, session: Session) -> Network:
    network = session.get(Network, network_id)
    if network is None:
        raise NotFoundError(f"Network {network_id} not found")
    return network


def _get_solve_for_network_or_404(
    network_id: int, solve_id: int, session: Session
) -> NetworkSolve:
    solve = session.get(NetworkSolve, solve_id)
    if solve is None or solve.network_id != network_id:
        raise NotFoundError(
            f"Solve {solve_id} not found for network {network_id}"
        )
    return solve


def _get_channel_for_network_or_404(
    network_id: int, channel_id: int, session: Session
) -> NetworkChannel:
    channel = session.get(NetworkChannel, channel_id)
    if channel is None or channel.network_id != network_id:
        raise NotFoundError(
            f"Channel {channel_id} not found for network {network_id}"
        )
    return channel


def _count_by_network(
    session: Session, table, network_ids: list[int]
) -> dict[int, int]:
    if not network_ids:
        return {}
    rows = session.execute(
        select(table.network_id, func.count())
        .where(table.network_id.in_(network_ids))
        .group_by(table.network_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _count_network_species_distinct(
    session: Session, network_ids: list[int]
) -> dict[int, int]:
    """Distinct species per network — ``network_species`` has ``role`` in its PK."""
    if not network_ids:
        return {}
    rows = session.execute(
        select(
            NetworkSpecies.network_id,
            func.count(func.distinct(NetworkSpecies.species_entry_id)),
        )
        .where(NetworkSpecies.network_id.in_(network_ids))
        .group_by(NetworkSpecies.network_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _count_solve_children(
    session: Session, table, solve_ids: list[int]
) -> dict[int, int]:
    if not solve_ids:
        return {}
    rows = session.execute(
        select(table.solve_id, func.count())
        .where(table.solve_id.in_(solve_ids))
        .group_by(table.solve_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _literature_read(lit) -> LiteratureRead | None:
    return None if lit is None else LiteratureRead.model_validate(lit)


def _software_release_read(sr) -> SoftwareReleaseRead | None:
    return None if sr is None else SoftwareReleaseRead.model_validate(sr)


def _workflow_tool_release_read(wtr) -> WorkflowToolReleaseRead | None:
    return None if wtr is None else WorkflowToolReleaseRead.model_validate(wtr)


def _kinetics_with_payloads(
    session: Session, kinetics_rows: list[NetworkKinetics]
) -> list[NetworkKineticsRead]:
    """Bulk-load per-parameterization child rows and stitch them onto kinetics."""
    if not kinetics_rows:
        return []

    k_ids = [k.id for k in kinetics_rows]

    cheb_rows = session.scalars(
        select(NetworkKineticsChebyshev).where(
            NetworkKineticsChebyshev.network_kinetics_id.in_(k_ids)
        )
    ).all()
    cheb_by_k = {row.network_kinetics_id: row for row in cheb_rows}

    plog_rows = session.scalars(
        select(NetworkKineticsPlog)
        .where(NetworkKineticsPlog.network_kinetics_id.in_(k_ids))
        .order_by(
            NetworkKineticsPlog.pressure_bar.asc(),
            NetworkKineticsPlog.entry_index.asc(),
        )
    ).all()
    plog_by_k: dict[int, list[NetworkKineticsPlog]] = {}
    for row in plog_rows:
        plog_by_k.setdefault(row.network_kinetics_id, []).append(row)

    point_rows = session.scalars(
        select(NetworkKineticsPoint)
        .where(NetworkKineticsPoint.network_kinetics_id.in_(k_ids))
        .order_by(
            NetworkKineticsPoint.temperature_k.asc(),
            NetworkKineticsPoint.pressure_bar.asc(),
        )
    ).all()
    points_by_k: dict[int, list[NetworkKineticsPoint]] = {}
    for row in point_rows:
        points_by_k.setdefault(row.network_kinetics_id, []).append(row)

    result: list[NetworkKineticsRead] = []
    for k in kinetics_rows:
        has_cheb = k.id in cheb_by_k
        plog_list = plog_by_k.get(k.id, [])
        point_list = points_by_k.get(k.id, [])
        _validate_kinetics_subtype(k, has_cheb, plog_list, point_list)

        result.append(
            NetworkKineticsRead(
                id=k.id,
                channel_id=k.channel_id,
                solve_id=k.solve_id,
                model_kind=k.model_kind,
                tmin_k=k.tmin_k,
                tmax_k=k.tmax_k,
                pmin_bar=k.pmin_bar,
                pmax_bar=k.pmax_bar,
                rate_units=k.rate_units,
                pressure_units=k.pressure_units,
                temperature_units=k.temperature_units,
                stores_log10_k=k.stores_log10_k,
                note=k.note,
                created_at=k.created_at,
                chebyshev=(
                    NetworkKineticsChebyshevRead.model_validate(cheb_by_k[k.id])
                    if has_cheb
                    else None
                ),
                plog_entries=[
                    NetworkKineticsPlogRead.model_validate(r) for r in plog_list
                ],
                points=[
                    NetworkKineticsPointRead.model_validate(r) for r in point_list
                ],
            )
        )
    return result


def _validate_kinetics_subtype(
    k: NetworkKinetics,
    has_cheb: bool,
    plog_list: list[NetworkKineticsPlog],
    point_list: list[NetworkKineticsPoint],
) -> None:
    """Enforce that a persisted NetworkKinetics row has exactly the subtype
    payload that matches its ``model_kind``.

    Any mismatch is a scientific-integrity failure on the stored data and
    is raised as :class:`DataIntegrityError` (HTTP 500) so the caller
    never sees silently-truncated partial data.
    """
    has_plog = len(plog_list) > 0
    has_points = len(point_list) > 0
    families_populated = sum([has_cheb, has_plog, has_points])

    mismatch_summary = (
        f"chebyshev={has_cheb}, plog={has_plog}, tabulated={has_points}"
    )
    model_kind_value = k.model_kind.value

    # User-facing detail must not expose the PK — log it for ops diagnosis.
    no_payload_msg = (
        f"Invalid network_kinetics row: model_kind='{model_kind_value}' "
        f"but no matching subtype payload was found"
    )
    mismatch_msg = (
        f"Invalid network_kinetics row: model_kind='{model_kind_value}' "
        f"but subtype payloads present were {mismatch_summary}"
    )

    if families_populated == 0:
        logger.error("%s (network_kinetics.id=%s)", no_payload_msg, k.id)
        raise DataIntegrityError(no_payload_msg)
    if families_populated > 1:
        logger.error("%s (network_kinetics.id=%s)", mismatch_msg, k.id)
        raise DataIntegrityError(mismatch_msg)

    expected_populated = {
        NetworkKineticsModelKind.chebyshev: has_cheb,
        NetworkKineticsModelKind.plog: has_plog,
        NetworkKineticsModelKind.tabulated: has_points,
    }[k.model_kind]
    if not expected_populated:
        logger.error("%s (network_kinetics.id=%s)", mismatch_msg, k.id)
        raise DataIntegrityError(mismatch_msg)


# ---------------------------------------------------------------------------
# GET /networks
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[NetworkListItemRead])
def list_networks(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
):
    total = session.scalar(select(func.count()).select_from(Network)) or 0

    networks = session.scalars(
        select(Network)
        .options(
            joinedload(Network.literature),
            joinedload(Network.software_release),
            joinedload(Network.workflow_tool_release),
        )
        .order_by(Network.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()

    ids = [n.id for n in networks]
    species_counts = _count_network_species_distinct(session, ids)
    reaction_counts = _count_by_network(session, NetworkReaction, ids)
    state_counts = _count_by_network(session, NetworkState, ids)
    channel_counts = _count_by_network(session, NetworkChannel, ids)
    solve_counts = _count_by_network(session, NetworkSolve, ids)

    items = [
        NetworkListItemRead(
            id=n.id,
            name=n.name,
            description=n.description,
            created_at=n.created_at,
            created_by=n.created_by,
            literature_id=n.literature_id,
            software_release_id=n.software_release_id,
            workflow_tool_release_id=n.workflow_tool_release_id,
            literature=_literature_read(n.literature),
            software_release=_software_release_read(n.software_release),
            workflow_tool_release=_workflow_tool_release_read(
                n.workflow_tool_release
            ),
            species_count=species_counts.get(n.id, 0),
            reaction_count=reaction_counts.get(n.id, 0),
            state_count=state_counts.get(n.id, 0),
            channel_count=channel_counts.get(n.id, 0),
            solve_count=solve_counts.get(n.id, 0),
        )
        for n in networks
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


# ---------------------------------------------------------------------------
# GET /networks/{network_id}
# ---------------------------------------------------------------------------


@router.get("/{network_id}", response_model=NetworkDetailRead)
def get_network(network_id: int, session: Session = Depends(get_db)):
    network = session.scalar(
        select(Network)
        .where(Network.id == network_id)
        .options(
            joinedload(Network.literature),
            joinedload(Network.software_release),
            joinedload(Network.workflow_tool_release),
        )
    )
    if network is None:
        raise NotFoundError(f"Network {network_id} not found")

    species_links = session.scalars(
        select(NetworkSpecies)
        .where(NetworkSpecies.network_id == network_id)
        .options(joinedload(NetworkSpecies.species_entry))
        .order_by(NetworkSpecies.species_entry_id, NetworkSpecies.role)
    ).all()

    reaction_links = session.scalars(
        select(NetworkReaction)
        .where(NetworkReaction.network_id == network_id)
        .options(joinedload(NetworkReaction.reaction_entry))
        .order_by(NetworkReaction.reaction_entry_id)
    ).all()

    states = session.scalars(
        select(NetworkState)
        .where(NetworkState.network_id == network_id)
        .options(
            selectinload(NetworkState.participants).joinedload(
                NetworkStateParticipant.species_entry
            )
        )
        .order_by(NetworkState.id)
    ).all()

    channels = session.scalars(
        select(NetworkChannel)
        .where(NetworkChannel.network_id == network_id)
        .order_by(NetworkChannel.id)
    ).all()

    solve_count = (
        session.scalar(
            select(func.count())
            .select_from(NetworkSolve)
            .where(NetworkSolve.network_id == network_id)
        )
        or 0
    )

    return NetworkDetailRead(
        id=network.id,
        name=network.name,
        description=network.description,
        created_at=network.created_at,
        created_by=network.created_by,
        literature_id=network.literature_id,
        software_release_id=network.software_release_id,
        workflow_tool_release_id=network.workflow_tool_release_id,
        literature=_literature_read(network.literature),
        software_release=_software_release_read(network.software_release),
        workflow_tool_release=_workflow_tool_release_read(
            network.workflow_tool_release
        ),
        species=[
            NetworkSpeciesLinkRead.model_validate(link) for link in species_links
        ],
        reactions=[
            NetworkReactionLinkRead.model_validate(link) for link in reaction_links
        ],
        states=[
            NetworkStateRead(
                id=s.id,
                network_id=s.network_id,
                kind=s.kind,
                composition_hash=s.composition_hash,
                label=s.label,
                participants=[
                    NetworkStateParticipantRead.model_validate(p)
                    for p in s.participants
                ],
            )
            for s in states
        ],
        channels=[NetworkChannelRead.model_validate(c) for c in channels],
        solve_count=solve_count,
    )


# ---------------------------------------------------------------------------
# GET /networks/{network_id}/solves
# ---------------------------------------------------------------------------


@router.get(
    "/{network_id}/solves",
    response_model=list[NetworkSolveListItemRead],
)
def list_network_solves(
    network_id: int,
    session: Session = Depends(get_db),
):
    _get_network_or_404(network_id, session)

    solves = session.scalars(
        select(NetworkSolve)
        .where(NetworkSolve.network_id == network_id)
        .options(
            joinedload(NetworkSolve.literature),
            joinedload(NetworkSolve.software_release),
            joinedload(NetworkSolve.workflow_tool_release),
        )
        .order_by(NetworkSolve.id)
    ).all()

    ids = [s.id for s in solves]
    bath_gas_counts = _count_solve_children(session, NetworkSolveBathGas, ids)
    source_calc_counts = _count_solve_children(
        session, NetworkSolveSourceCalculation, ids
    )
    kinetics_counts = _count_solve_children(session, NetworkKinetics, ids)

    return [
        NetworkSolveListItemRead(
            id=s.id,
            network_id=s.network_id,
            created_at=s.created_at,
            created_by=s.created_by,
            literature_id=s.literature_id,
            software_release_id=s.software_release_id,
            workflow_tool_release_id=s.workflow_tool_release_id,
            me_method=s.me_method,
            interpolation_model=s.interpolation_model,
            grain_size_cm_inv=s.grain_size_cm_inv,
            grain_count=s.grain_count,
            emax_kj_mol=s.emax_kj_mol,
            tmin_k=s.tmin_k,
            tmax_k=s.tmax_k,
            pmin_bar=s.pmin_bar,
            pmax_bar=s.pmax_bar,
            note=s.note,
            literature=_literature_read(s.literature),
            software_release=_software_release_read(s.software_release),
            workflow_tool_release=_workflow_tool_release_read(
                s.workflow_tool_release
            ),
            bath_gas_count=bath_gas_counts.get(s.id, 0),
            source_calculation_count=source_calc_counts.get(s.id, 0),
            kinetics_count=kinetics_counts.get(s.id, 0),
        )
        for s in solves
    ]


# ---------------------------------------------------------------------------
# GET /networks/{network_id}/solves/{solve_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{network_id}/solves/{solve_id}",
    response_model=NetworkSolveDetailRead,
)
def get_network_solve(
    network_id: int,
    solve_id: int,
    session: Session = Depends(get_db),
):
    _get_network_or_404(network_id, session)
    solve = session.scalar(
        select(NetworkSolve)
        .where(NetworkSolve.id == solve_id)
        .options(
            joinedload(NetworkSolve.literature),
            joinedload(NetworkSolve.software_release),
            joinedload(NetworkSolve.workflow_tool_release),
        )
    )
    if solve is None or solve.network_id != network_id:
        raise NotFoundError(
            f"Solve {solve_id} not found for network {network_id}"
        )

    bath_gases = session.scalars(
        select(NetworkSolveBathGas)
        .where(NetworkSolveBathGas.solve_id == solve_id)
        .options(joinedload(NetworkSolveBathGas.species_entry))
        .order_by(NetworkSolveBathGas.species_entry_id)
    ).all()

    energy_transfer_rows = session.scalars(
        select(NetworkSolveEnergyTransfer)
        .where(NetworkSolveEnergyTransfer.solve_id == solve_id)
        .order_by(NetworkSolveEnergyTransfer.id)
    ).all()
    if len(energy_transfer_rows) > 1:
        msg = (
            f"Invalid network_solve: expected at most one energy transfer row, "
            f"found {len(energy_transfer_rows)}"
        )
        logger.error("%s (network_solve.id=%s)", msg, solve_id)
        raise DataIntegrityError(msg)
    energy_transfer = (
        NetworkSolveEnergyTransferRead.model_validate(energy_transfer_rows[0])
        if energy_transfer_rows
        else None
    )

    source_calcs = session.scalars(
        select(NetworkSolveSourceCalculation)
        .where(NetworkSolveSourceCalculation.solve_id == solve_id)
        .options(joinedload(NetworkSolveSourceCalculation.calculation))
        .order_by(
            NetworkSolveSourceCalculation.role,
            NetworkSolveSourceCalculation.calculation_id,
        )
    ).all()

    kinetics_rows = session.scalars(
        select(NetworkKinetics)
        .where(NetworkKinetics.solve_id == solve_id)
        .order_by(NetworkKinetics.channel_id, NetworkKinetics.id)
    ).all()
    kinetics = _kinetics_with_payloads(session, kinetics_rows)

    return NetworkSolveDetailRead(
        id=solve.id,
        network_id=solve.network_id,
        created_at=solve.created_at,
        created_by=solve.created_by,
        literature_id=solve.literature_id,
        software_release_id=solve.software_release_id,
        workflow_tool_release_id=solve.workflow_tool_release_id,
        me_method=solve.me_method,
        interpolation_model=solve.interpolation_model,
        grain_size_cm_inv=solve.grain_size_cm_inv,
        grain_count=solve.grain_count,
        emax_kj_mol=solve.emax_kj_mol,
        tmin_k=solve.tmin_k,
        tmax_k=solve.tmax_k,
        pmin_bar=solve.pmin_bar,
        pmax_bar=solve.pmax_bar,
        note=solve.note,
        literature=_literature_read(solve.literature),
        software_release=_software_release_read(solve.software_release),
        workflow_tool_release=_workflow_tool_release_read(
            solve.workflow_tool_release
        ),
        bath_gases=[NetworkSolveBathGasRead.model_validate(bg) for bg in bath_gases],
        energy_transfer=energy_transfer,
        source_calculations=[
            NetworkSolveSourceCalculationRead.model_validate(sc) for sc in source_calcs
        ],
        kinetics=kinetics,
    )


# ---------------------------------------------------------------------------
# GET /networks/{network_id}/channels/{channel_id}/kinetics
# ---------------------------------------------------------------------------


@router.get(
    "/{network_id}/channels/{channel_id}/kinetics",
    response_model=list[NetworkKineticsRead],
)
def list_channel_kinetics(
    network_id: int,
    channel_id: int,
    session: Session = Depends(get_db),
    solve_id: int | None = Query(None),
):
    _get_network_or_404(network_id, session)
    _get_channel_for_network_or_404(network_id, channel_id, session)

    stmt = (
        select(NetworkKinetics)
        .where(NetworkKinetics.channel_id == channel_id)
        .order_by(NetworkKinetics.solve_id, NetworkKinetics.id)
    )
    if solve_id is not None:
        _get_solve_for_network_or_404(network_id, solve_id, session)
        stmt = stmt.where(NetworkKinetics.solve_id == solve_id)

    kinetics_rows = session.scalars(stmt).all()
    return _kinetics_with_payloads(session, kinetics_rows)
