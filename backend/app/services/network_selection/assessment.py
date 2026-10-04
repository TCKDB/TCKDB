"""The deterministic network applicability assessor.

Question asked of one *determination* (channel scope) or one *declared product set* (bundle scope):
can it supply the requested gas-phase, finite-pressure rate coefficient, over the whole requested
temperature and pressure domain, for the requested bath, partition and regime? The answer is one of
``applicable``, ``incompatible``, ``unsupported`` or ``unresolved`` (see
:class:`~app.services.selection_kernel.Applicability`), with every finding that led there. Pure over
the normalised facts; it reads no database.

The order of the checks is the plan's, and each check only adds findings (nothing short-circuits, so a
caller sees every reason):

1. exact network, channel and direction;
2. observable, coefficient basis, partition, boundary and regime compatibility;
3. coefficient units and order;
4. complete determination and representation relationships;
5. whole-request physical coverage (solve scope, representation support and declared validity: their
   *intersection*) and bath applicability;
6. representation integrity and demonstrated invalidation;
7. (review qualification is the loader's: only admitted solves reach this module);
8. comparative protocol evidence, which is disclosed and never excludes.

The rule that runs through every check: **an unstated fact is unknown, never a default.** A null
validity, an undeclared target, a missing temperature bound make a candidate ``unresolved`` rather than
assumed standard; something known to be wrong for the request is ``incompatible``; a form this release
does not evaluate (an additive component, a tabulated interval, a composition-dependent bath) is
``unsupported``. Precedence when several apply: incompatible, unsupported, unresolved.

What the checks do not do: they never extrapolate, never treat overlapping bounds or a summary envelope
as coverage, never reject a fit for a negative fitted term or negative fitted activation energy, never
evaluate a numerical rate, never fill one candidate's missing fact from another, and never sum fits.
"""

from __future__ import annotations

import math
from typing import Any

from tckdb_schemas.network_declarations import (
    NetworkObservable,
    NetworkRegimeKind,
    network_product_set_content_hash,
)

from app.services.network_selection.models import (
    BOUND_ABS_TOL,
    BOUND_REL_TOL,
    MOLE_FRACTION_TOLERANCE,
    BundleAssessment,
    DeterminationAssessment,
    DeterminationFacts,
    FitFacts,
    NetworkFacts,
    NetworkRequest,
    OutputCoverage,
    Reason,
    Scope,
    SolveFacts,
)
from app.services.selection_kernel import Applicability

_ORDER_BY_UNITS = {
    "per_s": 1,
    "cm3_mol_s": 2,
    "cm3_molecule_s": 2,
    "m3_mol_s": 2,
    "cm6_mol2_s": 3,
    "cm6_molecule2_s": 3,
    "m6_mol2_s": 3,
}
_PRECEDENCE = (Applicability.incompatible, Applicability.unsupported, Applicability.unresolved)
#: How far a verdict is from ``applicable`` (the best-of-fits rule picks the smallest).
_SEVERITY = {
    Applicability.applicable: 0,
    Applicability.unresolved: 1,
    Applicability.unsupported: 2,
    Applicability.incompatible: 3,
}
EVIDENCE_KINDS = (
    "convergence",
    "model_fidelity",
    "physical_validation",
    "representation_validation",
    "uncertainty",
    "dependence",
)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=BOUND_REL_TOL, abs_tol=BOUND_ABS_TOL)


def _covers(low: float, high: float, want_low: float, want_high: float) -> bool:
    """The whole requested window inside ``[low, high]`` (to the tolerance); overlap is not coverage."""
    return (want_low >= low or _close(want_low, low)) and (want_high <= high or _close(want_high, high))


class Findings:
    """An ordered list of findings with the precedence rule."""

    def __init__(self) -> None:
        self.reasons: list[Reason] = []

    def add(self, code: str, applicability: Applicability) -> None:
        self.reasons.append(Reason(code, applicability))

    def incompatible(self, code: str) -> None:
        self.add(code, Applicability.incompatible)

    def unsupported(self, code: str) -> None:
        self.add(code, Applicability.unsupported)

    def unresolved(self, code: str) -> None:
        self.add(code, Applicability.unresolved)

    def verdict(self) -> Applicability:
        present = {r.applicability for r in self.reasons}
        return next((a for a in _PRECEDENCE if a in present), Applicability.applicable)


