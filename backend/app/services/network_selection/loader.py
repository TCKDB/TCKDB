"""The network candidate loader: a consistent, normalised snapshot of one network's authorized population.

Two steps, so a population that is too large is refused before anything heavy is loaded:

1. :func:`scan_population` resolves the network, then reads only ids, review states and counts. It
   applies the effective review floor and the rejected/deprecated exclusion, counts the *complete*
   authorized population against every bound of the request's versioned bounds, and refuses over any
   of them (``network_selection_population_too_large``). It never returns a prefix, and it counts
   before any applicability filtering or grouping.
2. :func:`load_population` loads that population and normalises it into plain values.

The never-borrow rule governs what is read. A fit's facts are what it links: its own columns, its
own child rows, its own determination and its solve's declarations. Nothing is taken from a sibling
fit, a sibling solve or another network. Truncated browse projections are never used: the selection
reads every child row it needs.

What the caller may learn is bounded by the read profile: a solve the profile hides is never counted,
listed or named, in a refusal or anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import (
    NetworkObservableDeclaration,
    NetworkProtocolDeclaration,
    NetworkRepresentationDeclaration,
    NetworkValidationDeclaration,
    StoredNetworkTargetDeclaration,
)

from app.api.errors import not_found
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsDetermination,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkState,
    NetworkStateParticipant,
)
from app.db.models.species import SpeciesEntry
from app.services.network_selection.bounds import check_bound
from app.services.network_selection.models import (
    ChannelFact,
    DeterminationFacts,
    FitFacts,
    NetworkFacts,
    NetworkRequest,
    Scope,
    SolveFacts,
    StateFact,
)
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.network_declarations import solve_ref_is_visible

#: The exclusion listing is bounded; the total is always reported.
MAX_EXCLUDED_LISTED = 100
REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})


@dataclass(frozen=True)
class PopulationScan:
    """Ids, review states and counts of one network's solves, partitioned before anything is loaded."""

    network_id: int
    network_ref: str
    effective_statuses: frozenset[RecordReviewStatus]
    solve_ids: tuple[int, ...]
    review_status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus, str], ...]
    total_rows: int
    counts: dict[str, int]


def _profile_visible() -> frozenset[RecordReviewStatus]:
    """Statuses the read profile itself allows a caller to know exist (rejected and deprecated included)."""
    return frozenset(visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True))


