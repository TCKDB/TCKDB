"""The deterministic kinetics applicability assessor.

Question asked of one kinetics record: can it supply the requested *gas-phase rate coefficient*
(direction, target, coefficient basis, temperature and pressure window, collider)? The answer is
one of ``applicable``, ``incompatible``, ``unsupported`` or ``unresolved`` (see
:class:`~app.services.selection_kernel.Applicability`), with every finding that led there. Pure
over :class:`NormalizedKinetics`; it reads no database.

The rule that runs through every check: **an unstated fact is unknown, never a default.** A null
direction, an undeclared applicability block, a null pressure context on an Arrhenius fit, a
missing temperature bound all make the record ``unresolved`` rather than assumed standard, so a
record that says less never beats one that says more. Something known to be wrong for the request
(another direction, target, basis, pressure, collider, or a window the record does not fully
cover) is ``incompatible``; a form this release does not evaluate (a rate of progress, a net rate,
an additive component of a total) is ``unsupported``. Precedence when several apply:
incompatible, unsupported, unresolved.

What the checks do not do: they never extrapolate, never treat overlapping bounds as coverage,
never reject a record for a negative fitted A or Ea, never evaluate a numerical rate, and never
alter a stored expression (degeneracy is disclosed, not applied).
"""

from __future__ import annotations

import math
from typing import Any

from tckdb_schemas.kinetics_declarations import (
    MOLE_FRACTION_SUM_TOLERANCE,
    KineticsCoefficientBasis,
    KineticsColliderKind,
    KineticsObservable,
    KineticsPressureDependence,
)

from app.db.models.common import KineticsDeterminationTargetKind, ScientificOriginKind
from app.services.kinetics_selection.models import (
    PRESSURE_ABS_TOL,
    PRESSURE_REL_TOL,
    KineticsAssessment,
    KineticsRequest,
    NormalizedKinetics,
    PressureKind,
    Reason,
)
from app.services.selection_kernel import Applicability
from app.services.trust.models import EvidenceEvaluation

_FALLOFF_MODELS = frozenset({"lindemann", "troe", "sri"})
_ARRHENIUS_LIKE = frozenset({"arrhenius", "modified_arrhenius"})
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


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=PRESSURE_REL_TOL, abs_tol=PRESSURE_ABS_TOL)


def _within(low: float, high: float, request_low: float, request_high: float) -> bool:
    """The whole request window inside ``[low, high]`` (to the pressure tolerance); overlap is not coverage."""
    return (request_low >= low or _close(request_low, low)) and (request_high <= high or _close(request_high, high))


class _Findings:
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


def _claimed_pressure_dependence(c: NormalizedKinetics, declared: str | None) -> str | None:
    """What the record says about its pressure meaning: its declaration, else an explicit column, else a
    pressure surface's own structure. ``None`` for a plain Arrhenius-form fit that says nothing."""
    if declared is not None:
        return declared
    by_context = {
        "high_p_limit": KineticsPressureDependence.high_pressure_limit.value,
        "apparent_at_pressure": KineticsPressureDependence.fixed_pressure.value,
        "pressure_dependent": KineticsPressureDependence.pressure_dependent.value,
    }
    if c.pressure_context in by_context:
        return by_context[c.pressure_context]
    if c.model_kind in _FALLOFF_MODELS or c.model_kind in {"plog", "chebyshev"}:
        return KineticsPressureDependence.pressure_dependent.value
    return None


def _check_representation(c: NormalizedKinetics, f: _Findings) -> None:
    kind = c.model_kind
    if kind in _ARRHENIUS_LIKE and c.a is None:
        f.incompatible("representation_content_missing")
    elif kind == "multi_arrhenius" and c.arrhenius_terms < 1:
        f.incompatible("representation_content_missing")
    elif kind == "plog" and not c.plog_pressures_bar:
        f.incompatible("representation_content_missing")
    elif kind == "chebyshev" and not (c.chebyshev and c.chebyshev["has_coefficients"]):
        f.incompatible("representation_content_missing")
    elif kind in _FALLOFF_MODELS and not (c.has_falloff and c.a is not None):
        f.incompatible("representation_content_missing")
    elif kind in _FALLOFF_MODELS and c.falloff is not None and not _falloff_complete(kind, c.falloff):
        f.incompatible("representation_content_missing")


#: The falloff parameters each model needs besides the high-pressure Arrhenius line (Troe's T2 and SRI's d and e
#: are optional in the model).
_FALLOFF_REQUIRED = {
    "lindemann": ("low_a",),
    "troe": ("low_a", "troe_alpha", "troe_t3", "troe_t1"),
    "sri": ("low_a", "sri_a", "sri_b", "sri_c"),
}


