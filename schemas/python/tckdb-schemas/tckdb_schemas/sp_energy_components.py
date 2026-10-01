"""Block-tier checks for a single point's deposited energy components (ADR 0021).

A single point may carry the parts of its electronic energy: the reference
(SCF) energy, the correlation energy, a triples part, corrections. A composite
level of theory (a CCSD(T)/CBS extrapolation, a focal-point scheme) consumes
those parts, so they must be stored; they must also not contradict the energy
they are parts of.

What is checked, and what is not
--------------------------------
* **Component type.** Components belong to a single point and nowhere else.
* **No duplicates.** One value per component per calculation.
* **A ``total`` is the energy.** A deposited ``total`` must equal the
  single point's ``electronic_energy_hartree``.
* **Reference plus correlation is the energy.** When the reference part, the
  correlation part and the energy are all present, ``reference + correlation``
  must equal the energy within :data:`SUM_TOLERANCE_HARTREE`.

Two rules the checks keep, both from ADR 0021:

1. **TCKDB never stores a value it computed.** The sum is formed here only to
   be compared; it is returned nowhere and written nowhere. An absent part is
   never filled in as ``energy - other``.
2. **A check that cannot answer does not guess.** The sum rule is skipped when
   a ``triples`` component is also deposited: programs differ on whether the
   correlation energy they print includes the perturbative triples (ORCA's
   "correlation energy" does, Molpro prints the CCSD part and the (T) part
   separately), so ``reference + correlation`` is then not the energy by
   definition and refusing it would refuse correct deposits. The rule is also
   skipped when the energy is absent, which is the case until a log fills it.

The tolerance, 1e-6 hartree, is the tolerance the single-point energy
reconciliation and the planned composite-total check use: tight enough to
catch a part from a different run, loose enough for the digits a program
prints.

The functions take plain values and raise :class:`CodedValidationError`, so the
wire models, the client and the backend all run the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import CalculationType, EnergyComponentKind

__all__ = [
    "SP_ENERGY_COMPONENT_DUPLICATE",
    "SP_ENERGY_COMPONENT_NOT_ON_SP",
    "SP_ENERGY_COMPONENT_TOTAL_MISMATCH",
    "SP_ENERGY_COMPONENTS_DO_NOT_SUM",
    "SUM_TOLERANCE_HARTREE",
    "check_sp_energy_components",
]

SP_ENERGY_COMPONENT_NOT_ON_SP = "sp_energy_component_not_on_sp"
SP_ENERGY_COMPONENT_DUPLICATE = "sp_energy_component_duplicate"
SP_ENERGY_COMPONENT_TOTAL_MISMATCH = "sp_energy_component_total_mismatch"
SP_ENERGY_COMPONENTS_DO_NOT_SUM = "sp_energy_components_do_not_sum"

#: Agreement required between ``reference + correlation`` (or ``total``) and the
#: single point's electronic energy, in hartree.
SUM_TOLERANCE_HARTREE = 1e-6


def _kind(value: object) -> EnergyComponentKind:
    return value if isinstance(value, EnergyComponentKind) else EnergyComponentKind(value)


def check_sp_energy_components(
    components: Iterable[tuple[object, float]],
    *,
    calculation_type: CalculationType | str,
    electronic_energy_hartree: float | None,
) -> None:
    """Refuse a contradictory set of energy components.

    :param components: ``(component, value_hartree)`` pairs as deposited.
    :param calculation_type: The type of the calculation they sit on.
    :param electronic_energy_hartree: That calculation's deposited electronic
        energy, or ``None`` when it states none.
    :raises CodedValidationError: ``sp_energy_component_not_on_sp``,
        ``sp_energy_component_duplicate``, ``sp_energy_component_total_mismatch``
        or ``sp_energy_components_do_not_sum``.
    """
    pairs = [(_kind(component), float(value)) for component, value in components]
    if not pairs:
        return

    type_value = getattr(calculation_type, "value", calculation_type)
    if type_value != CalculationType.sp.value:
        raise CodedValidationError(
            SP_ENERGY_COMPONENT_NOT_ON_SP,
            (
                f"sp_energy_components are only allowed on single-point calculations "
                f"(got type {type_value!r}). The parts of an electronic energy belong to "
                "the single point that produced them."
            ),
            context={"calculation_type": type_value},
            message_prefix=False,
        )

    values: dict[EnergyComponentKind, float] = {}
    for kind, value in pairs:
        if kind in values:
            raise CodedValidationError(
                SP_ENERGY_COMPONENT_DUPLICATE,
                (
                    f"sp_energy_components lists component {kind.value!r} more than once. "
                    "A single point has one value per component."
                ),
                context={"component": kind.value},
                message_prefix=False,
            )
        values[kind] = value

    if electronic_energy_hartree is None:
        return
    energy = float(electronic_energy_hartree)

    total = values.get(EnergyComponentKind.total)
    if total is not None and abs(total - energy) > SUM_TOLERANCE_HARTREE:
        raise CodedValidationError(
            SP_ENERGY_COMPONENT_TOTAL_MISMATCH,
            (
                f"The 'total' energy component ({total!r} Eh) differs from the single "
                f"point's electronic_energy_hartree ({energy!r} Eh) by more than "
                f"{SUM_TOLERANCE_HARTREE:g} Eh. A total is the electronic energy; send one "
                "value, or the matching one."
            ),
            context={
                "total_hartree": total,
                "electronic_energy_hartree": energy,
                "tolerance_hartree": SUM_TOLERANCE_HARTREE,
            },
            message_prefix=False,
        )

    reference = values.get(EnergyComponentKind.reference)
    correlation = values.get(EnergyComponentKind.correlation)
    if reference is None or correlation is None or EnergyComponentKind.triples in values:
        return
    if abs((reference + correlation) - energy) > SUM_TOLERANCE_HARTREE:
        raise CodedValidationError(
            SP_ENERGY_COMPONENTS_DO_NOT_SUM,
            (
                f"The 'reference' ({reference!r} Eh) and 'correlation' ({correlation!r} Eh) "
                f"components do not add up to the single point's electronic_energy_hartree "
                f"({energy!r} Eh) within {SUM_TOLERANCE_HARTREE:g} Eh. They must be the parts "
                "of that one energy; a part taken from a different run is refused rather "
                "than stored."
            ),
            context={
                "reference_hartree": reference,
                "correlation_hartree": correlation,
                "electronic_energy_hartree": energy,
                "tolerance_hartree": SUM_TOLERANCE_HARTREE,
            },
            message_prefix=False,
        )