def scan_population(session: Session, *, request: NetworkRequest) -> PopulationScan:
    """Resolve the network, partition its solves by review state, count against every bound; load nothing else.

    A solve the read profile hides is dropped without trace. A solve the caller's own floor or the terminal
    states exclude is listed with why. The population is every row whose status passes the effective floor
    (the caller's ``min_review_status`` combined with the profile floor) and is neither rejected nor deprecated.

    :raises NotFoundError: the network does not exist.
    :raises CodedValueError: ``network_selection_population_too_large`` naming the bound and the visible count.
    """
    network = session.scalar(select(Network).where(Network.public_ref == request.network_ref))
    if network is None:
        raise not_found("network", ref=request.network_ref)
    bounds = request.bounds
    counts: dict[str, int] = {
        "states": session.scalar(select(func.count()).select_from(NetworkState).where(NetworkState.network_id == network.id))
        or 0,
        "channels": session.scalar(
            select(func.count()).select_from(NetworkChannel).where(NetworkChannel.network_id == network.id)
        )
        or 0,
        "required_outputs": len(request.required_outputs()),
    }
    check_bound(bounds, "states", counts["states"])
    check_bound(bounds, "channels", counts["channels"])
    check_bound(bounds, "required_outputs", counts["required_outputs"])

    effective = frozenset(
        visible_statuses(min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False)
    )
    ids = list(session.scalars(select(NetworkSolve.id).where(NetworkSolve.network_id == network.id).order_by(NetworkSolve.id)))
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.network_solve, record_ids=ids)
    visible = _profile_visible()
    ids = [i for i in ids if badges[i].status in visible]
    status = {i: badges[i].status for i in ids}
    population = tuple(i for i in ids if status[i] in effective)
    counts["solves"] = len(population)
    check_bound(bounds, "solves", counts["solves"])
    excluded = tuple(
        (i, status[i], REASON_TERMINAL if status[i] in _TERMINAL else REASON_BELOW_FLOOR)
        for i in ids
        if status[i] not in effective
    )

    if population:
        counts["kinetics_parents"] = (
            session.scalar(select(func.count()).select_from(NetworkKinetics).where(NetworkKinetics.solve_id.in_(population)))
            or 0
        )
        check_bound(bounds, "kinetics_parents", counts["kinetics_parents"])
        determination_count = (
            session.scalar(
                select(func.count())
                .select_from(NetworkKineticsDetermination)
                .where(NetworkKineticsDetermination.solve_id.in_(population))
            )
            or 0
        )
        if request.scope is Scope.single_channel:
            determination_count = (
                session.scalar(
                    select(func.count())
                    .select_from(NetworkKineticsDetermination)
                    .join(NetworkChannel, NetworkChannel.id == NetworkKineticsDetermination.channel_id)
                    .where(
                        NetworkKineticsDetermination.solve_id.in_(population),
                        NetworkChannel.channel_key == request.channel_key,
                    )
                )
                or 0
            )
        counts["channel_nodes"] = determination_count
        check_bound(bounds, "channel_nodes", counts["channel_nodes"])
        # Declared product sets across every admitted solve: a bundle decision's nodes.
        sets = func.coalesce(
            func.sum(
                func.jsonb_array_length(NetworkSolve.target_declaration["product_sets"]).cast(Float)
            ).filter(func.jsonb_typeof(NetworkSolve.target_declaration["product_sets"]) == "array"),
            0,
        )
        counts["bundle_nodes"] = int(
            session.scalar(select(sets).where(NetworkSolve.id.in_(population), NetworkSolve.target_declaration.is_not(None)))
            or 0
        )
        if request.scope is not Scope.single_channel:
            check_bound(bounds, "bundle_nodes", counts["bundle_nodes"])
        entries = func.coalesce(
            func.sum(func.jsonb_array_length(NetworkSolve.validation_declaration["entries"]).cast(Float)).filter(
                func.jsonb_typeof(NetworkSolve.validation_declaration["entries"]) == "array"
            ),
            0,
        )
        counts["evidence_entries"] = int(
            session.scalar(
                select(entries).where(NetworkSolve.id.in_(population), NetworkSolve.validation_declaration.is_not(None))
            )
            or 0
        )
        check_bound(bounds, "evidence_entries", counts["evidence_entries"])
        cells = (
            int(
                session.scalar(
                    select(func.coalesce(func.sum(NetworkKineticsChebyshev.n_temperature * NetworkKineticsChebyshev.n_pressure), 0))
                    .join(NetworkKinetics, NetworkKinetics.id == NetworkKineticsChebyshev.network_kinetics_id)
                    .where(NetworkKinetics.solve_id.in_(population))
                )
                or 0
            )
            + (
                session.scalar(
                    select(func.count())
                    .select_from(NetworkKineticsPlog)
                    .join(NetworkKinetics, NetworkKinetics.id == NetworkKineticsPlog.network_kinetics_id)
                    .where(NetworkKinetics.solve_id.in_(population))
                )
                or 0
            )
            + (
                session.scalar(
                    select(func.count())
                    .select_from(NetworkKineticsPoint)
                    .join(NetworkKinetics, NetworkKinetics.id == NetworkKineticsPoint.network_kinetics_id)
                    .where(NetworkKinetics.solve_id.in_(population))
                )
                or 0
            )
        )
        counts["numeric_cells"] = cells
        check_bound(bounds, "numeric_cells", cells)
    else:
        counts.update(kinetics_parents=0, channel_nodes=0, bundle_nodes=0, evidence_entries=0, numeric_cells=0)
    return PopulationScan(
        network_id=network.id,
        network_ref=network.public_ref,
        effective_statuses=effective,
        solve_ids=population,
        review_status=status,
        excluded=excluded,
        total_rows=len(ids),
        counts=counts,
    )