def worst(reasons: list[Reason]) -> Applicability:
    present = {r.applicability for r in reasons}
    return next((a for a in _PRECEDENCE if a in present), Applicability.applicable)


def expected_order(network: NetworkFacts, channel_key: str) -> int | None:
    """The molecularity a channel's coefficient has: the stoichiometry of its source state."""
    channel = network.channel(channel_key)
    state = network.state(channel.source_hash) if channel is not None else None
    if state is None:
        return None
    return sum(stoichiometry for _, stoichiometry in state.participants)


def _domain_inside(want: NetworkRequest, domain: dict[str, float]) -> bool:
    return _covers(domain["temperature_min_k"], domain["temperature_max_k"], want.temperature_min_k, want.temperature_max_k) and _covers(
        domain["pressure_min_bar"], domain["pressure_max_bar"], want.pressure_min_bar, want.pressure_max_bar
    )


def declared_validity(target: dict[str, Any] | None, channel_key: str) -> dict[str, float] | None:
    """The physical validity the producer declared for one output: its own entry, else the solve's."""
    if target is None:
        return None
    for output in target.get("outputs") or ():
        if output["channel_key"] == channel_key and output.get("validity") is not None:
            return output["validity"]
    return target.get("validity")


def _check_identity(det: DeterminationFacts, network: NetworkFacts, request: NetworkRequest, f: Findings) -> None:
    channel = network.channel(det.channel_key)
    if channel is None:
        f.incompatible("channel_not_in_network")
        return
    if request.source_composition_hash is not None and request.scope is Scope.single_channel:
        if (channel.source_hash, channel.sink_hash) != (request.source_composition_hash, request.sink_composition_hash):
            f.incompatible("directed_endpoints_mismatch")


def _check_target(
    det: DeterminationFacts, solve: SolveFacts, network: NetworkFacts, request: NetworkRequest, observable: NetworkObservable, f: Findings
) -> None:
    if det.observable_state != "valid" or det.observable is None:
        f.unresolved("observable_declaration_unreadable")
    else:
        if det.observable["observable"] != observable.value:
            f.incompatible("observable_mismatch")
        if det.observable["coefficient_basis"] != request.coefficient_basis:
            f.incompatible("coefficient_basis_mismatch")
        if request.degeneracy_applied is not None and det.observable["degeneracy_applied"] != request.degeneracy_applied:
            f.incompatible("degeneracy_convention_mismatch")
        order = expected_order(network, det.channel_key)
        if order is not None and det.observable["reaction_order"] != order:
            f.incompatible("order_contradicts_channel_source")
    if solve.target_state == "absent":
        f.unresolved("target_not_declared")
        return
    if solve.target_state == "unreadable" or solve.target is None:
        f.unresolved("target_declaration_unreadable")
        return
    target = solve.target
    partition = target.get("partition")
    if partition is None:
        f.unresolved("partition_not_declared")
    else:
        declared = (
            tuple(sorted(partition["retained"])),
            tuple(sorted(partition["eliminated"])),
            tuple(sorted(tuple(sorted(lump["members"])) for lump in partition["lumps"])),
        )
        if declared != request.partition.normalized():
            f.incompatible("partition_differs")
    declared_boundaries = {b["state_key"]: b["kind"] for b in target.get("boundaries") or ()}
    for state_hash, kind in request.boundaries:
        if state_hash not in declared_boundaries:
            f.unresolved(f"boundary_not_declared:{state_hash[:12]}")
        elif declared_boundaries[state_hash] != kind:
            f.incompatible(f"boundary_differs:{state_hash[:12]}")
    regime = target.get("regime")
    if regime is None:
        f.unresolved("regime_not_declared")
    elif regime["kind"] != request.regime_kind.value:
        f.incompatible("regime_differs")
    elif request.regime_kind is NetworkRegimeKind.initial_population_restricted:
        if sorted(regime["initial_state_keys"]) != sorted(request.initial_state_hashes):
            f.incompatible("initial_population_differs")
    for entry in target.get("outputs") or ():
        if entry["channel_key"] != det.channel_key:
            continue
        if entry["availability"] == "unavailable":
            f.incompatible("output_declared_unavailable")
        elif entry["availability"] == "declared_zero":
            f.incompatible("zero_claim_contradicts_determination")


