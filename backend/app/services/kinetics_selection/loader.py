"""The kinetics candidate loader: a consistent, normalised snapshot of one reaction entry's population.

Two steps, so a population that is too large is refused before anything heavy is loaded:

1. :func:`scan_population` reads only ids and review states, applies the effective review floor
   and the rejected/deprecated exclusion, and counts what is left. Over the cap it raises the
   coded 422 ``kinetics_selection_population_too_large``; it never returns a prefix.
2. :func:`load_population` loads that population with the eager-load graph the evidence
   evaluation needs and normalises each record.

The never-borrow rule governs what is read. A rate's facts are what it links: its own columns and
declarations, its own determination, its own children and efficiencies. Nothing is taken from a
sibling record of the same reaction entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from tckdb_schemas.kinetics_declarations import (
    StoredKineticsApplicabilityDeclaration,
    StoredKineticsProtocolDeclaration,
)

from app.api.error_contract import CodedValueError
from app.api.errors import not_found
from app.db.models.common import ReactionRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkChannel, NetworkKinetics
from app.db.models.reaction import ReactionEntry, ReactionParticipant
from app.db.models.species import Species
from app.db.models.transition_state import TransitionStateEntry
from app.services.kinetics_selection.models import DeterminationFacts, KineticsRequest, NormalizedKinetics
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.kinetics import KINETICS_TRUST_EAGER_LOADS

CODE_POPULATION_TOO_LARGE = "kinetics_selection_population_too_large"
REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})

_LOAD_OPTIONS = (
    *KINETICS_TRUST_EAGER_LOADS,
    selectinload(Kinetics.plog_entries),
    selectinload(Kinetics.arrhenius_entries),
    selectinload(Kinetics.chebyshev),
    selectinload(Kinetics.falloff),
    selectinload(Kinetics.third_body_efficiencies),
)


@dataclass(frozen=True)
class PopulationScan:
    """Ids and review states of one reaction entry's kinetics rows, partitioned before anything is loaded."""

    reaction_entry_id: int
    effective_statuses: frozenset[RecordReviewStatus]
    population_ids: tuple[int, ...]
    review_status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus, str], ...]
    total_rows: int
    cap: int


@dataclass
class LoadedPopulation:
    """The loaded, normalised population, in id order."""

    entry: ReactionEntry
    rows: list[Kinetics]
    candidates: dict[int, NormalizedKinetics] = field(default_factory=dict)


def scan_population(session: Session, *, reaction_entry_id: int, request: KineticsRequest) -> PopulationScan:
    """Partition the entry's kinetics rows by review state; load nothing else.

    The population is every row whose review status passes the effective floor (the caller's
    ``min_review_status`` combined with the read profile's floor) and is not rejected or
    deprecated. Rows the caller's own floor or the terminal states exclude are listed with why; rows
    the *profile* hides are dropped without trace, so no count or ref reveals them.

    :raises NotFoundError: if the reaction entry does not exist.
    :raises CodedValueError: ``kinetics_selection_population_too_large`` when the visible
        population exceeds ``request.max_candidates``. Nothing is assessed, nothing is chosen from
        a prefix, and the count named is of visible rows only.
    """
    if session.get(ReactionEntry, reaction_entry_id) is None:
        raise not_found("reaction_entry", row_id=reaction_entry_id)
    effective = frozenset(
        visible_statuses(
            min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False
        )
    )
    ids = list(
        session.scalars(select(Kinetics.id).where(Kinetics.reaction_entry_id == reaction_entry_id).order_by(Kinetics.id))
    )
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.kinetics, record_ids=ids)
    # Rows the read profile itself hides (under ``curated``: everything below ``approved``) are not part of
    # this request's world at all: never counted, never listed, never named in a refusal.
    profile_visible = frozenset(
        visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True)
    )
    ids = [i for i in ids if badges[i].status in profile_visible]
    status = {i: badges[i].status for i in ids}
    population = tuple(i for i in ids if status[i] in effective)
    if len(population) > request.max_candidates:
        raise CodedValueError(
            CODE_POPULATION_TOO_LARGE,
            f"{len(population)} visible kinetics records exceed the selection limit of {request.max_candidates}; "
            "nothing was assessed and no record was chosen from a subset.",
            context={"visible_candidates": len(population), "limit": request.max_candidates},
            message_prefix=False,
        )
    excluded = tuple(
        (i, status[i], REASON_TERMINAL if status[i] in _TERMINAL else REASON_BELOW_FLOOR)
        for i in ids
        if status[i] not in effective
    )
    return PopulationScan(
        reaction_entry_id=reaction_entry_id,
        effective_statuses=effective,
        population_ids=population,
        review_status=status,
        excluded=excluded,
        total_rows=len(ids),
        cap=request.max_candidates,
    )


def load_population(session: Session, scan: PopulationScan) -> LoadedPopulation:
    """Load and normalise the scanned population."""
    entry = session.get(ReactionEntry, scan.reaction_entry_id)
    if entry is None:
        raise not_found("reaction_entry", row_id=scan.reaction_entry_id)
    if not scan.population_ids:
        return LoadedPopulation(entry=entry, rows=[])
    rows = list(
        session.scalars(
            select(Kinetics).where(Kinetics.id.in_(scan.population_ids)).options(*_LOAD_OPTIONS).order_by(Kinetics.id)
        ).all()
    )
    return LoadedPopulation(entry=entry, rows=rows, candidates=normalize_rows(session, entry, rows, scan.review_status))


def _declaration(model, raw) -> tuple[str, dict | None]:
    if raw is None:
        return "absent", None
    try:
        return "valid", model.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return "unreadable", None