def excluded_listing(session: Session, scan: PopulationScan) -> tuple[dict[str, str], ...]:
    """The first ``MAX_EXCLUDED_LISTED`` excluded solves by id, named by public ref; the total is reported separately."""
    listed = scan.excluded[:MAX_EXCLUDED_LISTED]
    if not listed:
        return ()
    refs = dict(
        session.execute(select(NetworkSolve.id, NetworkSolve.public_ref).where(NetworkSolve.id.in_([i for i, _, _ in listed])))
        .tuples()
        .all()
    )
    return tuple({"solve_ref": refs[i], "review_status": s.value, "reason": r} for i, s, r in listed)


def load_network_facts(session: Session, network_id: int, network_ref: str) -> NetworkFacts:
    """The network's states (with the exact content behind each locator) and its directed channels."""
    states = list(session.scalars(select(NetworkState).where(NetworkState.network_id == network_id).order_by(NetworkState.id)))
    participants: dict[int, list[tuple[str, int]]] = {}
    if states:
        for state_id, ref, stoichiometry in session.execute(
            select(NetworkStateParticipant.state_id, SpeciesEntry.public_ref, NetworkStateParticipant.stoichiometry)
            .join(SpeciesEntry, SpeciesEntry.id == NetworkStateParticipant.species_entry_id)
            .where(NetworkStateParticipant.state_id.in_([s.id for s in states]))
        ):
            participants.setdefault(state_id, []).append((ref, stoichiometry))
    hash_by_id = {s.id: s.composition_hash for s in states}
    channels = list(
        session.scalars(select(NetworkChannel).where(NetworkChannel.network_id == network_id).order_by(NetworkChannel.id))
    )
    return NetworkFacts(
        network_ref=network_ref,
        states=tuple(
            StateFact(s.composition_hash, s.kind.value, tuple(sorted(participants.get(s.id, [])))) for s in states
        ),
        channels=tuple(
            ChannelFact(
                c.channel_key, hash_by_id[c.source_state_id], hash_by_id[c.sink_state_id], c.kind.value, c.mechanism.value
            )
            for c in channels
        ),
    )


def _declaration(model: type, raw: object) -> tuple[str, dict | None]:
    if raw is None:
        return "absent", None
    try:
        return "valid", model.model_validate(raw).model_dump(mode="json")  # type: ignore[attr-defined]
    except ValidationError:
        return "unreadable", None


def _fit_content_digest(
    fit: NetworkKinetics, cheb: NetworkKineticsChebyshev | None, plog_rows: list[NetworkKineticsPlog],
    point_rows: list[NetworkKineticsPoint],
) -> str:
    """SHA-256 over everything numeric the export would write for the fit: change a coefficient and it changes."""
    content = {
        "model_kind": fit.model_kind.value,
        "units": [
            fit.rate_units.value if fit.rate_units is not None else None,
            fit.pressure_units.value if fit.pressure_units is not None else None,
            fit.temperature_units.value if fit.temperature_units is not None else None,
        ],
        "stores_log10_k": fit.stores_log10_k,
        "domain": [fit.tmin_k, fit.tmax_k, fit.pmin_bar, fit.pmax_bar],
        "plog": sorted(
            [r.pressure_bar, r.entry_index, r.a, r.a_units.value if r.a_units is not None else None, r.n, r.ea_kj_mol]
            for r in plog_rows
        ),
        "chebyshev": None
        if cheb is None
        else {"n_temperature": cheb.n_temperature, "n_pressure": cheb.n_pressure, "coefficients": cheb.coefficients},
        "points": sorted([r.temperature_k, r.pressure_bar, r.rate_value] for r in point_rows),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _finite_matrix(matrix: object, rows: int, cols: int) -> bool:
    return (
        isinstance(matrix, list)
        and len(matrix) == rows
        and all(isinstance(r, list) and len(r) == cols for r in matrix)
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for r in matrix for v in r)
    )