def _check_bath(solve: SolveFacts, request: NetworkRequest, f: Findings) -> None:
    """The solve's own bath rows are authoritative; a declaration only adds what rows cannot say."""
    declared_scope = (solve.target or {}).get("bath_scope") if solve.target_state == "valid" else None
    if declared_scope == "composition_dependent":
        f.unsupported("composition_dependent_bath_unsupported")
        return
    if not solve.bath:
        f.unresolved("bath_not_stated")
        return
    stored = dict(solve.bath)
    wanted_refs = set(request.bath.species_refs)
    if set(stored) != wanted_refs:
        f.incompatible("bath_species_differ")
        return
    if request.bath.is_mixture:
        assert request.bath.mole_fractions is not None
        for ref, fraction in zip(request.bath.species_refs, request.bath.mole_fractions, strict=True):
            if abs(stored[ref] - fraction) > MOLE_FRACTION_TOLERANCE:
                f.incompatible("bath_composition_differs")
                return
    elif abs(stored[request.bath.species_refs[0]] - 1.0) > MOLE_FRACTION_TOLERANCE:
        f.incompatible("bath_composition_differs")
    if declared_scope == "specified_collider" and request.bath.is_mixture:
        f.incompatible("bath_scope_differs")
    if declared_scope == "fixed_mixture" and not request.bath.is_mixture:
        f.incompatible("bath_scope_differs")


def _check_solve_scope(solve: SolveFacts, request: NetworkRequest, f: Findings) -> None:
    bounds = (solve.tmin_k, solve.tmax_k, solve.pmin_bar, solve.pmax_bar)
    if any(b is None for b in bounds):
        f.unresolved("solve_scope_not_stated")
    elif not _domain_inside(
        request,
        {
            "temperature_min_k": solve.tmin_k,  # type: ignore[dict-item]
            "temperature_max_k": solve.tmax_k,  # type: ignore[dict-item]
            "pressure_min_bar": solve.pmin_bar,  # type: ignore[dict-item]
            "pressure_max_bar": solve.pmax_bar,  # type: ignore[dict-item]
        },
    ):
        f.incompatible("request_outside_solve_scope")


def _check_validity(solve: SolveFacts, channel_key: str, request: NetworkRequest, f: Findings) -> dict[str, float] | None:
    validity = declared_validity(solve.target if solve.target_state == "valid" else None, channel_key)
    if validity is None:
        f.unresolved("physical_validity_not_declared")
    elif not _domain_inside(request, validity):
        f.incompatible("request_outside_declared_validity")
    return validity


