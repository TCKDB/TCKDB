"""Recomputing an assembled composite's total from its inputs (ADR 0021, P5).

An assembled composite's deposited ``electronic_energy_hartree`` is *checked*
here and never replaced: TCKDB forms the sum below only to compare it with the
number the producer sent, returns nothing from it that is stored, and writes it
nowhere (owner decision 5). The functions take plain numbers, so the backend, the
client and a producer's own pre-flight check run the same arithmetic.

The total of a scheme is the **sum of its terms**, and a term's value is:

* ``base`` / ``value`` -- the input's energy component;
* ``extrapolation`` -- the formula's limit over the inputs' component at their
  cardinal numbers (:mod:`tckdb_schemas.composite_formulas`);
* ``difference`` -- the ``high`` input's component minus the ``low`` input's.

What "the input's component" is
-------------------------------
An input is a single point (its ``electronic_energy_hartree``) or an optimisation
(its ``final_energy_hartree``, which is the single-point value at the
optimisation's own level). ``total`` is that energy. The other components are the
single point's stored ``calc_sp_energy_component`` rows; an optimisation has none.

**Correlation and the triples convention (pinned here, ADR 0021).** Single-point
components allow two conventions when a ``triples`` part is also stored (see
:mod:`tckdb_schemas.sp_energy_components`): ORCA's correlation energy already
includes (T) (``reference + correlation = energy``); Molpro's is the CCSD part and
(T) is separate (``reference + correlation + triples = energy``). A scheme term
can ask for either meaning, by naming the component:

* ``correlation`` is the **whole** correlation energy, (T) included: the stored
  ``correlation`` under the ORCA convention, ``correlation + triples`` under the
  Molpro one;
* ``correlation_excluding_triples`` is the CCSD part: the stored ``correlation``
  under the Molpro convention, ``correlation - triples`` under the ORCA one.

The textbook scheme (extrapolate the CCSD correlation energy, add (T) from a
smaller basis as its own ``triples`` term) is written with
``correlation_excluding_triples`` and a ``triples`` term, and counts (T) once
whichever program produced each input. ``correlation_excluding_triples`` is
derived, so it is never a stored single-point component.

The convention is read off the stored row, never assumed: whichever of the two
sums equals the stored energy within the single-point tolerance
(:data:`~tckdb_schemas.sp_energy_components.SUM_TOLERANCE_HARTREE`, 1e-6 Eh) is
the row's convention, and |(T)| is far larger than that tolerance. If both sums
match (|(T)| below the tolerance) the stored ``correlation`` is used for either
meaning. If neither does, or the reference or the energy is not stated, the
convention cannot be determined and the total is *unverifiable*, never guessed;
so is a ``correlation_excluding_triples`` read under the ORCA convention from a
row that stores no ``triples`` to subtract. A term that reads ``triples`` reads
the stored component as it is.

The tolerance
-------------
``max(1e-6, 5e-7 * (1 + sum_i |d total / d x_i|))`` hartree
(:func:`composite_arithmetic_tolerance_hartree`), the sum running over every
stored number ``x_i`` the recomputation consumed. The weight of a number is how far
a rounding error in it can move the total: 1 for a value or base input and for
either side of a difference, the extrapolation's own weight for an extrapolated
input (``|d E_CBS / d E_i|``, :func:`~tckdb_schemas.composite_formulas.extrapolation_weights`),
and 1 for each number summed into a ``correlation`` that needs two. A larger-basis
energy enters an inverse-power extrapolation with a weight above 1, so a total
computed from inputs printed to six decimals stays inside the tolerance it earns.
When every weight is 1 this is the rule of the composite results
(``max(1e-6, 5e-7 * n)``, ``n`` the number of rounded quantities). The deposited
total counts as one number of weight 1.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_formulas import ExtrapolationError, extrapolate, extrapolation_weights
from tckdb_schemas.enums import (
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeTermOperation,
    EnergyComponentKind,
)
from tckdb_schemas.fragments.calculation import composite_arithmetic_tolerance_hartree
from tckdb_schemas.sp_energy_components import SUM_TOLERANCE_HARTREE

__all__ = [
    "COMPOSITE_TOTAL_MISMATCH",
    "ComponentValue",
    "InputEnergies",
    "TotalCheck",
    "assert_composite_total_recomputes",
    "check_composite_total",
    "component_value",
]

#: The deposited total differs from the total recomputed from the inputs by more
#: than the tolerance. Block tier (ADR 0008): a total that does not follow from
#: the inputs it cites contradicts its own recipe.
COMPOSITE_TOTAL_MISMATCH = "composite_total_mismatch"

#: Reasons a total is unverifiable (stable tokens; each is context, not a code).
REASON_INPUT_ENERGY_NOT_STATED = "input_energy_not_stated"
REASON_COMPONENT_NOT_STATED = "component_not_stated"
REASON_CORRELATION_CONVENTION_UNDETERMINABLE = "correlation_convention_undeterminable"
REASON_EXTRAPOLATION_DEGENERATE = "extrapolation_degenerate"
REASON_NO_TOTAL_DEPOSITED = "no_total_deposited"


@dataclass(frozen=True)
class InputEnergies:
    """The stored energies of one input calculation; ``None`` is *not stated*.

    :param total: The single point's ``electronic_energy_hartree`` or the
        optimisation's ``final_energy_hartree``.
    :param components: The single point's stored energy components.
    """

    total: float | None
    components: Mapping[EnergyComponentKind, float]


@dataclass(frozen=True)
class ComponentValue:
    """One component of an input, as a term reads it.

    :param value: The number, or ``None`` when it cannot be read.
    :param quantities: How many stored numbers it was formed from.
    :param reason: Why ``value`` is ``None`` (a stable token).
    """

    value: float | None
    quantities: int
    reason: str | None = None


def _kind(component: object) -> EnergyComponentKind:
    return component if isinstance(component, EnergyComponentKind) else EnergyComponentKind(component)


def _convention(energies: InputEnergies) -> str | None:
    """``"includes"``, ``"separate"``, ``"both"`` (|(T)| under the tolerance) or ``None`` (unreadable)."""
    reference = energies.components.get(EnergyComponentKind.reference)
    stored = energies.components.get(EnergyComponentKind.correlation)
    triples = energies.components.get(EnergyComponentKind.triples)
    if stored is None or reference is None or energies.total is None:
        return None
    includes = abs(reference + stored - energies.total) <= SUM_TOLERANCE_HARTREE
    separate = triples is not None and abs(reference + stored + triples - energies.total) <= SUM_TOLERANCE_HARTREE
    if includes and separate:
        return "both"
    if includes:
        return "includes"
    if separate:
        return "separate"
    return None


def component_value(energies: InputEnergies, component: EnergyComponentKind | str) -> ComponentValue:
    """Read ``component`` off an input, resolving the triples convention for the correlation kinds.

    :param energies: The input's stored energies.
    :param component: The component a scheme term reads.
    :returns: The value and how many stored numbers made it, or ``value=None`` with
        the reason it cannot be read.
    """
    kind = _kind(component)
    if kind is EnergyComponentKind.total:
        if energies.total is None:
            return ComponentValue(None, 0, REASON_INPUT_ENERGY_NOT_STATED)
        return ComponentValue(energies.total, 1)
    if kind not in (EnergyComponentKind.correlation, EnergyComponentKind.correlation_excluding_triples):
        stored = energies.components.get(kind)
        if stored is None:
            return ComponentValue(None, 0, REASON_COMPONENT_NOT_STATED)
        return ComponentValue(stored, 1)

    correlation = energies.components.get(EnergyComponentKind.correlation)
    triples = energies.components.get(EnergyComponentKind.triples)
    if correlation is None:
        return ComponentValue(None, 0, REASON_COMPONENT_NOT_STATED)
    convention = _convention(energies)
    if convention is None:
        return ComponentValue(None, 0, REASON_CORRELATION_CONVENTION_UNDETERMINABLE)
    if kind is EnergyComponentKind.correlation:
        if convention == "separate":
            assert triples is not None
            return ComponentValue(correlation + triples, 2)
        return ComponentValue(correlation, 1)
    # correlation_excluding_triples
    if convention in ("separate", "both"):
        return ComponentValue(correlation, 1)
    if triples is None:
        return ComponentValue(None, 0, REASON_COMPONENT_NOT_STATED)
    return ComponentValue(correlation - triples, 2)


@dataclass(frozen=True)
class TotalCheck:
    """The outcome of comparing a deposited total with the recomputed one.

    ``status`` is ``ok``, ``mismatch`` or ``unverifiable``. ``recomputed`` and
    ``gap`` are for the refusal's context and are never stored.
    """

    status: str
    deposited: float | None
    recomputed: float | None
    gap: float | None
    tolerance: float | None
    quantities: int
    reason: str | None = None
    term_key: str | None = None
    #: ``1 + sum |weight|`` over the consumed numbers: what the tolerance is ``5e-7 *``.
    weighted_quantities: float = 0.0


def _unverifiable(deposited: float | None, reason: str, term_key: str | None = None) -> TotalCheck:
    return TotalCheck("unverifiable", deposited, None, None, None, 0, reason, term_key)


def check_composite_total(
    definition: object,
    energies_for: Callable[[int, CompositeInputSlot, int | None], InputEnergies | None],
    deposited_total: float | None,
) -> TotalCheck:
    """Recompute a scheme's total from its inputs and compare it with the deposited one.

    :param definition: The ``CompositeSchemeDefinition`` (or any object with its
        ``terms`` / ``operation`` / ``energy_component`` / ``formula`` /
        ``exponent`` / ``inputs`` attributes).
    :param energies_for: ``(term_position, slot, cardinal_number) -> InputEnergies``
        for the calculation that fills that slot, or ``None`` when none does.
    :param deposited_total: The composite's deposited ``electronic_energy_hartree``.
    :returns: A :class:`TotalCheck`. Nothing in it is meant to be stored.
    """
    if deposited_total is None:
        return _unverifiable(None, REASON_NO_TOTAL_DEPOSITED)
    total = 0.0
    quantities = 0
    weight_sum = 0.0
    for position, term in enumerate(definition.terms):  # type: ignore[attr-defined]
        operation = CompositeTermOperation(getattr(term.operation, "value", term.operation))
        component = _kind(getattr(term.energy_component, "value", term.energy_component))
        values: dict[tuple[CompositeInputSlot, int | None], ComponentValue] = {}
        for term_input in term.inputs:
            slot = CompositeInputSlot(getattr(term_input.slot, "value", term_input.slot))
            cardinal = term_input.cardinal_number if slot is CompositeInputSlot.cardinal else None
            energies = energies_for(position, slot, cardinal)
            if energies is None:
                return _unverifiable(deposited_total, REASON_INPUT_ENERGY_NOT_STATED, term.key)
            read = component_value(energies, component)
            if read.value is None:
                return _unverifiable(deposited_total, read.reason or REASON_COMPONENT_NOT_STATED, term.key)
            values[(slot, cardinal)] = read
            quantities += read.quantities
        if operation in (CompositeTermOperation.base, CompositeTermOperation.value):
            term_value = values[(CompositeInputSlot.value, None)].value
            weight_sum += sum(read.quantities for read in values.values())
        elif operation is CompositeTermOperation.difference:
            high = values[(CompositeInputSlot.high, None)].value
            low = values[(CompositeInputSlot.low, None)].value
            assert high is not None and low is not None
            term_value = high - low
            weight_sum += sum(read.quantities for read in values.values())
        elif operation is CompositeTermOperation.extrapolation:
            by_cardinal = {c: read for (_, c), read in values.items() if c is not None and read.value is not None}
            points = [(c, read.value) for c, read in by_cardinal.items() if read.value is not None]
            formula = CompositeExtrapolationFormula(getattr(term.formula, "value", term.formula))
            try:
                term_value = extrapolate(formula, points, term.exponent)
                weights = extrapolation_weights(formula, points, term.exponent)
            except ExtrapolationError:
                return _unverifiable(deposited_total, REASON_EXTRAPOLATION_DEGENERATE, term.key)
            for (cardinal, _), weight in zip(sorted(points, key=lambda point: point[0]), weights, strict=True):
                weight_sum += weight * by_cardinal[cardinal].quantities
        else:  # empirical: nothing to recompute it from
            return _unverifiable(deposited_total, REASON_COMPONENT_NOT_STATED, term.key)
        assert term_value is not None
        if not math.isfinite(term_value):
            return _unverifiable(deposited_total, REASON_EXTRAPOLATION_DEGENERATE, term.key)
        total += term_value
    weighted = 1.0 + weight_sum
    tolerance = composite_arithmetic_tolerance_hartree(weighted)
    gap = deposited_total - total
    status = "ok" if abs(gap) <= tolerance + 1e-12 else "mismatch"
    return TotalCheck(status, deposited_total, total, gap, tolerance, quantities, weighted_quantities=weighted)


def assert_composite_total_recomputes(check: TotalCheck) -> None:
    """Refuse a deposited total that the inputs do not reproduce.

    :param check: The result of :func:`check_composite_total`.
    :raises CodedValidationError: ``composite_total_mismatch``. An
        ``unverifiable`` check is not refused here; it is a warning the caller
        reports.
    """
    if check.status != "mismatch":
        return
    raise CodedValidationError(
        COMPOSITE_TOTAL_MISMATCH,
        (
            f"composite_result.electronic_energy_hartree ({check.deposited!r} Eh) is not what the scheme "
            f"gives for the energies of the calculations it names: recomputed from them the total is "
            f"{check.recomputed!r} Eh, a difference of {check.gap:.3e} Eh against a tolerance of "
            f"{check.tolerance:.2e} Eh. The recomputed value is not stored; fix the deposited total, "
            "the scheme, or the input that disagrees."
        ),
        context={
            "field": "composite_result.electronic_energy_hartree",
            "deposited_hartree": check.deposited,
            "recomputed_hartree": check.recomputed,
            "difference_hartree": check.gap,
            "tolerance_hartree": check.tolerance,
        },
        message_prefix=False,
    )
