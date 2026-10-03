"""Which calculation types may be the source of a network solve's energy (#642).

What the rule is
----------------
A pressure-dependent network states energies in three places, and each can
cite the calculation the number came from:

* ``solve.state_energies[].source_calculation_key`` (a well, bimolecular or
  other state energy),
* ``solve.channel_barriers[].source_calculation_key`` (a saddle point's
  energy, as a barrier),
* ``solve.source_calculations[]`` with the role ``well_energy`` or
  ``barrier_energy``.

Until #642 any calculation type was accepted in all three, so a network could
say a well's energy came from an IRC point or a rotor scan. The same mistake is
already refused on the transition-state ``energy_ordering`` evidence
(``app.services.transition_state_validation._ENERGY_SOURCE_TYPES``, #637); this
module is that rule for the network route, extended for the ``composite``
calculation type (ADR 0021), which carries both an electronic energy and an E0
in ``calc_composite_result``.

What the route's energy kinds map to
------------------------------------
The route has no ``energy_kind`` field of its own. The kind a state or barrier
energy claims is its ``correction_convention``:

* ``electronic_only`` is a bare electronic energy. It comes from what carries
  one: an ``sp`` (its single-point energy), an ``opt`` (its final energy), or
  a ``composite`` (its electronic energy). A ``freq`` is refused, as on the
  transition-state route.
* ``electronic_plus_zpe``, ``atom_and_bond_corrected``,
  ``thermal_enthalpy_298k`` and ``other`` are *composed* quantities: an
  electronic energy plus a frequency-derived term (and, for the corrected
  kinds, a correction library). The route gives each energy ONE source slot,
  so the slot cites the calculation of the energy's electronic part, or a
  ``composite`` that holds the whole E0; the ``freq`` goes in the
  ``well_freq`` / ``barrier_freq`` roles. These kinds are held to the floor
  below and no tighter: every producer and fixture in this repository cites
  the single point, and #637's "an E0 cites its ``freq``" fits a record that
  has separate electronic and E0 groups, not this one-slot route.

The floor, for every kind and for the two energy roles, is #637's electronic
set union its E0 set, plus ``composite``: ``sp``, ``opt``, ``freq`` or
``composite``. #637's table (``_ENERGY_SOURCE_TYPES`` in
``app.services.transition_state_validation``) predates the ``composite``
calculation type, so it does not list it; the floor is that rule extended, not
a deviation from it. ``irc``, ``scan`` and ``path_search`` report the energies
of points along a path, and ``conf`` is a conformer search, so none is ever the
source of a state or barrier energy. A follow-up will share one table between
the two rules.

Tier (ADR 0008)
---------------
``block``, the tier #637 used for the same mismatch: a type that cannot carry
the stated energy is not a weaker link, it is a wrong one, and no correct
deposit produces it. The refusal is a ``CodedValueError`` so a client branches
on the code and is told the types that would have worked. Uploads only: nothing
here runs on a read, so stored networks are never affected.

The refusal names the field the depositor wrote and never a row id (DR-0028);
the id goes to the log.
"""

from __future__ import annotations

import logging

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    EnergyCorrectionConvention,
    NetworkSolveCalculationRole,
)

logger = logging.getLogger(__name__)

#: A network solve cites, as the source of an energy, a calculation whose type
#: cannot carry that energy.
W_NETWORK_ENERGY_SOURCE_TYPE_MISMATCH = "network_energy_source_type_mismatch"

#: Calculation types that report the energy of a stationary point at all.
_STATIONARY_POINT_ENERGY_TYPES: frozenset[CalculationType] = frozenset(
    {
        CalculationType.sp,
        CalculationType.opt,
        CalculationType.freq,
        CalculationType.composite,
    }
)

#: The types that carry a bare electronic energy.
_ELECTRONIC_ENERGY_TYPES: frozenset[CalculationType] = frozenset(
    {
        CalculationType.sp,
        CalculationType.opt,
        CalculationType.composite,
    }
)

#: Accepted source types by the energy kind a state or barrier states. A kind
#: absent from this map falls back to the floor (see the module docstring).
_SOURCE_TYPES_BY_CORRECTION: dict[
    EnergyCorrectionConvention, frozenset[CalculationType]
] = {
    EnergyCorrectionConvention.electronic_only: _ELECTRONIC_ENERGY_TYPES,
}

#: The ``source_calculations`` roles that name an energy source. The frequency,
#: master-equation and fit roles name other jobs and are not constrained here.
_ENERGY_ROLES: frozenset[NetworkSolveCalculationRole] = frozenset(
    {
        NetworkSolveCalculationRole.well_energy,
        NetworkSolveCalculationRole.barrier_energy,
    }
)


def _ordered(types: frozenset[CalculationType]) -> list[str]:
    return sorted(calc_type.value for calc_type in types)


def _refuse(
    calculation: Calculation,
    accepted: frozenset[CalculationType],
    *,
    field: str,
    stated: str,
    stated_value: str,
) -> None:
    logger.info(
        "network energy source type mismatch at %s: calculation id=%s type=%s, %s=%s",
        field,
        calculation.id,
        calculation.type.value,
        stated,
        stated_value,
    )
    accepted_values = _ordered(accepted)
    raise CodedValueError(
        W_NETWORK_ENERGY_SOURCE_TYPE_MISMATCH,
        f"{field}: a '{calculation.type.value}' calculation cannot be the "
        f"source of an energy stated as {stated}='{stated_value}'. Cite a "
        f"{' or '.join(repr(value) for value in accepted_values)} calculation.",
        context={
            "field": field,
            "stated": stated,
            "stated_value": stated_value,
            "accepted_calculation_types": accepted_values,
            "actual_calculation_type": calculation.type.value,
        },
        message_prefix=False,
    )


def assert_network_energy_source_type(
    calculation: Calculation,
    correction_convention: EnergyCorrectionConvention,
    *,
    field: str,
) -> None:
    """Refuse a state or barrier energy whose cited calculation cannot carry it.

    :param field: The field the depositor wrote, e.g.
        ``solve.state_energies[0].source_calculation_key``.
    :raises CodedValueError: ``network_energy_source_type_mismatch``.
    """
    # The wire package's enum, or a bare string from an unvalidated request,
    # both normalise to the backend's own member here.
    correction_convention = EnergyCorrectionConvention(correction_convention)
    accepted = _SOURCE_TYPES_BY_CORRECTION.get(
        correction_convention, _STATIONARY_POINT_ENERGY_TYPES
    )
    if calculation.type in accepted:
        return
    _refuse(
        calculation,
        accepted,
        field=field,
        stated="correction_convention",
        stated_value=correction_convention.value,
    )


def assert_network_source_role_type(
    calculation: Calculation,
    role: NetworkSolveCalculationRole,
    *,
    field: str,
) -> None:
    """Refuse a ``well_energy`` / ``barrier_energy`` link to a type with no energy.

    The role states no energy kind, so only the floor applies. Other roles are
    not constrained here.

    :raises CodedValueError: ``network_energy_source_type_mismatch``.
    """
    role = NetworkSolveCalculationRole(role)
    if role not in _ENERGY_ROLES:
        return
    if calculation.type in _STATIONARY_POINT_ENERGY_TYPES:
        return
    _refuse(
        calculation,
        _STATIONARY_POINT_ENERGY_TYPES,
        field=field,
        stated="role",
        stated_value=role.value,
    )