def _check_fit(
    fit: FitFacts,
    order: int | None,
    validity: dict[str, float] | None,
    request: NetworkRequest,
) -> tuple[list[Reason], list[str]]:
    """Findings about one fit as a representation: role, integrity, units, support. Plus its blocking failures."""
    f = Findings()
    blocking: list[str] = []
    if fit.representation_role == "additive_component":
        f.unsupported("additive_component_not_a_complete_determination")
    elif fit.representation_role == "overlapping_contribution":
        f.unsupported("overlapping_contribution_not_a_complete_determination")
    if fit.representation_state != "valid":
        f.unresolved("representation_declaration_unreadable")
    kind = fit.model_kind
    units: list[str | None]
    if kind == "chebyshev":
        if fit.chebyshev is None:
            f.incompatible("representation_content_missing")
        elif not fit.chebyshev["intact"]:
            f.incompatible("representation_incomplete")
            blocking.append("chebyshev_coefficients_not_intact")
        if None in (fit.tmin_k, fit.tmax_k, fit.pmin_bar, fit.pmax_bar):
            f.incompatible("chebyshev_mapping_domain_missing")
        elif not _domain_inside(
            request,
            {
                "temperature_min_k": fit.tmin_k,  # type: ignore[dict-item]
                "temperature_max_k": fit.tmax_k,  # type: ignore[dict-item]
                "pressure_min_bar": fit.pmin_bar,  # type: ignore[dict-item]
                "pressure_max_bar": fit.pmax_bar,  # type: ignore[dict-item]
            },
        ):
            f.incompatible("request_outside_fit_support")
        if fit.stores_log10_k is None:
            f.unresolved("stores_log10_k_not_stated")
        if fit.pressure_units is None or fit.temperature_units is None:
            f.unresolved("axis_units_not_stated")
        elif (fit.pressure_units, fit.temperature_units) != ("bar", "kelvin"):
            f.unsupported("axis_units_not_bar_kelvin")
        units = [fit.rate_units]
    elif kind == "plog":
        if not fit.plog_pressures_bar:
            f.incompatible("representation_content_missing")
        else:
            if not fit.plog_finite:
                f.incompatible("non_finite_coefficient")
                blocking.append("plog_coefficients_not_finite")
            low, high = min(fit.plog_pressures_bar), max(fit.plog_pressures_bar)
            # Pressure anchors bound interpolation: the request must sit between them. Never extrapolated.
            if not _covers(low, high, request.pressure_min_bar, request.pressure_max_bar):
                f.incompatible("request_outside_plog_anchors")
        if fit.tmin_k is not None and fit.tmax_k is not None:
            if not _covers(fit.tmin_k, fit.tmax_k, request.temperature_min_k, request.temperature_max_k):
                f.incompatible("request_outside_fit_support")
        elif validity is None:
            # The numerical evaluator treats absent bounds as unbounded; that is not a physical statement.
            f.unresolved("temperature_validity_not_declared")
        units = list(fit.plog_units) if fit.plog_units else [fit.rate_units]
    elif kind == "tabulated":
        point = request.temperature_min_k == request.temperature_max_k and request.pressure_min_bar == request.pressure_max_bar
        if not point:
            # Extrema do not establish interior coverage; interpolation needs a declared, validated implementation.
            f.unsupported("tabulated_interval_unsupported")
        elif not any(
            _close(t, request.temperature_min_k) and _close(p, request.pressure_min_bar) for t, p in fit.point_cells
        ):
            f.incompatible("tabulated_point_absent")
        if not fit.point_values_finite:
            f.incompatible("non_finite_coefficient")
            blocking.append("tabulated_values_not_finite")
        units = [fit.rate_units]
    else:  # pragma: no cover - the enum is closed
        f.unsupported(f"model_kind_unsupported:{kind}")
        units = [fit.rate_units]
    if any(u is None for u in units):
        f.unresolved("rate_units_not_stated")
    else:
        orders = {_ORDER_BY_UNITS[u] for u in units if u is not None}
        if len(orders) > 1:
            f.incompatible("units_orders_inconsistent")
        elif order is not None and orders and next(iter(orders)) != order:
            f.incompatible("rate_units_contradict_order")
    return f.reasons, blocking


def evidence_states(solve: SolveFacts) -> dict[str, str]:
    """The state of each kind of cited evidence: ``declared`` (stated, not verified here) or ``unavailable``.

    ``verified`` and ``contradicted`` need a verification this release does not make, so neither is ever
    reported; a producer's own claim never satisfies a prerequisite that needs verified evidence.
    """
    stated = {entry["kind"] for entry in (solve.validation or {}).get("entries", ())} if solve.validation_state == "valid" else set()
    return {kind: ("declared" if kind in stated else "unavailable") for kind in EVIDENCE_KINDS}


def assess_determination(
    det: DeterminationFacts,
    solve: SolveFacts,
    network: NetworkFacts,
    request: NetworkRequest,
    observable: NetworkObservable,
) -> DeterminationAssessment:
    """Assess one determination of one channel, and every complete representation it holds."""
    shared = Findings()
    _check_identity(det, network, request, shared)
    _check_target(det, solve, network, request, observable, shared)
    _check_bath(solve, request, shared)
    _check_solve_scope(solve, request, shared)
    validity = _check_validity(solve, det.channel_key, request, shared)
    order = expected_order(network, det.channel_key)

    fits = sorted((f for f in solve.fits if f.determination_ref == det.determination_ref), key=lambda f: f.id_rank)
    complete = [f for f in fits if f.representation_role == "complete"]
    advisory: list[str] = []
    per_fit: dict[str, tuple[list[Reason], list[str]]] = {f.fit_ref: _check_fit(f, order, validity, request) for f in fits}
    if not fits:
        shared.unresolved("determination_has_no_fit")
    elif not complete:
        # Only additive or overlapping pieces: no complete representation to select, and nothing is summed.
        shared.add(
            "no_complete_representation",
            Applicability.unsupported,
        )
    best_reasons: list[Reason] = []
    eligible: list[str] = []
    blocking: list[str] = []
    if complete:
        verdicts = {f.fit_ref: worst(per_fit[f.fit_ref][0]) for f in complete}
        eligible = [f.fit_ref for f in complete if verdicts[f.fit_ref] is Applicability.applicable and not per_fit[f.fit_ref][1]]
        best = min(complete, key=lambda f: (_SEVERITY[verdicts[f.fit_ref]], f.id_rank))
        best_reasons = list(per_fit[best.fit_ref][0])
        for f in complete:
            if f.fit_ref != best.fit_ref:
                advisory.extend(f"fit_not_eligible:{f.fit_ref}:{r.code}" for r in per_fit[f.fit_ref][0])
            blocking.extend(f"{f.fit_ref}:{b}" for b in per_fit[f.fit_ref][1])
    for f in fits:
        if f.representation_role != "complete":
            advisory.extend(f"component_not_selected:{f.fit_ref}:{r.code}" for r in per_fit[f.fit_ref][0])
    reasons = [*shared.reasons, *best_reasons]
    if solve.protocol_state == "absent":
        advisory.append("protocol_not_declared")
    elif solve.protocol_state == "unreadable":
        advisory.append("protocol_declaration_unreadable")
    verdict = worst(reasons)
    # Blocking failures exclude a determination only when no complete representation survives them.
    own_blocking = tuple(blocking) if not eligible else ()
    return DeterminationAssessment(
        determination_ref=det.determination_ref,
        solve_ref=solve.solve_ref,
        channel_key=det.channel_key,
        applicability=verdict,
        reasons=tuple(reasons),
        eligible_fit_refs=tuple(eligible) if verdict is Applicability.applicable and not own_blocking else (),
        blocking=own_blocking,
        advisory=tuple(advisory),
        evidence=evidence_states(solve),
    )


