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
that reads ``correlation`` means the **whole** correlation energy, (T) included,
so under the second convention the term's value is ``correlation + triples``. The
convention is read off the stored row, never assumed: whichever of the two sums
equals the stored energy within the single-point tolerance
(:data:`~tckdb_schemas.sp_energy_components.SUM_TOLERANCE_HARTREE`, 1e-6 Eh) is
the row's convention, and |(T)| is far larger than that tolerance. If both sums
match (|(T)| below the tolerance) the stored ``correlation`` is used. If neither
does, or the reference or the energy is not stated, the convention cannot be
determined and the total is *unverifiable*, never guessed. A scheme that reads
``triples`` as its own term reads the stored ``triples`` as it is, so a producer
who wants (T) at a different basis than the rest of the correlation energy sends
inputs under the separate convention (``correlation`` = CCSD) and gives (T) its
own terms.

The tolerance
-------------
``max(1e-6, 5e-7 * n)`` hartree (:func:`composite_arithmetic_tolerance_hartree`),
with ``n`` the number of rounded quantities in the equation: the deposited total
and every stored number the recomputation consumed. The count is of numbers, not
weighted by how much a formula amplifies them: an extrapolation weights its
larger-basis energy by more than one, so a total computed from inputs printed to
six decimals can sit just outside the tolerance in the worst case. Energies
printed to nine or more decimals (ORCA, Molpro) are far inside it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_formulas import ExtrapolationError, extrapolate
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


def component_value(energies: InputEnergies, component: EnergyComponentKind | str) -> ComponentValue:
    """Read ``component`` off an input, resolving the triples convention for ``correlation``.

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
    stored = energies.components.get(kind)
    if kind is not EnergyComponentKind.correlation:
        if stored is None:
            return ComponentValue(None, 0, REASON_COMPONENT_NOT_STATED)
        return ComponentValue(stored, 1)

    reference = energies.components.get(EnergyComponentKind.reference)
    triples = energies.components.get(EnergyComponentKind.triples)
    if stored is None:
        return ComponentValue(None, 0, REASON_COMPONENT_NOT_STATED)
    if reference is None or energies.total is None:
        return ComponentValue(None, 0, REASON_CORRELATION_CONVENTION_UNDETERMINABLE)
    includes_triples = abs(reference + stored - energies.total) <= SUM_TOLERANCE_HARTREE
    separate_triples = triples is not None and abs(reference + stored + triples - energies.total) <= SUM_TOLERANCE_HARTREE
    if includes_triples:
        return ComponentValue(stored, 1)
    if separate_triples:
        assert triples is not None
        return ComponentValue(stored + triples, 2)
    return ComponentValue(None, 0, REASON_CORRELATION_CONVENTION_UNDETERMINABLE)


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
        elif operation is CompositeTermOperation.difference:
            high = values[(CompositeInputSlot.high, None)].value
            low = values[(CompositeInputSlot.low, None)].value
            assert high is not None and low is not None
            term_value = high - low
        elif operation is CompositeTermOperation.extrapolation:
            points = [(cardinal, read.value) for (slot, cardinal), read in values.items() if cardinal is not None and read.value is not None]
            formula = CompositeExtrapolationFormula(getattr(term.formula, "value", term.formula))
            try:
                term_value = extrapolate(formula, points, term.exponent)
            except ExtrapolationError:
                return _unverifiable(deposited_total, REASON_EXTRAPOLATION_DEGENERATE, term.key)
        else:  # empirical: nothing to recompute it from
            return _unverifiable(deposited_total, REASON_COMPONENT_NOT_STATED, term.key)
        assert term_value is not None
        if not math.isfinite(term_value):
            return _unverifiable(deposited_total, REASON_EXTRAPOLATION_DEGENERATE, term.key)
        total += term_value
    tolerance = composite_arithmetic_tolerance_hartree(quantities + 1)
    gap = deposited_total - total
    status = "ok" if abs(gap) <= tolerance + 1e-12 else "mismatch"
    return TotalCheck(status, deposited_total, total, gap, tolerance, quantities)


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
