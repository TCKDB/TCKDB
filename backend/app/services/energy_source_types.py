"""Which calculation types may be the source of a stated energy (#669).

One table, read by both rules that hold a cited calculation against the kind of
energy it is cited for:

* the transition-state ``energy_ordering`` evidence rule
  (:mod:`app.services.transition_state_validation`, #637), and
* the pressure-dependent network rule
  (:mod:`app.services.network_energy_sources`, #642).

They used to keep a table each, and the two disagreed: #637's predated the
``composite`` calculation type (ADR 0021, #654) and accepted it for neither
kind, while #642 had to extend its own floor to take it.

What each kind is
-----------------
* **electronic**: a bare electronic energy. An ``sp`` reports one, an ``opt`` ends
  on one, and a ``composite`` stores one (``electronic_energy_hartree``).
* **E0**: an energy that includes the zero-point energy. A ``freq`` carries the
  ZPE, so an E0 is cited to the ``freq`` it came from; a ``composite`` stores an
  ``e0_hartree`` of its own and is the whole answer.

``irc``, ``scan`` and ``path_search`` report the energies of points along a path
and ``conf`` is a conformer search, so none is ever the source of either kind.

The frequency roles
-------------------
``well_freq`` and ``barrier_freq`` name the job whose *modes* the master equation
reads. A ``composite`` stores only a ZPE, not the modes, so it cannot stand in:
those roles take a ``freq`` and nothing else (:data:`FREQUENCY_ROLE_TYPES`).
"""

from __future__ import annotations

from app.db.models.common import CalculationType

ELECTRONIC_ENERGY_SOURCE_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.sp, CalculationType.opt, CalculationType.composite}
)

E0_ENERGY_SOURCE_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.freq, CalculationType.composite}
)

#: By the ``energy_kind`` an ``energy_ordering`` energy states.
ENERGY_SOURCE_TYPES_BY_KIND: dict[str, frozenset[CalculationType]] = {
    "electronic": ELECTRONIC_ENERGY_SOURCE_TYPES,
    "e0": E0_ENERGY_SOURCE_TYPES,
}

#: Every type that reports the energy of a stationary point at all: the floor for a
#: network energy whose kind the route does not state.
STATIONARY_POINT_ENERGY_TYPES: frozenset[CalculationType] = (
    ELECTRONIC_ENERGY_SOURCE_TYPES | E0_ENERGY_SOURCE_TYPES
)

#: The types a ``well_freq`` / ``barrier_freq`` source may be: the modes are needed.
FREQUENCY_ROLE_TYPES: frozenset[CalculationType] = frozenset({CalculationType.freq})

__all__ = [
    "E0_ENERGY_SOURCE_TYPES",
    "ELECTRONIC_ENERGY_SOURCE_TYPES",
    "ENERGY_SOURCE_TYPES_BY_KIND",
    "FREQUENCY_ROLE_TYPES",
    "STATIONARY_POINT_ENERGY_TYPES",
]