def assess_channel_scope(
    solves: tuple[SolveFacts, ...], network: NetworkFacts, request: NetworkRequest
) -> tuple[list[DeterminationAssessment], list[str]]:
    """Assess every determination of the requested channel across the admitted solves.

    Also returns the refs of the channel's fits that state no determination: they belong to no determination,
    so selection cannot say how they relate to the others, and they are disclosed rather than grouped.
    """
    assert request.channel_key is not None and request.observable is not None
    out: list[DeterminationAssessment] = []
    ungrouped: list[str] = []
    for solve in solves:
        for det in solve.determinations:
            if det.channel_key == request.channel_key:
                out.append(assess_determination(det, solve, network, request, request.observable))
        ungrouped.extend(ungrouped_fits(solve, {request.channel_key}))
    return out, ungrouped


def ungrouped_fits(solve: SolveFacts, channel_keys: set[str]) -> list[str]:
    """Fits of the given channels that state no determination: browsable, never grouped, never given one."""
    return [f.fit_ref for f in solve.fits if f.determination_ref is None and f.channel_key in channel_keys]


def _product_sets(solve: SolveFacts) -> list[dict[str, Any]]:
    if solve.target_state != "valid" or solve.target is None:
        return []
    return list(solve.target.get("product_sets") or ())


