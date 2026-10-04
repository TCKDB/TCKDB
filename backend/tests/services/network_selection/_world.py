"""A small network built with the ORM factories, and helpers to add solves, determinations and fits to it.

The world: states ``ent`` (bimolecular A + B, order 2), ``well`` (W, order 1) and ``exit`` (P + Q); channels
``assoc`` (ent -> well), ``diss`` (well -> ent), ``elim`` and ``elim_alt`` (two pathways well -> exit with the
same endpoints). One network can hold several solves only through these helpers: an upload makes one network
per payload.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tckdb_schemas.network_declarations import (
    StoredNetworkTargetDeclaration,
    network_product_set_content_hash,
)

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkRepresentationRole,
    NetworkSolveKind,
    NetworkStateKind,
    PressureUnit,
    RecordReviewStatus,
    SubmissionRecordType,
    TemperatureUnit,
)
from app.db.models.network_pdep import (
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsDetermination,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolveBathGas,
)
from app.services.network_declaration_resolution import determination_identity_hash
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    attach_network_state_participant,
    make_network,
    make_network_channel,
    make_network_solve,
    make_network_state,
    make_species,
    make_species_entry,
    next_inchi_key,
)

GOOD_COEFFS = [[1.0, 0.5], [0.25, 0.125]]
PLOG_ROWS = [(0.1, 1.0e13, 0.0, 40.0), (1.0, 2.0e13, 0.1, 42.0), (10.0, 3.0e13, 0.2, 44.0)]
ORDER = {"assoc": 2, "diss": 1, "elim": 1, "elim_alt": 1}
UNITS = {2: "cm3_mol_s", 1: "per_s", 3: "cm6_mol2_s"}


def build_world(session) -> SimpleNamespace:
    def entry(smiles: str, tag: str, mult: int = 1):
        return make_species_entry(session, make_species(session, smiles=smiles, multiplicity=mult, inchi_key=next_inchi_key(tag)))

    a, b = entry("[H]", "NSA", 2), entry("C", "NSB")
    w = entry("[CH3]", "NSW", 2)
    p, q = entry("[H][H]", "NSP"), entry("[CH2]", "NSQ", 3)
    ar, he = entry("[Ar]", "NSAR"), entry("[He]", "NSHE")
    network = make_network(session)
    ent = make_network_state(session, network=network, kind=NetworkStateKind.bimolecular, composition_hash="a" * 64)
    well = make_network_state(session, network=network, kind=NetworkStateKind.well, composition_hash="b" * 64)
    exit_ = make_network_state(session, network=network, kind=NetworkStateKind.bimolecular, composition_hash="c" * 64)
    for state, members in ((ent, (a, b)), (well, (w,)), (exit_, (p, q))):
        for member in members:
            attach_network_state_participant(session, state=state, species_entry=member)
    channels = {
        "assoc": make_network_channel(session, network=network, source_state=ent, sink_state=well, kind=NetworkChannelKind.association, channel_key="assoc"),
        "diss": make_network_channel(session, network=network, source_state=well, sink_state=ent, kind=NetworkChannelKind.dissociation, channel_key="diss"),
        "elim": make_network_channel(session, network=network, source_state=well, sink_state=exit_, kind=NetworkChannelKind.dissociation, channel_key="elim"),
        "elim_alt": make_network_channel(session, network=network, source_state=well, sink_state=exit_, kind=NetworkChannelKind.dissociation, channel_key="elim_alt"),
    }
    actor = AppUser(username="ns-curator", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return SimpleNamespace(
        network=network,
        ref=network.public_ref,
        hashes={"ent": ent.composition_hash, "well": well.composition_hash, "exit": exit_.composition_hash},
        channels=channels,
        ar=ar,
        he=he,
        actor=actor,
        solves=[],
    )


def validity(**changes: float) -> dict[str, float]:
    return {
        "temperature_min_k": 300.0,
        "temperature_max_k": 2000.0,
        "pressure_min_bar": 0.01,
        "pressure_max_bar": 100.0,
        **changes,
    }


def target(world, **changes: Any) -> dict[str, Any]:
    """A complete stored target declaration for the world; a keyword replaces a claim (``None`` removes it)."""
    base: dict[str, Any] = {
        "version": 1,
        "claim_origin": "source_publication",
        "partition": {"retained": sorted(world.hashes.values()), "eliminated": [], "lumps": []},
        "boundaries": [{"state_key": world.hashes["exit"], "kind": "absorbing"}],
        "regime": {"kind": "time_independent", "initial_state_keys": []},
        "validity": validity(),
        "bath_scope": "specified_collider",
        "outputs": [],
        "product_sets": [],
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def set_review(session, world, solve, status: RecordReviewStatus) -> None:
    ensure_record_review(session, record_type=SubmissionRecordType.network_solve, record_id=solve.id)
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.network_solve,
        record_id=solve.id,
        status=status,
        actor=world.actor,
    )


def fit_spec(channel: str = "assoc", **changes: Any) -> dict[str, Any]:
    """One fit of a solve. ``det=None`` is a fit that states no determination."""
    base: dict[str, Any] = {
        "channel": channel,
        "det": f"d_{channel}",
        "rep": "plog1",
        "role": "complete",
        "model": "plog",
        "units": UNITS[ORDER[channel]],
        "tmin": 300.0,
        "tmax": 2000.0,
        "pmin": 0.1,
        "pmax": 10.0,
        "plog": PLOG_ROWS,
        "coeffs": GOOD_COEFFS,
        "points": [],
        "observable": {},
        "stores_log10": True,
        "pressure_units": "bar",
        "temperature_units": "kelvin",
    }
    base.update(changes)
    return base


def add_solve(
    session,
    world,
    *,
    fits: list[dict[str, Any]],
    solve_target: Any = "default",
    bath: list[tuple[Any, float]] | None = "default",  # type: ignore[assignment]
    review: RecordReviewStatus | None = RecordReviewStatus.approved,
    kind: NetworkSolveKind = NetworkSolveKind.computed,
    protocol: dict | None = None,
    validation: dict | None = None,
    product_sets: list[dict[str, Any]] | None = None,
    scope: dict[str, float | None] | None = None,
    created_by: int | None = None,
):
    """Add a solve with its determinations and fits to the world's network, and return the solve row.

    ``solve_target`` is ``"default"`` (a complete target), ``None`` (no declaration) or a dict (stored as given,
    with ``product_sets`` appended in stored form when ``product_sets`` is passed: each is
    ``{"key": ..., "members": [(determination key, [representation keys])]}``).
    """
    solve = make_network_solve(
        session,
        network=world.network,
        kind=kind,
        literature_id=None,
        **(scope or {}),
    ) if kind is NetworkSolveKind.computed else _reported_solve(session, world, scope)
    for ref, fraction in ([(world.ar, 1.0)] if bath == "default" else bath or []):
        session.add(NetworkSolveBathGas(solve_id=solve.id, species_entry_id=ref.id, mole_fraction=fraction))
    determinations: dict[str, NetworkKineticsDetermination] = {}
    fit_rows: list[NetworkKinetics] = []
    for spec in fits:
        det_row = None
        if spec["det"] is not None:
            det_row = determinations.get(spec["det"])
            if det_row is None:
                order = ORDER[spec["channel"]]
                observable = {
                    "version": 1,
                    "observable": "product_resolved_coefficient",
                    "coefficient_basis": "kernel",
                    "reaction_order": order,
                    "degeneracy_applied": True,
                    **spec["observable"],
                }
                channel = world.channels[spec["channel"]]
                det_row = NetworkKineticsDetermination(
                    solve_id=solve.id,
                    channel_id=channel.id,
                    determination_key=spec["det"],
                    observable_declaration=observable,
                    identity_hash=determination_identity_hash(
                        solve_id=solve.id,
                        channel_id=channel.id,
                        determination_key=spec["det"],
                        observable=observable,
                    ),
                )
                session.add(det_row)
                session.flush()
                determinations[spec["det"]] = det_row
        fit = NetworkKinetics(
            channel_id=world.channels[spec["channel"]].id,
            solve_id=solve.id,
            model_kind=NetworkKineticsModelKind(spec["model"]),
            tmin_k=spec["tmin"],
            tmax_k=spec["tmax"],
            pmin_bar=spec["pmin"],
            pmax_bar=spec["pmax"],
            rate_units=ArrheniusAUnits(spec["units"]) if spec["units"] else None,
            pressure_units=PressureUnit(spec["pressure_units"]) if spec["pressure_units"] else None,
            temperature_units=TemperatureUnit(spec["temperature_units"]) if spec["temperature_units"] else None,
            stores_log10_k=spec["stores_log10"] if spec["model"] == "chebyshev" else None,
        )
        if det_row is not None:
            fit.determination_id = det_row.id
            fit.representation_role = NetworkRepresentationRole(spec["role"])
            fit.representation_declaration = {"version": 1, "key": spec["rep"], "fit_origin": "solver_output"}
        session.add(fit)
        session.flush()
        if spec["model"] == "plog":
            for pressure, a, n, ea in spec["plog"]:
                session.add(
                    NetworkKineticsPlog(
                        network_kinetics_id=fit.id,
                        pressure_bar=pressure,
                        entry_index=1,
                        a=a,
                        a_units=ArrheniusAUnits(spec["units"]) if spec["units"] else None,
                        n=n,
                        ea_kj_mol=ea,
                    )
                )
        elif spec["model"] == "chebyshev":
            session.add(
                NetworkKineticsChebyshev(
                    network_kinetics_id=fit.id,
                    n_temperature=2,
                    n_pressure=2,
                    coefficients={"coeffs": spec["coeffs"]},
                )
            )
        else:
            for t, p, k in spec["points"]:
                session.add(NetworkKineticsPoint(network_kinetics_id=fit.id, temperature_k=t, pressure_bar=p, rate_value=k))
        fit_rows.append(fit)
    session.flush()

    if solve_target is not None:
        body = target(world) if solve_target == "default" else dict(solve_target)
        if product_sets:
            stored_sets = []
            for declared in product_sets:
                members = [
                    {
                        "determination_ref": determinations[key].public_ref,
                        "representation_keys": list(reps),
                    }
                    for key, reps in declared["members"]
                ]
                stored_sets.append(
                    {
                        "product_set_key": declared["key"],
                        "members": members,
                        "membership_version": 1,
                        "content_hash": network_product_set_content_hash(
                            [(m["determination_ref"], m["representation_keys"]) for m in members]
                        ),
                    }
                )
            body["product_sets"] = stored_sets
        solve.target_declaration = StoredNetworkTargetDeclaration.model_validate(body).model_dump(
            mode="json", exclude_none=True
        )
    if protocol is not None:
        solve.protocol_declaration = protocol
    if validation is not None:
        solve.validation_declaration = validation
    session.flush()
    if review is not None:
        set_review(session, world, solve, review)
    solve._dets = determinations  # type: ignore[attr-defined]
    solve._fits = fit_rows  # type: ignore[attr-defined]
    world.solves.append(solve)
    return solve


def _reported_solve(session, world, scope):
    from tests.services.scientific_read._factories import make_literature

    return make_network_solve(
        session,
        network=world.network,
        kind=NetworkSolveKind.reported,
        literature_id=make_literature(session).id,
        me_method=None,
        **(scope or {}),
    )