def _falloff_complete(kind: str, falloff: dict[str, Any]) -> bool:
    return all(falloff.get(name) is not None for name in _FALLOFF_REQUIRED[kind])


def _check_units(c: NormalizedKinetics, declared_order: int | None, f: _Findings) -> None:
    """Every term, entry and block of the representation has its units; the orders agree with each other and with
    the declared order. Missing units are ``unresolved``; mixed orders, or an order other than the declared one,
    are ``incompatible``. A falloff's low-pressure units are one order higher than its high-pressure line.

    Also stated here because it surprised a reviewer once: an established pressure independence answers a
    high-pressure-limit request as well as a finite one (see :func:`_check_pressure`); the reverse is not true."""
    kind = c.model_kind
    if kind == "multi_arrhenius":
        units = list(c.arrhenius_units)
    elif kind == "plog":
        units = list(c.plog_units)
    else:
        units = [c.a_units]
    if not units:
        return  # a multi-Arrhenius or PLOG fit with nothing in it: reported as missing content, not as missing units
    if any(u is None for u in units):
        f.unresolved("a_units_not_recorded")
        return
    orders = {_ORDER_BY_UNITS[u] for u in units if u is not None}
    if len(orders) > 1:
        f.incompatible("units_orders_inconsistent")
        return
    (order,) = orders
    if declared_order is not None and order != declared_order:
        f.incompatible("reaction_order_units_mismatch")
    if kind in _FALLOFF_MODELS and c.falloff is not None:
        low = c.falloff.get("low_a_units")
        if low is None:
            f.unresolved("low_pressure_units_not_recorded")
        elif _ORDER_BY_UNITS[low] != order + 1:
            f.incompatible("falloff_low_pressure_order_inconsistent")


def _check_identity(c: NormalizedKinetics, request: KineticsRequest, f: _Findings) -> None:
    det = c.determination
    if det is None:
        f.unresolved("determination_not_declared")
    elif c.representation_role == "additive_component":
        f.unsupported("additive_component_not_total_rate")
    if c.direction is None:
        f.unresolved("direction_not_recorded")
    elif c.direction == "net":
        f.unsupported("net_rate_unsupported")
    elif c.direction != request.direction.value:
        f.incompatible("direction_mismatch")
    if det is None:
        return
    if det.direction != request.direction.value:
        f.incompatible("determination_direction_mismatch")
    target = request.target
    if det.target_kind != target.kind.value:
        f.incompatible("target_mismatch")
    elif target.kind is KineticsDeterminationTargetKind.resolved_channel:
        if target.transition_state_entry_ref is not None:
            if det.transition_state_entry_ref != target.transition_state_entry_ref:
                f.incompatible("target_mismatch")
        elif (det.network_ref, det.channel_key) != (target.network_ref, target.channel_key):
            f.incompatible("target_mismatch")
    if c.network_channel_ref is not None and det.network_ref is not None:
        if c.network_channel_ref != f"{det.network_ref}/{det.channel_key}":
            f.incompatible("network_channel_mismatch")


def _check_applicability_block(
    c: NormalizedKinetics, request: KineticsRequest, f: _Findings, advisory: list[str]
) -> dict[str, Any] | None:
    """The declared applicability claims against the request; returns the block when readable."""
    if c.applicability_state == "absent":
        f.unresolved("applicability_not_declared")
        return None
    if c.applicability_state == "unreadable":
        f.unresolved("applicability_unreadable")
        return None
    block = c.applicability or {}
    phase = block.get("phase")
    if phase is None:
        f.unresolved("phase_not_declared")
    elif phase != "gas":
        f.incompatible("phase_mismatch")
    observable = block.get("observable")
    if observable is None:
        f.unresolved("observable_not_declared")
    elif observable != KineticsObservable.rate_coefficient.value:
        f.unsupported(f"observable_unsupported:{observable}")
    scope = block.get("scope")
    if scope is not None and scope != request.target.kind.value:
        f.incompatible("scope_mismatch")
    basis = block.get("coefficient_basis")
    if basis is None:
        f.unresolved("coefficient_basis_not_declared")
    elif basis != request.coefficient_basis.value:
        f.incompatible("coefficient_basis_mismatch")
    elif c.is_third_body != (basis == KineticsCoefficientBasis.third_body_kernel.value):
        f.incompatible("third_body_form_contradicts_basis")
    order = block.get("reaction_order")
    _check_units(c, order, f)
    if order is None:
        advisory.append("reaction_order_not_declared")
    # The coefficient is a rate *of* the side its direction names: a forward coefficient normalises on the
    # reactants, a reverse one on the products. Only the canonical convention (rate of reaction progress) is
    # assessed; another (a reactant-loss rate) is a different coefficient for a species counted twice, and v1
    # converts nothing.
    side = c.reactant_stoichiometries if request.direction.value == "forward" else c.product_stoichiometries
    if any(s > 1 for s in side):
        convention = block.get("rate_progress_convention")
        if convention is None:
            f.unresolved("rate_progress_convention_not_declared")
        elif convention != "reaction_progress":
            f.unsupported(f"rate_progress_convention_unsupported:{convention}")
    return block