def assess_bundle_scope(
    solves: tuple[SolveFacts, ...], network: NetworkFacts, request: NetworkRequest
) -> tuple[list[BundleAssessment], list[DeterminationAssessment]]:
    """Assess every declared product set of every admitted solve against the bundle request.

    A node is a declared complete solve and product set, never a combination of whatever fits exist. Every
    required output must be answered inside the node, by a member determination that is itself applicable for the
    whole request, or by a declared zero; nothing is filled from another solve. Returns the node verdicts and the
    member determination verdicts they rest on (each determination once).
    """
    nodes: list[BundleAssessment] = []
    members: dict[str, DeterminationAssessment] = {}
    rank = 0
    for solve in solves:
        by_ref = {d.determination_ref: d for d in solve.determinations}
        target = solve.target if solve.target_state == "valid" else None
        catalog = {e["channel_key"]: e for e in (target or {}).get("outputs") or ()}
        for product_set in _product_sets(solve):
            rank += 1
            f = Findings()
            member_refs: list[str] = []
            covered: dict[str, str] = {}
            pinned = network_product_set_content_hash(
                [(m["determination_ref"], list(m["representation_keys"])) for m in product_set["members"]]
            )
            if pinned != product_set["content_hash"]:
                f.incompatible("product_set_content_mismatch")
            for member in product_set["members"]:
                det = by_ref.get(member["determination_ref"])
                if det is None:
                    f.incompatible("member_not_in_solve")
                    continue
                member_refs.append(det.determination_ref)
                wanted = next((o for o in request.required_outputs() if o.channel_key == det.channel_key), None)
                if wanted is None:
                    continue  # a member beyond the request's outputs does not answer it, and does not harm it
                assessed = assess_determination(det, solve, network, request, wanted.observable)
                if member["representation_keys"]:
                    chosen = {
                        fit.fit_ref
                        for fit in solve.fits
                        if fit.determination_ref == det.determination_ref
                        and fit.representation is not None
                        and fit.representation["key"] in member["representation_keys"]
                    }
                    present = {
                        fit.representation["key"]
                        for fit in solve.fits
                        if fit.determination_ref == det.determination_ref and fit.representation is not None
                    }
                    if set(member["representation_keys"]) - present:
                        f.incompatible(f"member_representation_missing:{det.determination_key}")
                    narrowed = tuple(r for r in assessed.eligible_fit_refs if r in chosen)
                    if assessed.eligible_fit_refs and not narrowed:
                        assessed = DeterminationAssessment(
                            **{**assessed.__dict__, "applicability": Applicability.incompatible, "eligible_fit_refs": (),
                               "reasons": (*assessed.reasons, Reason("no_chosen_representation_is_eligible", Applicability.incompatible))}
                        )
                    else:
                        assessed = DeterminationAssessment(**{**assessed.__dict__, "eligible_fit_refs": narrowed})
                members[det.determination_ref] = assessed
                if assessed.physically_eligible:
                    covered[det.channel_key] = det.determination_ref
                else:
                    for reason in assessed.reasons:
                        f.add(f"member:{det.determination_key}:{reason.code}", reason.applicability)
                    if not assessed.reasons:
                        f.unresolved(f"member:{det.determination_key}:not_eligible")
                    covered[det.channel_key] = det.determination_ref
            coverage: list[OutputCoverage] = []
            for out in request.required_outputs():
                entry = catalog.get(out.channel_key)
                if out.channel_key in covered:
                    ref = covered[out.channel_key]
                    status = "determination"
                    coverage.append(OutputCoverage(out.channel_key, status, ref))
                elif entry is not None and entry["availability"] == "declared_zero":
                    coverage.append(OutputCoverage(out.channel_key, "declared_zero"))
                elif entry is not None and entry["availability"] == "unavailable":
                    coverage.append(OutputCoverage(out.channel_key, "unavailable"))
                    f.incompatible(f"required_output_unavailable:{out.channel_key}")
                elif entry is not None:
                    coverage.append(OutputCoverage(out.channel_key, "missing"))
                    f.incompatible(f"required_output_not_in_product_set:{out.channel_key}")
                else:
                    coverage.append(OutputCoverage(out.channel_key, "not_in_catalog"))
                    f.unresolved(f"required_output_not_in_catalog:{out.channel_key}")
            if request.scope is Scope.full_network:
                _check_closure(solve, catalog, network, request, f)
            nodes.append(
                BundleAssessment(
                    node_ref=f"{solve.solve_ref}/{product_set['product_set_key']}",
                    solve_ref=solve.solve_ref,
                    product_set_key=product_set["product_set_key"],
                    content_hash=product_set["content_hash"],
                    id_rank=rank,
                    applicability=f.verdict(),
                    reasons=tuple(f.reasons),
                    member_refs=tuple(member_refs),
                    coverage=tuple(coverage),
                )
            )
    return nodes, list(members.values())


def _check_closure(
    solve: SolveFacts, catalog: dict[str, dict[str, Any]], network: NetworkFacts, request: NetworkRequest, f: Findings
) -> None:
    """A full-network certification needs an output catalog and every required output in the request.

    The request cannot silently omit a material loss or a required reverse output: every catalog entry marked
    required must be among the requested outputs. Each channel that ends in a state the target declares
    absorbing or open is a boundary loss and must be in the catalog. Where the catalog or the boundary
    semantics are not declared, closure is unresolved, never assumed.
    """
    if not catalog:
        f.unresolved("output_catalog_not_declared")
        return
    requested = {o.channel_key for o in request.required_outputs()}
    for key, entry in sorted(catalog.items()):
        if entry.get("required", True) and key not in requested:
            f.incompatible(f"request_omits_required_output:{key}")
    boundaries = {b["state_key"]: b["kind"] for b in (solve.target or {}).get("boundaries") or ()}
    for channel in network.channels:
        sink_boundary = boundaries.get(channel.sink_hash)
        if sink_boundary in {"absorbing", "open"} and channel.channel_key not in catalog:
            f.unresolved(f"boundary_loss_not_in_catalog:{channel.channel_key}")
    if not boundaries:
        f.unresolved("boundary_semantics_not_declared")
