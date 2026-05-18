"""Service implementation for the scientific Network / PDep read surface.

One detail surface here; search ships in a sibling module:

- ``GET /scientific/networks/{ref_or_id}`` — one network record.

The Network surface is record-grained at the ``network`` row. Child
tables (``network_species`` / ``network_reaction`` / ``network_state`` /
``network_channel`` / ``network_solve`` / ``network_kinetics``) are
exposed only as bounded embedded summaries under include tokens.
``NetworkSolve`` carries a public_ref (``nsolve_…``) but the standalone
``/scientific/network-solves/{ref}`` detail endpoint is deferred —
the embedded summary covers the read use case.

Network kinetics coefficient payloads (Chebyshev coefficient matrix,
PLOG rows, point triples) are deliberately not inlined under
``include=kinetics``; the summary projection surfaces shape metadata
(``chebyshev_shape``, ``plog_entry_count``, ``point_count``). A
future ``/scientific/network-kinetics/{ref}`` endpoint can surface
the coefficient payloads once ``network_kinetics`` grows a
``public_ref`` column.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    NetworkKineticsModelKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
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
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_network import (
    AvailableNetworkSections,
    NetworkChannelSummary,
    NetworkCoreBlock,
    NetworkEvidenceSummary,
    NetworkKineticsSummary,
    NetworkReactionSummary,
    NetworkReviewEntry,
    NetworkSolveBathGasSummary,
    NetworkSolveSummary,
    NetworkSourceCalculationSummary,
    NetworkSpeciesSummary,
    NetworkStateSummary,
    RequestEcho,
    ScientificNetworkDetailResponse,
    ScientificNetworkRecord,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import resolve_network_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)


_LEGAL_INCLUDE_TOKENS: set[str] = {
    "species",
    "reactions",
    "states",
    "channels",
    "solves",
    "kinetics",
    "source_calculations",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_network(
    session: Session,
    *,
    network_handle: str,
    include: list[str] | None = None,
) -> ScientificNetworkDetailResponse:
    """Resolve a network handle and return its scientific projection."""
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/networks/{network_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    network_id = resolve_network_handle(session, network_handle)
    n = session.get(Network, network_id)
    if n is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"network not found (network_id={network_id})",
            code="handle_not_found",
        )

    badge = _load_review_badge(session, n.id)
    record = build_network_record(
        session, n=n, badge=badge, includes=includes
    )
    return ScientificNetworkDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_network_record(
    session: Session,
    *,
    n: Network,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificNetworkRecord:
    """Project one Network row into the public scientific record shape."""
    # --- bulk-load counts + envelope -------------------------------------
    species_count = _count(
        session, NetworkSpecies, NetworkSpecies.network_id == n.id
    )
    reaction_count = _count(
        session, NetworkReaction, NetworkReaction.network_id == n.id
    )
    state_count = _count(
        session, NetworkState, NetworkState.network_id == n.id
    )
    channel_count = _count(
        session, NetworkChannel, NetworkChannel.network_id == n.id
    )
    solve_rows = session.scalars(
        select(NetworkSolve).where(NetworkSolve.network_id == n.id)
    ).all()
    solve_count = len(solve_rows)
    solve_ids = [s.id for s in solve_rows]
    kinetics_count = (
        int(
            session.scalar(
                select(func.count())
                .select_from(NetworkKinetics)
                .where(NetworkKinetics.solve_id.in_(solve_ids))
            )
            or 0
        )
        if solve_ids
        else 0
    )
    source_calculation_count = (
        int(
            session.scalar(
                select(func.count())
                .select_from(NetworkSolveSourceCalculation)
                .where(NetworkSolveSourceCalculation.solve_id.in_(solve_ids))
            )
            or 0
        )
        if solve_ids
        else 0
    )

    has_cheb, has_plog, has_point = _kinetics_kind_presence(
        session, solve_ids
    )

    # Solve-level T/P envelope union (cheap MIN/MAX aggregate).
    if solve_rows:
        tmins = [s.tmin_k for s in solve_rows if s.tmin_k is not None]
        tmaxs = [s.tmax_k for s in solve_rows if s.tmax_k is not None]
        pmins = [s.pmin_bar for s in solve_rows if s.pmin_bar is not None]
        pmaxs = [s.pmax_bar for s in solve_rows if s.pmax_bar is not None]
        solve_tmin = min(tmins) if tmins else None
        solve_tmax = max(tmaxs) if tmaxs else None
        solve_pmin = min(pmins) if pmins else None
        solve_pmax = max(pmaxs) if pmaxs else None
    else:
        solve_tmin = solve_tmax = solve_pmin = solve_pmax = None

    evidence = NetworkEvidenceSummary(
        species_count=species_count,
        reaction_count=reaction_count,
        state_count=state_count,
        channel_count=channel_count,
        solve_count=solve_count,
        kinetics_count=kinetics_count,
        source_calculation_count=source_calculation_count,
        has_chebyshev=has_cheb,
        has_plog=has_plog,
        has_point_kinetics=has_point,
    )
    available = AvailableNetworkSections(
        has_species=species_count > 0,
        has_reactions=reaction_count > 0,
        has_states=state_count > 0,
        has_channels=channel_count > 0,
        has_solves=solve_count > 0,
        has_kinetics=kinetics_count > 0,
        has_source_calculations=source_calculation_count > 0,
        has_review=_exists_review_for(
            session, SubmissionRecordType.network, n.id
        ),
    )

    core = NetworkCoreBlock(
        network_id=n.id,
        network_ref=n.public_ref,
        name=n.name,
        description=n.description,
        solve_temperature_min_k=solve_tmin,
        solve_temperature_max_k=solve_tmax,
        solve_pressure_min_bar=solve_pmin,
        solve_pressure_max_bar=solve_pmax,
        created_at=n.created_at,
        review=badge,
    )

    sw_summary = _build_software_summary(session, n.software_release_id)
    wf_summary = _build_workflow_summary(session, n.workflow_tool_release_id)
    lit_summary = _build_literature_summary(session, n.literature_id)

    # --- conditional include blocks --------------------------------------
    species_block: list[NetworkSpeciesSummary] | None = None
    if "species" in includes:
        species_block = _build_species(session, n.id)

    reactions_block: list[NetworkReactionSummary] | None = None
    if "reactions" in includes:
        reactions_block = _build_reactions(session, n.id)

    states_block: list[NetworkStateSummary] | None = None
    if "states" in includes:
        states_block = _build_states(session, n.id)

    channels_block: list[NetworkChannelSummary] | None = None
    if "channels" in includes:
        channels_block = _build_channels(session, n.id)

    solves_block: list[NetworkSolveSummary] | None = None
    if "solves" in includes:
        solves_block = _build_solves(session, solve_rows)

    kinetics_block: list[NetworkKineticsSummary] | None = None
    if "kinetics" in includes:
        kinetics_block = _build_kinetics(session, solve_ids)

    sources_block: list[NetworkSourceCalculationSummary] | None = None
    if "source_calculations" in includes:
        sources_block = _build_source_calculations(session, solve_rows)

    review_block: list[NetworkReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(session, n.id)

    return ScientificNetworkRecord(
        network=core,
        software_release=sw_summary,
        workflow_tool_release=wf_summary,
        literature=lit_summary,
        evidence_summary=evidence,
        available_sections=available,
        species=species_block,
        reactions=reactions_block,
        states=states_block,
        channels=channels_block,
        solves=solves_block,
        kinetics=kinetics_block,
        source_calculations=sources_block,
        review_history=review_block,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _count(session: Session, model_cls, where) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model_cls).where(where)
        )
        or 0
    )


def _exists_review_for(
    session: Session, record_type: SubmissionRecordType, record_id: int
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        RecordReview.record_type == record_type,
                        RecordReview.record_id == record_id,
                    )
                )
            )
        )
    )


def _kinetics_kind_presence(
    session: Session, solve_ids: list[int]
) -> tuple[bool, bool, bool]:
    if not solve_ids:
        return False, False, False
    rows = session.execute(
        select(NetworkKinetics.model_kind, func.count(NetworkKinetics.id))
        .where(NetworkKinetics.solve_id.in_(solve_ids))
        .group_by(NetworkKinetics.model_kind)
    ).all()
    by_kind = {r[0]: r[1] for r in rows}
    return (
        by_kind.get(NetworkKineticsModelKind.chebyshev, 0) > 0,
        by_kind.get(NetworkKineticsModelKind.plog, 0) > 0,
        by_kind.get(NetworkKineticsModelKind.tabulated, 0) > 0,
    )


# ---------------------------------------------------------------------------
# Provenance summaries
# ---------------------------------------------------------------------------


def _build_software_summary(
    session: Session, software_release_id: int | None
) -> SoftwareReleaseSummary | None:
    if software_release_id is None:
        return None
    row = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id == software_release_id)
    ).one_or_none()
    if row is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=row.id,
        software_release_ref=row.public_ref,
        software=row.name,
        version=row.version,
    )


def _build_workflow_summary(
    session: Session, workflow_tool_release_id: int | None
) -> WorkflowToolReleaseSummary | None:
    if workflow_tool_release_id is None:
        return None
    row = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
    ).one_or_none()
    if row is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.id,
        workflow_tool_release_ref=row.public_ref,
        workflow_tool=row.name,
        version=row.version,
    )


def _build_literature_summary(
    session: Session, literature_id: int | None
) -> LiteratureSummary | None:
    if literature_id is None:
        return None
    lit = session.get(Literature, literature_id)
    if lit is None:
        return None
    return LiteratureSummary(
        id=lit.id,
        literature_ref=lit.public_ref,
        title=getattr(lit, "title", None),
        year=getattr(lit, "year", None),
        doi=getattr(lit, "doi", None),
    )


# ---------------------------------------------------------------------------
# Include block builders
# ---------------------------------------------------------------------------


def _build_species(
    session: Session, network_id: int
) -> list[NetworkSpeciesSummary]:
    rows = session.execute(
        select(
            NetworkSpecies.species_entry_id,
            SpeciesEntry.public_ref.label("entry_ref"),
            Species.id.label("species_id"),
            Species.public_ref.label("species_ref"),
            Species.smiles,
            Species.inchi_key,
            NetworkSpecies.role,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == NetworkSpecies.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(NetworkSpecies.network_id == network_id)
        .order_by(NetworkSpecies.role.asc(), NetworkSpecies.species_entry_id.asc())
    ).all()
    return [
        NetworkSpeciesSummary(
            species_entry_id=r.species_entry_id,
            species_entry_ref=r.entry_ref,
            species_ref=r.species_ref,
            role=r.role,
            canonical_smiles=r.smiles,
            inchi_key=r.inchi_key,
        )
        for r in rows
    ]


def _build_reactions(
    session: Session, network_id: int
) -> list[NetworkReactionSummary]:
    rows = session.execute(
        select(
            ReactionEntry.id.label("entry_id"),
            ReactionEntry.public_ref.label("entry_ref"),
            ChemReaction.id.label("reaction_id"),
            ChemReaction.public_ref.label("reaction_ref"),
            ChemReaction.reversible,
        )
        .join(
            NetworkReaction,
            NetworkReaction.reaction_entry_id == ReactionEntry.id,
        )
        .join(ChemReaction, ChemReaction.id == ReactionEntry.reaction_id)
        .where(NetworkReaction.network_id == network_id)
        .order_by(ReactionEntry.id.asc())
    ).all()
    return [
        NetworkReactionSummary(
            reaction_entry_id=r.entry_id,
            reaction_entry_ref=r.entry_ref,
            reaction_id=r.reaction_id,
            reaction_ref=r.reaction_ref,
            reversible=r.reversible,
        )
        for r in rows
    ]


def _build_states(
    session: Session, network_id: int
) -> list[NetworkStateSummary]:
    rows = session.scalars(
        select(NetworkState)
        .where(NetworkState.network_id == network_id)
        .order_by(NetworkState.id.asc())
    ).all()
    if not rows:
        return []
    counts_by_state = dict(
        session.execute(
            select(
                NetworkStateParticipant.state_id,
                func.count(NetworkStateParticipant.species_entry_id),
            )
            .where(
                NetworkStateParticipant.state_id.in_([s.id for s in rows])
            )
            .group_by(NetworkStateParticipant.state_id)
        ).all()
    )
    return [
        NetworkStateSummary(
            network_state_id=s.id,
            composition_hash=s.composition_hash,
            kind=s.kind,
            label=s.label,
            participant_count=int(counts_by_state.get(s.id, 0)),
        )
        for s in rows
    ]


def _build_channels(
    session: Session, network_id: int
) -> list[NetworkChannelSummary]:
    rows = session.scalars(
        select(NetworkChannel)
        .where(NetworkChannel.network_id == network_id)
        .order_by(NetworkChannel.id.asc())
    ).all()
    if not rows:
        return []
    state_ids = {r.source_state_id for r in rows} | {
        r.sink_state_id for r in rows
    }
    state_hash_by_id = dict(
        session.execute(
            select(NetworkState.id, NetworkState.composition_hash).where(
                NetworkState.id.in_(state_ids)
            )
        ).all()
    )
    kinetics_by_channel = {
        cid: True
        for (cid,) in session.execute(
            select(NetworkKinetics.channel_id)
            .where(NetworkKinetics.channel_id.in_([r.id for r in rows]))
            .distinct()
        ).all()
    }
    return [
        NetworkChannelSummary(
            network_channel_id=r.id,
            kind=r.kind,
            source_state_id=r.source_state_id,
            sink_state_id=r.sink_state_id,
            source_state_composition_hash=state_hash_by_id.get(
                r.source_state_id, ""
            ),
            sink_state_composition_hash=state_hash_by_id.get(
                r.sink_state_id, ""
            ),
            has_kinetics=bool(kinetics_by_channel.get(r.id, False)),
        )
        for r in rows
    ]


def _build_solves(
    session: Session, solve_rows: list[NetworkSolve]
) -> list[NetworkSolveSummary]:
    if not solve_rows:
        return []
    solve_ids = [s.id for s in solve_rows]
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network_solve,
        record_ids=solve_ids,
    )
    bath_rows = session.execute(
        select(
            NetworkSolveBathGas.solve_id,
            NetworkSolveBathGas.species_entry_id,
            SpeciesEntry.public_ref,
            NetworkSolveBathGas.mole_fraction,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == NetworkSolveBathGas.species_entry_id,
        )
        .where(NetworkSolveBathGas.solve_id.in_(solve_ids))
        .order_by(
            NetworkSolveBathGas.solve_id.asc(),
            NetworkSolveBathGas.species_entry_id.asc(),
        )
    ).all()
    baths_by_solve: dict[int, list[NetworkSolveBathGasSummary]] = {}
    for r in bath_rows:
        baths_by_solve.setdefault(r.solve_id, []).append(
            NetworkSolveBathGasSummary(
                species_entry_id=r.species_entry_id,
                species_entry_ref=r.public_ref,
                mole_fraction=r.mole_fraction,
            )
        )
    et_counts = dict(
        session.execute(
            select(
                NetworkSolveEnergyTransfer.solve_id,
                func.count(NetworkSolveEnergyTransfer.id),
            )
            .where(NetworkSolveEnergyTransfer.solve_id.in_(solve_ids))
            .group_by(NetworkSolveEnergyTransfer.solve_id)
        ).all()
    )
    src_counts = dict(
        session.execute(
            select(
                NetworkSolveSourceCalculation.solve_id,
                func.count(),
            )
            .where(NetworkSolveSourceCalculation.solve_id.in_(solve_ids))
            .group_by(NetworkSolveSourceCalculation.solve_id)
        ).all()
    )
    out: list[NetworkSolveSummary] = []
    for s in solve_rows:
        baths = baths_by_solve.get(s.id, [])
        out.append(
            NetworkSolveSummary(
                network_solve_id=s.id,
                network_solve_ref=s.public_ref,
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
                created_at=s.created_at,
                review=badges.get(
                    s.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                bath_gases=baths,
                bath_gas_count=len(baths),
                energy_transfer_count=int(et_counts.get(s.id, 0)),
                source_calculation_count=int(src_counts.get(s.id, 0)),
            )
        )
    return out


def _build_kinetics(
    session: Session, solve_ids: list[int]
) -> list[NetworkKineticsSummary]:
    if not solve_ids:
        return []
    rows = session.scalars(
        select(NetworkKinetics)
        .where(NetworkKinetics.solve_id.in_(solve_ids))
        .order_by(NetworkKinetics.id.asc())
    ).all()
    if not rows:
        return []
    kinetics_ids = [r.id for r in rows]
    channel_ids = {r.channel_id for r in rows}
    channel_state_hashes = _channel_state_hashes(session, channel_ids)
    # Solve ref lookup.
    solve_ref_by_id = dict(
        session.execute(
            select(NetworkSolve.id, NetworkSolve.public_ref).where(
                NetworkSolve.id.in_(solve_ids)
            )
        ).all()
    )
    # Cheb shapes.
    cheb_rows = session.execute(
        select(
            NetworkKineticsChebyshev.network_kinetics_id,
            NetworkKineticsChebyshev.n_temperature,
            NetworkKineticsChebyshev.n_pressure,
        ).where(
            NetworkKineticsChebyshev.network_kinetics_id.in_(kinetics_ids)
        )
    ).all()
    cheb_by_kid: dict[int, tuple[int, int]] = {
        r[0]: (r[1], r[2]) for r in cheb_rows
    }
    # plog counts.
    plog_counts = dict(
        session.execute(
            select(
                NetworkKineticsPlog.network_kinetics_id,
                func.count(),
            )
            .where(
                NetworkKineticsPlog.network_kinetics_id.in_(kinetics_ids)
            )
            .group_by(NetworkKineticsPlog.network_kinetics_id)
        ).all()
    )
    point_counts = dict(
        session.execute(
            select(
                NetworkKineticsPoint.network_kinetics_id,
                func.count(),
            )
            .where(
                NetworkKineticsPoint.network_kinetics_id.in_(kinetics_ids)
            )
            .group_by(NetworkKineticsPoint.network_kinetics_id)
        ).all()
    )
    out: list[NetworkKineticsSummary] = []
    for r in rows:
        src, sink = channel_state_hashes.get(
            r.channel_id, ("", "")
        )
        cheb_shape = None
        if r.id in cheb_by_kid:
            n_t, n_p = cheb_by_kid[r.id]
            cheb_shape = f"{n_t}x{n_p}"
        out.append(
            NetworkKineticsSummary(
                network_kinetics_id=r.id,
                network_channel_id=r.channel_id,
                network_solve_id=r.solve_id,
                network_solve_ref=solve_ref_by_id.get(r.solve_id),
                channel_source_composition_hash=src,
                channel_sink_composition_hash=sink,
                model_kind=r.model_kind,
                tmin_k=r.tmin_k,
                tmax_k=r.tmax_k,
                pmin_bar=r.pmin_bar,
                pmax_bar=r.pmax_bar,
                plog_entry_count=int(plog_counts.get(r.id, 0)) or None,
                point_count=int(point_counts.get(r.id, 0)) or None,
                chebyshev_shape=cheb_shape,
            )
        )
    return out


def _channel_state_hashes(
    session: Session, channel_ids: set[int]
) -> dict[int, tuple[str, str]]:
    if not channel_ids:
        return {}
    rows = session.execute(
        select(NetworkChannel).where(NetworkChannel.id.in_(channel_ids))
    ).scalars().all()
    state_ids = {r.source_state_id for r in rows} | {
        r.sink_state_id for r in rows
    }
    state_hash_by_id = dict(
        session.execute(
            select(NetworkState.id, NetworkState.composition_hash).where(
                NetworkState.id.in_(state_ids)
            )
        ).all()
    )
    return {
        r.id: (
            state_hash_by_id.get(r.source_state_id, ""),
            state_hash_by_id.get(r.sink_state_id, ""),
        )
        for r in rows
    }


def _build_source_calculations(
    session: Session, solve_rows: list[NetworkSolve]
) -> list[NetworkSourceCalculationSummary]:
    if not solve_rows:
        return []
    solve_ids = [s.id for s in solve_rows]
    solve_ref_by_id = {s.id: s.public_ref for s in solve_rows}
    rows = session.scalars(
        select(NetworkSolveSourceCalculation)
        .where(NetworkSolveSourceCalculation.solve_id.in_(solve_ids))
        .order_by(
            NetworkSolveSourceCalculation.solve_id.asc(),
            NetworkSolveSourceCalculation.role.asc(),
            NetworkSolveSourceCalculation.calculation_id.asc(),
        )
    ).all()
    if not rows:
        return []
    calc_ids = list({r.calculation_id for r in rows})
    calcs = session.scalars(
        select(Calculation).where(Calculation.id.in_(calc_ids))
    ).all()
    calc_by_id = {c.id: c for c in calcs}
    lot_by_id = _bulk_lot_summaries(
        session, {c.lot_id for c in calcs if c.lot_id is not None}
    )
    sw_by_id = _bulk_software_summaries(
        session,
        {c.software_release_id for c in calcs if c.software_release_id is not None},
    )
    wf_by_id = _bulk_workflow_summaries(
        session,
        {
            c.workflow_tool_release_id
            for c in calcs
            if c.workflow_tool_release_id is not None
        },
    )
    out: list[NetworkSourceCalculationSummary] = []
    for r in rows:
        calc = calc_by_id.get(r.calculation_id)
        if calc is None:  # pragma: no cover — race with delete
            continue
        out.append(
            NetworkSourceCalculationSummary(
                role=r.role,
                network_solve_id=r.solve_id,
                network_solve_ref=solve_ref_by_id.get(r.solve_id, ""),
                calculation_id=calc.id,
                calculation_ref=calc.public_ref,
                calculation_type=calc.type,
                level_of_theory=lot_by_id.get(calc.lot_id),
                software_release=sw_by_id.get(calc.software_release_id),
                workflow_tool_release=wf_by_id.get(
                    calc.workflow_tool_release_id
                ),
            )
        )
    return out


def _bulk_lot_summaries(
    session: Session, lot_ids: set[int]
) -> dict[int, LevelOfTheorySummary]:
    if not lot_ids:
        return {}
    rows = session.scalars(
        select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids))
    ).all()
    return {
        lot.id: LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            label=None,
        )
        for lot in rows
    }


def _bulk_software_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, SoftwareReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: SoftwareReleaseSummary(
            software_release_id=row.id,
            software_release_ref=row.public_ref,
            software=row.name,
            version=row.version,
        )
        for row in rows
    }


def _bulk_workflow_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, WorkflowToolReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: WorkflowToolReleaseSummary(
            workflow_tool_release_id=row.id,
            workflow_tool_release_ref=row.public_ref,
            workflow_tool=row.name,
            version=row.version,
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# Review history + badge
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session, network_id: int
) -> list[NetworkReviewEntry]:
    rows = session.scalars(
        select(RecordReview)
        .where(
            RecordReview.record_type == SubmissionRecordType.network,
            RecordReview.record_id == network_id,
        )
        .order_by(RecordReview.reviewed_at.asc().nulls_last())
    ).all()
    return [
        NetworkReviewEntry(
            status=(
                row.status.value
                if hasattr(row.status, "value")
                else str(row.status)
            ),
            reviewed_at=row.reviewed_at,
            reviewed_by=row.reviewed_by,
            note=row.note,
        )
        for row in rows
    ]


def _load_review_badge(session: Session, network_id: int) -> RecordReviewBadge:
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network,
        record_ids=[network_id],
    )
    return badges.get(
        network_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_network_record",
    "get_network",
]