def _check_temperature(c: NormalizedKinetics, request: KineticsRequest, f: _Findings) -> None:
    bounds = [(c.tmin_k, c.tmax_k)]
    if c.chebyshev is not None:
        bounds.append((c.chebyshev["tmin_k"], c.chebyshev["tmax_k"]))
    lows: list[float] = []
    highs: list[float] = []
    for low, high in bounds:
        if low is None or high is None:
            f.unresolved("temperature_domain_not_recorded")
            return
        lows.append(low)
        highs.append(high)
    if not (max(lows) <= request.temperature_min_k and request.temperature_max_k <= min(highs)):
        f.incompatible("temperature_range_not_covered")


def _pressure_support(c: NormalizedKinetics, block: dict[str, Any] | None, f: _Findings) -> tuple[float, float] | None:
    """The pressure window a pressure-dependent record supports: its own table or bounds, intersected with
    any declared validity domain. ``None`` (with an ``unresolved`` finding) when it cannot be established.

    A PLOG fit with no declared validity domain covers exactly its anchor range (its lowest to its highest
    stored pressure), and a Chebyshev fit its recorded bounds; a declared domain can only narrow that, never
    widen it. A falloff fit has no table bounding it, so only a declared domain gives it a window."""
    declared = None
    if block is not None and block.get("pressure_domain_min_bar") is not None:
        declared = (block["pressure_domain_min_bar"], block["pressure_domain_max_bar"])
    if c.model_kind == "plog":
        support = (min(c.plog_pressures_bar), max(c.plog_pressures_bar)) if c.plog_pressures_bar else None
    elif c.model_kind == "chebyshev":
        ch = c.chebyshev or {}
        support = (ch["pmin_bar"], ch["pmax_bar"]) if ch.get("pmin_bar") is not None and ch.get("pmax_bar") is not None else None
        if support is None:
            f.unresolved("pressure_domain_not_recorded")
            return None
    else:
        # Falloff and other model-carried dependence: only a declared domain bounds it.
        if declared is None:
            f.unresolved("pressure_domain_not_declared")
            return None
        return declared
    if support is None:
        return None
    if declared is not None:
        support = (max(support[0], declared[0]), min(support[1], declared[1]))
    return support


def _check_pressure(
    c: NormalizedKinetics, request: KineticsRequest, block: dict[str, Any] | None, f: _Findings, advisory: list[str]
) -> None:
    declared = block.get("pressure_dependence") if block is not None else None
    claimed = _claimed_pressure_dependence(c, declared)
    kind = request.pressure.kind
    if kind is PressureKind.independent:
        if claimed is None:
            f.unresolved("pressure_dependence_not_declared")
        elif claimed != KineticsPressureDependence.independent.value:
            f.incompatible("pressure_dependence_mismatch")
        return
    if kind is PressureKind.high_pressure_limit:
        if claimed is None:
            f.unresolved("pressure_dependence_not_declared")
        elif claimed not in (
            KineticsPressureDependence.high_pressure_limit.value,
            KineticsPressureDependence.independent.value,
        ):
            f.incompatible("pressure_dependence_mismatch")
        return
    pmin, pmax = request.pressure.min_bar, request.pressure.max_bar
    assert pmin is not None and pmax is not None
    if claimed is None:
        f.unresolved("pressure_dependence_not_declared")
        return
    if claimed == KineticsPressureDependence.independent.value:
        return  # established pressure independence answers a finite pressure as it answers every other
    if claimed == KineticsPressureDependence.fixed_pressure.value:
        if c.pressure_bar is None:
            f.unresolved("fixed_pressure_not_recorded")
        elif not request.pressure.is_point or not _close(c.pressure_bar, pmin):
            f.incompatible("fixed_pressure_mismatch")
        return
    if claimed != KineticsPressureDependence.pressure_dependent.value:
        f.incompatible("pressure_dependence_mismatch")
        return
    support = _pressure_support(c, block, f)
    if support is not None and not _within(support[0], support[1], pmin, pmax):
        f.incompatible("pressure_range_not_covered")
    if c.network_solve_ref is not None:
        advisory.append(f"network_solve:{c.network_solve_ref}")


