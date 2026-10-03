"""Block-tier checks for a single point's deposited energy components (ADR 0021).

A single point may carry the parts of its electronic energy: the reference
(SCF / HF) energy, the correlation energy, a triples part, corrections. A
composite level of theory (a CCSD(T)/CBS extrapolation, a focal-point scheme)
consumes those parts, so they must be stored; they must also not contradict the
energy they are parts of.

What is checked
---------------
* **Component type.** Components belong to a single point and nowhere else.
* **No duplicates.** One value per component per calculation.
* **The energy must be stated.** Components without the single point's
  ``electronic_energy_hartree`` are refused
  (``sp_energy_components_require_energy``). The parts come from the same
  output as the energy, so a producer that has them can state it. Accepting
  them without it would let a log fill the energy later, after every check
  here had run, and store parts that contradict it.
* **A ``total`` is the energy.** A deposited ``total`` must equal the energy.
* **Reference plus correlation is the energy, under either convention.**
  Programs differ on what "correlation" means for a CCSD(T) single point:

  - *Correlation includes the triples* (ORCA's "correlation energy"; the part
    that is total minus SCF): ``reference + correlation`` is the energy, and no
    ``triples`` component is sent.
  - *Correlation is the CCSD part, triples separate* (Molpro prints CCSD and
    (T) separately): ``reference + correlation + triples`` is the energy.

  When a ``triples`` component is sent, either sum may match; otherwise the
  deposit is refused (``sp_energy_components_do_not_sum``) and both sums are
  reported in the context.

TCKDB never stores a value it computed: the sums are formed only to be
compared, returned nowhere and written nowhere, and an absent part is never
filled in as ``energy - other``.

For producers on F12 methods: ``reference`` must include the CABS-singles
correction if the program's total energy does. A reference taken from the plain
Hartree-Fock line, with the CABS correction left out, will not add up and is
refused.

The tolerance, 1e-6 hartree, is the one the single-point energy reconciliation
uses: tight enough to catch a part from a different run, loose enough for the
digits a program prints.

The functions take plain values and raise :class:`CodedValidationError`, so the
wire models, the client and the backend all run the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import CalculationType, EnergyComponentKind

#: Field description shared by every carrier, so the producer contract states the
#: conventions where a producer reads the field.
SP_ENERGY_COMPONENTS_DESCRIPTION = (
    "The parts of this single point's electronic energy, as the program printed them. "
    "Single points only, and only together with the energy: sp_result.electronic_energy_hartree "
    "(or sp_electronic_energy_hartree) must be stated, else sp_energy_components_require_energy. "
    "One value per component. reference + correlation must equal the energy within 1e-6 Eh; "
    "when a triples component is also sent, reference + correlation + triples may match instead. "
    "ORCA's correlation energy already includes (T) (use reference + correlation); Molpro prints "
    "CCSD and (T) separately (send correlation = CCSD and triples = (T)). On F12 methods, "
    "reference must include the CABS-singles correction if the program's total does. A total "
    "component must equal the energy. TCKDB compares and never stores a value it computed."
)

__all__ = [
    "SP_ENERGY_COMPONENTS_DESCRIPTION",
    "SP_ENERGY_COMPONENT_DERIVED",
    "SP_ENERGY_COMPONENT_DUPLICATE",
    "SP_ENERGY_COMPONENT_NOT_ON_SP",
    "SP_ENERGY_COMPONENT_TOTAL_MISMATCH",
    "SP_ENERGY_COMPONENTS_DO_NOT_SUM",
    "SP_ENERGY_COMPONENTS_REQUIRE_ENERGY",
    "SUM_TOLERANCE_HARTREE",
    "check_sp_energy_components",
]

SP_ENERGY_COMPONENT_NOT_ON_SP = "sp_energy_component_not_on_sp"
#: ``correlation_excluding_triples`` is a quantity a composite scheme term reads,
#: derived from the stored ``correlation`` and ``triples``; it is never stored.
SP_ENERGY_COMPONENT_DERIVED = "sp_energy_component_derived"
SP_ENERGY_COMPONENT_DUPLICATE = "sp_energy_component_duplicate"
SP_ENERGY_COMPONENT_TOTAL_MISMATCH = "sp_energy_component_total_mismatch"
SP_ENERGY_COMPONENTS_DO_NOT_SUM = "sp_energy_components_do_not_sum"
SP_ENERGY_COMPONENTS_REQUIRE_ENERGY = "sp_energy_components_require_energy"

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

    for kind, _ in pairs:
        if kind is EnergyComponentKind.correlation_excluding_triples:
            raise CodedValidationError(
                SP_ENERGY_COMPONENT_DERIVED,
                (
                    "'correlation_excluding_triples' is not a stored component: it is derived from the "
                    "'correlation' and 'triples' you send, under the convention your reference, correlation "
                    "and energy imply. Send 'correlation' (and 'triples' where the program prints it "
                    "separately); a composite scheme term may then read the CCSD part by name."
                ),
                context={"component": kind.value},
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
        raise CodedValidationError(
            SP_ENERGY_COMPONENTS_REQUIRE_ENERGY,
            (
                "sp_energy_components were sent without the single point's "
                "electronic_energy_hartree. The parts come from the same output as the "
                "energy; state it so the parts can be checked against it."
            ),
            context={"calculation_type": type_value},
            message_prefix=False,
        )
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
    if reference is None or correlation is None:
        return
    triples = values.get(EnergyComponentKind.triples)
    without_triples = reference + correlation
    with_triples = None if triples is None else without_triples + triples
    if abs(without_triples - energy) <= SUM_TOLERANCE_HARTREE:
        return
    if with_triples is not None and abs(with_triples - energy) <= SUM_TOLERANCE_HARTREE:
        return
    raise CodedValidationError(
        SP_ENERGY_COMPONENTS_DO_NOT_SUM,
        (
            f"The 'reference' ({reference!r} Eh) and 'correlation' ({correlation!r} Eh) "
            f"components do not add up to the single point's electronic_energy_hartree "
            f"({energy!r} Eh) within {SUM_TOLERANCE_HARTREE:g} Eh"
            + (
                ", with or without the 'triples' component. "
                if triples is not None
                else ". "
            )
            + "They must be the parts of that one energy; a part taken from a different "
            "run is refused rather than stored."
        ),
        context={
            "reference_hartree": reference,
            "correlation_hartree": correlation,
            "triples_hartree": triples,
            "reference_plus_correlation_hartree": without_triples,
            "reference_plus_correlation_plus_triples_hartree": with_triples,
            "electronic_energy_hartree": energy,
            "tolerance_hartree": SUM_TOLERANCE_HARTREE,
        },
        message_prefix=False,
    )