def _withhold_hidden_references(session: Session, validation: dict | None) -> dict | None:
    """Withhold a validation entry's reference to a solve the caller may not see.

    The captured facts, and so the manifest, must not carry a ref the read profile hides, and the evidence it would
    have supported must not count: the entry keeps its other content, its reference is ``None`` and
    ``reference_withheld`` is true. A reference to a solve that does not exist is treated identically, so the facts
    do not say which it was.
    """
    if validation is None:
        return None
    entries = []
    for entry in validation["entries"]:
        ref = entry.get("reference_solve_ref")
        if ref is not None and not solve_ref_is_visible(session, ref):
            entry = {**entry, "reference_solve_ref": None, "reference_withheld": True}
        entries.append(entry)
    return {**validation, "entries": entries}


def load_population(session: Session, scan: PopulationScan, request: NetworkRequest) -> tuple[NetworkFacts, tuple[SolveFacts, ...]]:
    """Load and normalise the scanned population (and the network's own facts)."""
    network = load_network_facts(session, scan.network_id, scan.network_ref)
    if not scan.solve_ids:
        return network, ()
    solves = list(
        session.scalars(select(NetworkSolve).where(NetworkSolve.id.in_(scan.solve_ids)).order_by(NetworkSolve.id))
    )
    baths: dict[int, list[tuple[str, float]]] = {}
    for solve_id, ref, fraction in session.execute(
        select(NetworkSolveBathGas.solve_id, SpeciesEntry.public_ref, NetworkSolveBathGas.mole_fraction)
        .join(SpeciesEntry, SpeciesEntry.id == NetworkSolveBathGas.species_entry_id)
        .where(NetworkSolveBathGas.solve_id.in_(scan.solve_ids))
    ):
        baths.setdefault(solve_id, []).append((ref, fraction))

    channel_key = {c.id: c.channel_key for c in session.scalars(select(NetworkChannel).where(NetworkChannel.network_id == scan.network_id))}
    determinations = list(
        session.scalars(
            select(NetworkKineticsDetermination)
            .where(NetworkKineticsDetermination.solve_id.in_(scan.solve_ids))
            .order_by(NetworkKineticsDetermination.id)
        )
    )
    det_rank = {d.id: n for n, d in enumerate(determinations, start=1)}
    det_ref = {d.id: d.public_ref for d in determinations}
    fits = list(
        session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id.in_(scan.solve_ids)).order_by(NetworkKinetics.id)
        )
    )
    fit_ids = [f.id for f in fits]
    cheb = {
        c.network_kinetics_id: c
        for c in session.scalars(
            select(NetworkKineticsChebyshev).where(NetworkKineticsChebyshev.network_kinetics_id.in_(fit_ids))
        )
    }
    plog: dict[int, list[NetworkKineticsPlog]] = {}
    for plog_row in session.scalars(
        select(NetworkKineticsPlog)
        .where(NetworkKineticsPlog.network_kinetics_id.in_(fit_ids))
        .order_by(NetworkKineticsPlog.pressure_bar, NetworkKineticsPlog.entry_index)
    ):
        plog.setdefault(plog_row.network_kinetics_id, []).append(plog_row)
    points: dict[int, list[NetworkKineticsPoint]] = {}
    for point_row in session.scalars(
        select(NetworkKineticsPoint)
        .where(NetworkKineticsPoint.network_kinetics_id.in_(fit_ids))
        .order_by(NetworkKineticsPoint.temperature_k, NetworkKineticsPoint.pressure_bar)
    ):
        points.setdefault(point_row.network_kinetics_id, []).append(point_row)

    fit_facts: dict[int, list[FitFacts]] = {}
    for rank, f in enumerate(fits, start=1):
        c = cheb.get(f.id)
        chebyshev = None
        if c is not None:
            matrix = c.coefficients.get("coeffs") if isinstance(c.coefficients, dict) else None
            chebyshev = {
                "n_temperature": c.n_temperature,
                "n_pressure": c.n_pressure,
                "intact": _finite_matrix(matrix, c.n_temperature, c.n_pressure),
            }
        plog_rows = plog.get(f.id, [])
        point_rows = points.get(f.id, [])
        state, representation = _declaration(NetworkRepresentationDeclaration, f.representation_declaration)
        fit_facts.setdefault(f.solve_id, []).append(
            FitFacts(
                fit_ref=f.public_ref,
                id_rank=rank,
                channel_key=channel_key[f.channel_id],
                model_kind=f.model_kind.value,
                tmin_k=f.tmin_k,
                tmax_k=f.tmax_k,
                pmin_bar=f.pmin_bar,
                pmax_bar=f.pmax_bar,
                rate_units=f.rate_units.value if f.rate_units is not None else None,
                pressure_units=f.pressure_units.value if f.pressure_units is not None else None,
                temperature_units=f.temperature_units.value if f.temperature_units is not None else None,
                stores_log10_k=f.stores_log10_k,
                determination_ref=det_ref.get(f.determination_id) if f.determination_id else None,
                representation_role=f.representation_role.value if f.representation_role is not None else None,
                representation=representation,
                representation_state=state,
                chebyshev=chebyshev,
                plog_pressures_bar=tuple(r.pressure_bar for r in plog_rows),
                plog_units=tuple(r.a_units.value if r.a_units is not None else None for r in plog_rows),
                plog_finite=all(math.isfinite(r.a) and math.isfinite(r.n) and math.isfinite(r.ea_kj_mol) for r in plog_rows),
                point_cells=tuple((r.temperature_k, r.pressure_bar) for r in point_rows),
                point_values_finite=all(math.isfinite(r.rate_value) for r in point_rows),
                content_digest=_fit_content_digest(f, c, plog_rows, point_rows),
            )
        )

    det_facts: dict[int, list[DeterminationFacts]] = {}
    for d in determinations:
        state, observable = _declaration(NetworkObservableDeclaration, d.observable_declaration)
        det_facts.setdefault(d.solve_id, []).append(
            DeterminationFacts(
                determination_ref=d.public_ref,
                id_rank=det_rank[d.id],
                determination_key=d.determination_key,
                channel_key=channel_key[d.channel_id],
                observable_state=state,
                observable=observable,
            )
        )

    out = []
    for rank, s in enumerate(solves, start=1):
        target_state, target = _declaration(StoredNetworkTargetDeclaration, s.target_declaration)
        protocol_state, protocol = _declaration(NetworkProtocolDeclaration, s.protocol_declaration)
        validation_state, validation = _declaration(NetworkValidationDeclaration, s.validation_declaration)
        validation = _withhold_hidden_references(session, validation)
        out.append(
            SolveFacts(
                solve_ref=s.public_ref,
                review_status=scan.review_status[s.id],
                created_at=s.created_at,
                id_rank=rank,
                kind=s.kind.value,
                tmin_k=s.tmin_k,
                tmax_k=s.tmax_k,
                pmin_bar=s.pmin_bar,
                pmax_bar=s.pmax_bar,
                bath=tuple(sorted(baths.get(s.id, []))),
                target_state=target_state,
                target=target,
                protocol_state=protocol_state,
                protocol=protocol,
                validation_state=validation_state,
                validation=validation,
                determinations=tuple(det_facts.get(s.id, [])),
                fits=tuple(fit_facts.get(s.id, [])),
            )
        )
    return network, tuple(out)


def resolve_reference_model(session: Session, request: NetworkRequest) -> None:
    """The pinned reference model of a model-fidelity request must be a visible solve of this network.

    A solve the read profile hides, a solve of another network and a ref that names nothing all read the same
    way (404), so the refusal does not tell a caller whether a hidden solve exists.
    """
    if request.reference_model_ref is None:
        return
    row = session.execute(
        select(NetworkSolve.id, Network.public_ref)
        .join(Network, Network.id == NetworkSolve.network_id)
        .where(NetworkSolve.public_ref == request.reference_model_ref)
    ).one_or_none()
    visible = False
    if row is not None and row[1] == request.network_ref:
        badge = fetch_review_badges(session, record_type=SubmissionRecordType.network_solve, record_ids=[row[0]])[row[0]]
        visible = badge.status in _profile_visible()
    if not visible:
        raise not_found("network_solve", ref=request.reference_model_ref)