def _check_collider(
    c: NormalizedKinetics, request: KineticsRequest, block: dict[str, Any] | None, f: _Findings, claimed: str | None
) -> None:
    if not request.collider_is_material:
        return
    asked = request.collider
    assert asked is not None
    kind = block.get("collider_kind") if block is not None else None
    composition_effective = request.coefficient_basis is KineticsCoefficientBasis.composition_effective_coefficient
    if (
        claimed == KineticsPressureDependence.independent.value
        and not composition_effective
        and kind not in (KineticsColliderKind.specified_collider.value, KineticsColliderKind.fixed_mixture.value)
    ):
        # A pressure-independent coefficient does not depend on the bath gas, so it needs no collider statement;
        # only a collider it names (a specified one, or a fixed mixture) has to be the one asked for.
        return
    if kind is None:
        f.unresolved("collider_not_declared")
        return
    assert block is not None  # a kind is only ever read from a block
    declared = block.get("colliders") or []
    refs = tuple(d["species_ref"] for d in declared)
    if kind == KineticsColliderKind.not_dependent.value:
        if composition_effective:
            f.incompatible("collider_mismatch")
        return
    if kind == KineticsColliderKind.specified_collider.value:
        if asked.is_mixture or refs != asked.species_refs:
            f.incompatible("collider_mismatch")
        return
    if kind == KineticsColliderKind.fixed_mixture.value:
        if not asked.is_mixture or set(refs) != set(asked.species_refs):
            f.incompatible("collider_mismatch")
            return
        recorded = {d["species_ref"]: d["mole_fraction"] for d in declared}
        if any(abs(recorded[r] - m) > MOLE_FRACTION_SUM_TOLERANCE for r, m in zip(asked.species_refs, asked.mole_fractions or (), strict=False)):
            f.incompatible("collider_mismatch")
        return
    # composition_dependent: its own efficiencies stand in for the collider. It is not an already-evaluated
    # effective coefficient, so it cannot answer one.
    if composition_effective:
        f.incompatible("collider_mismatch")
        return
    if block.get("default_third_body_efficiency") is None and any(r not in c.efficiencies for r in asked.species_refs):
        f.unresolved("third_body_efficiency_missing_for_collider")


def _check_degeneracy(c: NormalizedKinetics, f: _Findings, advisory: list[str]) -> None:
    if c.degeneracy is None or c.degeneracy == 1:
        return
    if c.degeneracy_convention == "unknown":
        f.unresolved("degeneracy_convention_unknown")
    elif c.degeneracy_convention == "not_applied":
        f.unresolved("degeneracy_not_applied")
    else:
        advisory.append("degeneracy_already_applied")


def assess_candidate(
    c: NormalizedKinetics, *, request: KineticsRequest, evidence: EvidenceEvaluation | None
) -> KineticsAssessment:
    """Assess one record for the request. Pure; see the module docstring for what each verdict means.

    :param evidence: The computed-kinetics evidence evaluation, or ``None`` when the rubric does not apply
        (an experimental or estimated rate needs no transition-state chain).
    """
    f = _Findings()
    advisory: list[str] = []
    _check_identity(c, request, f)
    block = _check_applicability_block(c, request, f, advisory)
    _check_representation(c, f)
    _check_temperature(c, request, f)
    _check_pressure(c, request, block, f, advisory)
    _check_collider(
        c, request, block, f, _claimed_pressure_dependence(c, block.get("pressure_dependence") if block else None)
    )
    _check_degeneracy(c, f, advisory)

    blocking: list[str] = []
    if evidence is None:
        if c.scientific_origin != ScientificOriginKind.computed.value:
            advisory.append(f"evidence_rubric_not_applicable:{c.scientific_origin}")
    else:
        if evidence.hard_fail_reason is not None:
            blocking.append(f"evidence_hard_failed:{evidence.hard_fail_reason.value}")
        advisory.append(f"evidence_label:{evidence.label.value}")
        for name, outcome in evidence.checks.items():
            if outcome.value in {"missing", "warning"}:
                advisory.append(f"evidence_check_{outcome.value}:{name}")
    return KineticsAssessment(
        kinetics_ref=c.kinetics_ref,
        applicability=f.verdict(),
        reasons=tuple(f.reasons),
        blocking=tuple(blocking),
        advisory=tuple(advisory),
    )