def _determination_facts(session: Session, ids: set[int]) -> dict[int, DeterminationFacts]:
    if not ids:
        return {}
    rows = session.execute(
        select(
            KineticsDetermination,
            TransitionStateEntry.public_ref,
            Network.public_ref,
            NetworkChannel.channel_key,
        )
        .outerjoin(
            TransitionStateEntry, TransitionStateEntry.id == KineticsDetermination.target_transition_state_entry_id
        )
        .outerjoin(NetworkChannel, NetworkChannel.id == KineticsDetermination.target_network_channel_id)
        .outerjoin(Network, Network.id == NetworkChannel.network_id)
        .where(KineticsDetermination.id.in_(ids))
    ).all()
    return {
        det.id: DeterminationFacts(
            determination_ref=det.public_ref,
            direction=det.direction.value,
            target_kind=det.target_kind.value,
            transition_state_entry_ref=ts_ref,
            network_ref=network_ref,
            channel_key=channel_key,
        )
        for det, ts_ref, network_ref, channel_key in rows
    }


def _network_context(session: Session, ids: set[int]) -> dict[int, tuple[str, str | None]]:
    """``network_kinetics.id`` -> (``network_ref/channel_key``, solve public ref) for network-linked fits."""
    if not ids:
        return {}
    from app.db.models.network_pdep import NetworkSolve

    rows = session.execute(
        select(NetworkKinetics.id, Network.public_ref, NetworkChannel.channel_key, NetworkSolve.public_ref)
        .join(NetworkChannel, NetworkChannel.id == NetworkKinetics.channel_id)
        .join(Network, Network.id == NetworkChannel.network_id)
        .join(NetworkSolve, NetworkSolve.id == NetworkKinetics.solve_id)
        .where(NetworkKinetics.id.in_(ids))
    ).all()
    return {i: (f"{net}/{key}", solve) for i, net, key, solve in rows}


def normalize_rows(
    session: Session,
    entry: ReactionEntry,
    rows: list[Kinetics],
    review_status: dict[int, RecordReviewStatus],
) -> dict[int, NormalizedKinetics]:
    """Normalise loaded rows (children eager-loaded) into assessor inputs; ``id_rank`` follows the given order.

    The rows must be ordered by id for ``id_rank`` to be an id ordinal.
    """
    determinations = _determination_facts(session, {k.determination_id for k in rows if k.determination_id})
    network = _network_context(session, {k.network_kinetics_id for k in rows if k.network_kinetics_id})
    collider_refs: dict[int, str] = {}
    collider_ids = {e.collider_species_id for k in rows for e in k.third_body_efficiencies}
    if collider_ids:
        for species_id, ref in session.execute(
            select(Species.id, Species.public_ref).where(Species.id.in_(collider_ids))
        ):
            collider_refs[species_id] = ref
    reactant_stoichiometries = tuple(
        sorted(
            session.scalars(
                select(ReactionParticipant.stoichiometry).where(
                    ReactionParticipant.reaction_id == entry.reaction_id,
                    ReactionParticipant.role == ReactionRole.reactant,
                )
            )
        )
    )
    candidates: dict[int, NormalizedKinetics] = {}
    for rank, k in enumerate(rows, start=1):
        applicability_state, applicability = _declaration(
            StoredKineticsApplicabilityDeclaration, k.applicability_declaration
        )
        protocol_state, protocol = _declaration(StoredKineticsProtocolDeclaration, k.protocol_declaration)
        chebyshev = None
        if k.chebyshev is not None:
            c = k.chebyshev
            chebyshev = {
                "tmin_k": c.tmin_k, "tmax_k": c.tmax_k, "pmin_bar": c.pmin_bar, "pmax_bar": c.pmax_bar,
                "n_temperature": c.n_temperature, "n_pressure": c.n_pressure,
                "has_coefficients": bool(c.coefficients),
            }
        net_ref, solve_ref = network.get(k.network_kinetics_id, (None, None)) if k.network_kinetics_id else (None, None)
        candidates[k.id] = NormalizedKinetics(
            kinetics_ref=k.public_ref,
            review_status=review_status[k.id],
            created_at=k.created_at,
            id_rank=rank,
            scientific_origin=k.scientific_origin.value,
            model_kind=k.model_kind.value,
            direction=k.direction.value if k.direction is not None else None,
            is_third_body=k.is_third_body,
            tmin_k=k.tmin_k,
            tmax_k=k.tmax_k,
            pressure_context=k.pressure_context.value if k.pressure_context is not None else None,
            pressure_bar=k.pressure_bar,
            a=k.a,
            a_units=k.a_units.value if k.a_units is not None else None,
            degeneracy=k.degeneracy,
            degeneracy_convention=k.degeneracy_convention.value,
            representation_role=k.representation_role.value if k.representation_role is not None else None,
            determination=determinations.get(k.determination_id) if k.determination_id else None,
            applicability_state=applicability_state,
            applicability=applicability,
            protocol_state=protocol_state,
            protocol=protocol,
            arrhenius_terms=len(k.arrhenius_entries),
            plog_pressures_bar=tuple(sorted(p.pressure_bar for p in k.plog_entries)),
            chebyshev=chebyshev,
            has_falloff=k.falloff is not None,
            efficiencies={collider_refs[e.collider_species_id]: e.efficiency for e in k.third_body_efficiencies},
            network_channel_ref=net_ref,
            network_solve_ref=solve_ref,
            reactant_stoichiometries=reactant_stoichiometries,
        )
    return candidates
