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

Whose calculation it is (#668)
------------------------------
The type is half the rule. A calculation of the right type can still belong to
the wrong subject: a well's energy cited to another well's single point, or a
saddle point's barrier cited to a species single point (#665's own test matrix
stored exactly that). The transition-state route already refuses this for its
``energy_ordering`` evidence (#637: each energy must come from the participant
it is the energy of); this is the same rule for the network route, read from
the persisted ``Calculation.species_entry_id`` / ``transition_state_entry_id``
and never from the payload:

* a ``state_energies[]`` source must be owned by a species entry that is a
  *participant of that state*. A bimolecular state is a sum over its species and
  the one source slot cannot hold the sum, so any one participant's calculation
  is accepted (the hydrazine ingester cites the first participant's single
  point); a species outside the state is not.
* a ``channel_barriers[]`` source must be owned by the transition-state entry
  the barrier names.
* a ``well_energy`` / ``barrier_energy`` link is attached to the solve, not to a
  state or a channel, so its subject is the network: ``well_energy`` must be a
  calculation of a species entry that takes part in one of the network's
  states, and ``barrier_energy`` one of a transition state the upload declares.

The subject is checked after the type, so a calculation that fails both reports
the type (the older, narrower refusal). Neither refusal runs on a read.

One source per participant, and their sum (#678)
------------------------------------------------
A state with several species has an energy that is a sum, and one source slot cannot name
where a sum came from. ``state_energies[].source_calculation_keys`` names one calculation per
participant, ``{species_key, calculation_key}``. Each passes the type rule above and must
belong to *exactly* the participant it is listed for
(:func:`assert_state_participant_source_owner`). The single ``source_calculation_key`` stays: on
a state with more than one participant it is one summand of several, is accepted with a
``network_state_energy_sources_partial`` warning, and is read back as ``partial_sources``.
The other participants' sources are never borrowed from the solve's ``well_energy`` links.

:func:`compare_state_energy_sums` then holds the stated energy against the sum of the stored
energies, ``sum(stoichiometry * stored energy)``, and refuses a contradiction
(``network_state_energy_sum_mismatch``, block). Which energies have a sum that is defined:

* ``electronic_only``: the sum of the sources' electronic energies (``sp`` energy, ``opt`` final
  energy, ``composite`` electronic energy).
* ``electronic_plus_zpe``: only when every source is a ``composite`` that stores an ``e0``. An
  ``sp`` or ``opt`` carries no zero-point energy, a ``freq`` no electronic energy, and the one
  slot per participant cannot hold both, so those are *not compared* (``zpe_not_in_source``),
  never guessed from another calculation.
* ``atom_and_bond_corrected``, ``thermal_enthalpy_298k``, ``other``: not compared
  (``convention_not_summable``): the corrections and thermal terms are not stored per source.

Which zeros are comparable: ``absolute`` directly. ``lowest_state`` and ``entrance_channel``
shift every state energy of a solve by one constant, so the stated energy is not the sum, but
the *difference* between two states' stated energies is the difference of their sums. Those are
compared pairwise against the state with the lowest stated energy, among the solve's states
that have a complete, comparable sum; a solve with one such state is not compared
(``no_second_state_on_the_same_zero``). ``separated_reactants`` and ``other`` share no constant
across states and are not compared (``energy_zero_not_comparable``). The production ingester
states ``lowest_state``, so this is the branch that runs on real data.

Tolerance: :func:`~tckdb_schemas.fragments.calculation.composite_arithmetic_tolerance_hartree`
of the number of rounded quantities in the equation, weighted as ADR 0021 / #657 weight a
computed total (``1 + sum |d total / d x_i|``): the stated energy is one rounded quantity and
each source energy enters multiplied by its stoichiometry, so ``n = 1 + sum(nu_i)`` for an
absolute energy (``2A + B`` has n = 1 + 2 + 1 = 4) and ``n = 2 + sum(nu_i) + sum(nu_j)`` for the
difference of two states. The stated energy is in kJ/mol and is converted to hartree first.
No total is ever stored: the outcome (``agrees`` or ``not_compared`` plus the reason) is.

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
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from tckdb_schemas.fragments.calculation import composite_arithmetic_tolerance_hartree
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    NetworkSolveCalculationRole,
)

logger = logging.getLogger(__name__)

#: A network solve cites, as the source of an energy, a calculation whose type
#: cannot carry that energy.
W_NETWORK_ENERGY_SOURCE_TYPE_MISMATCH = "network_energy_source_type_mismatch"

#: A network solve cites, as the source of an energy, a calculation owned by a
#: subject other than the one the energy is stated for (#668).
W_NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH = "network_energy_source_subject_mismatch"

#: A state energy contradicts the sum of the stored energies of the calculations it cites,
#: one per participant, beyond the printed-precision tolerance (#678). Block tier.
E_NETWORK_STATE_ENERGY_SUM_MISMATCH = "network_state_energy_sum_mismatch"

#: A state energy could not be held against the sum of its sources; it is stored as not
#: compared with the reason (#678). Warn tier.
W_NETWORK_STATE_ENERGY_SUM_NOT_COMPARED = "network_state_energy_sum_not_compared"

#: A state energy cites sources for some but not all of its participants (#678). Warn tier.
W_NETWORK_STATE_ENERGY_SOURCES_PARTIAL = "network_state_energy_sources_partial"

#: ``network_solve_state_energy.source_sum_comparison`` values.
COMPARISON_AGREES = "agrees"
COMPARISON_NOT_COMPARED = "not_compared"

#: Why a state energy was not compared (``source_sum_not_compared_reason``). Stable tokens.
NOT_COMPARED_NO_SOURCE_STATED = "no_source_stated"
NOT_COMPARED_SOURCES_INCOMPLETE = "sources_incomplete"
NOT_COMPARED_CONVENTION_NOT_SUMMABLE = "convention_not_summable"
NOT_COMPARED_ENERGY_ZERO_NOT_COMPARABLE = "energy_zero_not_comparable"
NOT_COMPARED_STORED_ENERGY_NOT_STATED = "stored_energy_not_stated"
NOT_COMPARED_ZPE_NOT_IN_SOURCE = "zpe_not_in_source"
NOT_COMPARED_NO_SECOND_STATE = "no_second_state_on_the_same_zero"

#: Slack for float noise in ``|stated - stored| <= tolerance``; the value
#: ``tckdb_schemas.composite_total`` and the transition-state comparison use.
_TOLERANCE_FLOAT_SLACK = 1e-12

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


def _owner_kind(calculation: Calculation) -> str:
    if calculation.species_entry_id is not None:
        return "species_entry"
    if calculation.transition_state_entry_id is not None:
        return "transition_state_entry"
    return "none"


def _refuse_subject(
    calculation: Calculation,
    *,
    field: str,
    subject_kind: str,
    subject_key: str | None,
    stated: str,
    detail: str,
    extra: dict[str, object] | None = None,
) -> None:
    logger.info(
        "network energy source subject mismatch at %s: calculation id=%s "
        "species_entry_id=%s transition_state_entry_id=%s, expected a %s (%s=%s)",
        field,
        calculation.id,
        calculation.species_entry_id,
        calculation.transition_state_entry_id,
        subject_kind,
        stated,
        subject_key,
    )
    context: dict[str, object] = {
        "field": field,
        "expected_owner_kind": subject_kind,
        "actual_owner_kind": _owner_kind(calculation),
    }
    if subject_key is not None:
        context["stated"] = stated
        context["stated_value"] = subject_key
    if extra:
        context.update(extra)
    raise CodedValueError(
        W_NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH,
        f"{field}: {detail}",
        context=context,
        message_prefix=False,
    )


def assert_state_energy_source_owner(
    calculation: Calculation,
    state_species_entry_ids: Collection[int],
    *,
    state_key: str,
    field: str,
) -> None:
    """Refuse a state energy cited to a calculation of a species outside the state.

    :param state_species_entry_ids: The species entries that are participants
        of the state, read from the persisted participant rows. A bimolecular
        state accepts a calculation of any one of them. An empty collection
        refuses everything, never accepts it.
    :raises CodedValueError: ``network_energy_source_subject_mismatch``.
    """
    if (
        calculation.species_entry_id is not None
        and calculation.species_entry_id in state_species_entry_ids
    ):
        return
    _refuse_subject(
        calculation,
        field=field,
        subject_kind="species_entry",
        subject_key=state_key,
        stated="state_key",
        detail=(
            f"the cited calculation does not belong to a species of the state "
            f"'{state_key}' whose energy it is cited for. Cite a calculation of "
            "one of that state's own species."
        ),
    )


def assert_barrier_source_owner(
    calculation: Calculation,
    transition_state_entry_id: int,
    *,
    transition_state_key: str,
    field: str,
) -> None:
    """Refuse a barrier cited to a calculation of anything but its own saddle point.

    :raises CodedValueError: ``network_energy_source_subject_mismatch``.
    """
    if calculation.transition_state_entry_id == transition_state_entry_id:
        return
    _refuse_subject(
        calculation,
        field=field,
        subject_kind="transition_state_entry",
        subject_key=transition_state_key,
        stated="transition_state_key",
        detail=(
            "the cited calculation does not belong to the transition state "
            f"'{transition_state_key}' the barrier is stated for. Cite a "
            "calculation of that transition state."
        ),
    )


def assert_network_role_source_owner(
    calculation: Calculation,
    role: NetworkSolveCalculationRole,
    *,
    network_species_entry_ids: Collection[int],
    network_transition_state_entry_ids: Collection[int],
    field: str,
) -> None:
    """Refuse a ``well_energy`` / ``barrier_energy`` link to another subject.

    The link hangs off the solve, so the subject it is held to is the network:
    ``well_energy`` to a species entry in one of its states, ``barrier_energy``
    to one of its transition states. Other roles are not constrained here.

    :raises CodedValueError: ``network_energy_source_subject_mismatch``.
    """
    role = NetworkSolveCalculationRole(role)
    if role == NetworkSolveCalculationRole.well_energy:
        if (
            calculation.species_entry_id is not None
            and calculation.species_entry_id in network_species_entry_ids
        ):
            return
        _refuse_subject(
            calculation,
            field=field,
            subject_kind="species_entry",
            subject_key=role.value,
            stated="role",
            detail=(
                "a 'well_energy' calculation must belong to a species of one of "
                "this network's states."
            ),
        )
    elif role == NetworkSolveCalculationRole.barrier_energy:
        if (
            calculation.transition_state_entry_id is not None
            and calculation.transition_state_entry_id
            in network_transition_state_entry_ids
        ):
            return
        _refuse_subject(
            calculation,
            field=field,
            subject_kind="transition_state_entry",
            subject_key=role.value,
            stated="role",
            detail=(
                "a 'barrier_energy' calculation must belong to a transition "
                "state of this network."
            ),
        )


def assert_state_participant_source_owner(
    calculation: Calculation,
    participant_species_entry_id: int,
    *,
    species_key: str,
    state_key: str,
    field: str,
) -> None:
    """Refuse a participant's source that is not a calculation of that exact participant.

    Stricter than :func:`assert_state_energy_source_owner`, which accepts any one species of
    the state: here the slot names the species, so another participant's calculation (or one
    of a species outside the state) is a wrong source, not a partial one.

    :raises CodedValueError: ``network_energy_source_subject_mismatch``.
    """
    if calculation.species_entry_id == participant_species_entry_id:
        return
    _refuse_subject(
        calculation,
        field=field,
        subject_kind="species_entry",
        subject_key=species_key,
        stated="species_key",
        detail=(
            f"the cited calculation does not belong to the species '{species_key}' of the "
            f"state '{state_key}' it is listed for. Cite a calculation of that species."
        ),
        extra={"state_key": state_key},
    )


def refuse_source_species_outside_state(
    calculation: Calculation,
    *,
    species_key: str,
    state_key: str,
    field: str,
) -> None:
    """Refuse a source listed for a species that is not a participant of the state.

    :raises CodedValueError: ``network_energy_source_subject_mismatch``.
    """
    _refuse_subject(
        calculation,
        field=field,
        subject_kind="species_entry",
        subject_key=species_key,
        stated="species_key",
        detail=(
            f"the species '{species_key}' is not a participant of the state '{state_key}' "
            "whose energy the source is listed for. List one source per participant of the state."
        ),
        extra={"state_key": state_key},
    )


# ---------------------------------------------------------------------------
# The sum of a state energy's sources (#678)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParticipantSource:
    """One participant of a state, and the calculation cited for it (None: none cited)."""

    species_key: str
    stoichiometry: int
    calculation: Calculation | None


@dataclass(frozen=True)
class StateEnergyToCompare:
    """A stated state energy with every participant of its state and their sources."""

    index: int
    state_key: str
    energy_kj_mol: float
    energy_zero_convention: EnergyZeroConvention
    correction_convention: EnergyCorrectionConvention
    participants: tuple[ParticipantSource, ...]

    @property
    def field(self) -> str:
        return f"solve.state_energies[{self.index}].energy_kj_mol"


@dataclass(frozen=True)
class SumComparison:
    """What holding a state energy against its sources concluded."""

    status: str
    reason: str | None = None


_AGREES = SumComparison(COMPARISON_AGREES)

#: Zero conventions that shift every state energy of a solve by one constant, so differences
#: between states are comparable even though the energies themselves are not.
_SHARED_OFFSET_ZEROS = frozenset({EnergyZeroConvention.lowest_state, EnergyZeroConvention.entrance_channel})


def _stored_energy_hartree(
    calculation: Calculation, correction: EnergyCorrectionConvention
) -> tuple[float | None, str | None]:
    """``(energy, None)`` or ``(None, reason)``: the stored energy of the kind the state states."""
    if correction == EnergyCorrectionConvention.electronic_plus_zpe:
        if calculation.type != CalculationType.composite:
            return None, NOT_COMPARED_ZPE_NOT_IN_SOURCE
        composite = calculation.composite_result
        value = None if composite is None else composite.e0_hartree
    elif calculation.type == CalculationType.sp:
        sp = calculation.sp_result
        value = None if sp is None else sp.electronic_energy_hartree
    elif calculation.type == CalculationType.opt:
        opt = calculation.opt_result
        value = None if opt is None else opt.final_energy_hartree
    elif calculation.type == CalculationType.composite:
        composite = calculation.composite_result
        value = None if composite is None else composite.electronic_energy_hartree
    else:
        value = None
    if value is None:
        return None, NOT_COMPARED_STORED_ENERGY_NOT_STATED
    return value, None


@dataclass(frozen=True)
class _Prepared:
    energy: StateEnergyToCompare
    stated_hartree: float
    stored_hartree: float
    weight: int  # sum of the stoichiometric coefficients: each source enters nu times


def _prepare(energy: StateEnergyToCompare) -> SumComparison | _Prepared:
    cited = [p for p in energy.participants if p.calculation is not None]
    if not cited:
        return SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_NO_SOURCE_STATED)
    correction = EnergyCorrectionConvention(energy.correction_convention)
    if correction not in (
        EnergyCorrectionConvention.electronic_only,
        EnergyCorrectionConvention.electronic_plus_zpe,
    ):
        return SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_CONVENTION_NOT_SUMMABLE)
    zero = EnergyZeroConvention(energy.energy_zero_convention)
    if zero != EnergyZeroConvention.absolute and zero not in _SHARED_OFFSET_ZEROS:
        return SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_ENERGY_ZERO_NOT_COMPARABLE)
    if len(cited) != len(energy.participants):
        return SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_SOURCES_INCOMPLETE)
    total = 0.0
    weight = 0
    for participant in energy.participants:
        assert participant.calculation is not None
        value, reason = _stored_energy_hartree(participant.calculation, correction)
        if value is None:
            return SumComparison(COMPARISON_NOT_COMPARED, reason)
        total += participant.stoichiometry * value
        weight += participant.stoichiometry
    return _Prepared(energy, energy.energy_kj_mol / HARTREE_TO_KJ_MOL, total, weight)


def _sum_mismatch(
    prepared: _Prepared,
    *,
    difference_hartree: float,
    tolerance_hartree: float,
    against: _Prepared | None,
) -> CodedValueError:
    energy = prepared.energy
    zero = EnergyZeroConvention(energy.energy_zero_convention).value
    context: dict[str, object] = {
        "field": energy.field,
        "state_key": energy.state_key,
        "energy_zero_convention": zero,
        "correction_convention": EnergyCorrectionConvention(energy.correction_convention).value,
        "stated_energy_kj_mol": energy.energy_kj_mol,
        "stored_sum_kj_mol": prepared.stored_hartree * HARTREE_TO_KJ_MOL,
        "difference_kj_mol": difference_hartree * HARTREE_TO_KJ_MOL,
        "tolerance_kj_mol": tolerance_hartree * HARTREE_TO_KJ_MOL,
    }
    gap = abs(difference_hartree) * HARTREE_TO_KJ_MOL
    tol = tolerance_hartree * HARTREE_TO_KJ_MOL
    if against is None:
        message = (
            f"{energy.field} states {energy.energy_kj_mol!r} kJ/mol for the state "
            f"'{energy.state_key}', but the energies stored for the calculations it cites, summed "
            f"over the state's species by stoichiometry, give "
            f"{prepared.stored_hartree * HARTREE_TO_KJ_MOL!r} kJ/mol (difference {gap:.3e}, "
            f"tolerance {tol:.2e}). State the sum of the cited energies, or cite the right "
            "calculations."
        )
    else:
        context["compared_with_state_key"] = against.energy.state_key
        stated_gap = (prepared.stated_hartree - against.stated_hartree) * HARTREE_TO_KJ_MOL
        stored_gap = (prepared.stored_hartree - against.stored_hartree) * HARTREE_TO_KJ_MOL
        message = (
            f"{energy.field}: on the shared '{zero}' zero, the stated energies of the states "
            f"'{energy.state_key}' and '{against.energy.state_key}' differ by {stated_gap!r} "
            f"kJ/mol, but the sums of the energies stored for the calculations they cite differ "
            f"by {stored_gap!r} kJ/mol (disagreement {gap:.3e}, tolerance {tol:.2e}). One of the "
            "two states' energies or sources is wrong."
        )
    return CodedValueError(E_NETWORK_STATE_ENERGY_SUM_MISMATCH, message, context=context)


def compare_state_energy_sums(energies: Sequence[StateEnergyToCompare]) -> list[SumComparison]:
    """Hold every stated state energy of a solve against the sum of its cited sources.

    Reads the stored energies off the persisted calculation rows, never the payload. Returns one
    :class:`SumComparison` per input, in order: ``agrees``, or ``not_compared`` with a reason
    (see the module docstring for which conventions have a defined sum). A stated energy that
    contradicts its sum raises.

    :raises CodedValueError: ``network_state_energy_sum_mismatch``.
    """
    results: list[SumComparison | None] = [None] * len(energies)
    comparable: list[tuple[int, _Prepared]] = []
    for position, energy in enumerate(energies):
        prepared = _prepare(energy)
        if isinstance(prepared, SumComparison):
            results[position] = prepared
        else:
            comparable.append((position, prepared))

    shared: dict[tuple[EnergyZeroConvention, EnergyCorrectionConvention], list[tuple[int, _Prepared]]] = {}
    for position, prepared in comparable:
        zero = EnergyZeroConvention(prepared.energy.energy_zero_convention)
        if zero == EnergyZeroConvention.absolute:
            # One stated energy and one weighted sum: n = 1 + sum(nu_i).
            tolerance = composite_arithmetic_tolerance_hartree(1 + prepared.weight)
            difference = prepared.stated_hartree - prepared.stored_hartree
            if abs(difference) > tolerance + _TOLERANCE_FLOAT_SLACK:
                raise _sum_mismatch(
                    prepared, difference_hartree=difference, tolerance_hartree=tolerance, against=None
                )
            results[position] = _AGREES
        else:
            key = (zero, EnergyCorrectionConvention(prepared.energy.correction_convention))
            shared.setdefault(key, []).append((position, prepared))

    for group in shared.values():
        if len(group) < 2:
            results[group[0][0]] = SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_NO_SECOND_STATE)
            continue
        # The state with the lowest stated energy is the zero for ``lowest_state``; for any shared
        # offset it is as good a reference as another, and the choice is deterministic.
        reference_position, reference = min(group, key=lambda item: (item[1].stated_hartree, item[0]))
        results[reference_position] = _AGREES
        for position, prepared in group:
            if position == reference_position:
                continue
            # Two stated energies and the two weighted sums: n = 2 + sum(nu_i) + sum(nu_j).
            tolerance = composite_arithmetic_tolerance_hartree(2 + prepared.weight + reference.weight)
            difference = (prepared.stated_hartree - reference.stated_hartree) - (
                prepared.stored_hartree - reference.stored_hartree
            )
            if abs(difference) > tolerance + _TOLERANCE_FLOAT_SLACK:
                raise _sum_mismatch(
                    prepared, difference_hartree=difference, tolerance_hartree=tolerance, against=reference
                )
            results[position] = _AGREES

    return [result for result in results if result is not None]


def collect_state_energy_source_warnings(
    energies: Sequence[StateEnergyToCompare],
    comparisons: Sequence[SumComparison],
) -> list[UploadWarning]:
    """The warnings for sources that cover only some participants, and for energies not compared.

    A state with no source at all is silent: nothing was claimed. A partially sourced state gets
    ``network_state_energy_sources_partial`` (its remedy is to cite the missing participants), and
    is left out of the not-compared warning so one problem is not reported twice.
    """
    warnings: list[UploadWarning] = []
    skipped: list[str] = []
    for energy, comparison in zip(energies, comparisons, strict=True):
        cited = [p for p in energy.participants if p.calculation is not None]
        if cited and len(cited) < len(energy.participants):
            missing = ", ".join(f"'{p.species_key}'" for p in energy.participants if p.calculation is None)
            warnings.append(
                UploadWarning(
                    field=f"solve.state_energies[{energy.index}]",
                    code=W_NETWORK_STATE_ENERGY_SOURCES_PARTIAL,
                    message=(
                        f"The energy of state '{energy.state_key}' is a sum over "
                        f"{len(energy.participants)} species but names a source calculation for "
                        f"{len(cited)}; no source is stated for {missing}. It is stored as given "
                        "and reads back as partial_sources, and the sum is not compared. Cite "
                        "every participant with source_calculation_keys."
                    ),
                )
            )
        elif comparison.status == COMPARISON_NOT_COMPARED and comparison.reason != NOT_COMPARED_NO_SOURCE_STATED:
            skipped.append(f"'{energy.state_key}' ({comparison.reason})")
    if skipped:
        warnings.append(
            UploadWarning(
                field="solve.state_energies",
                code=W_NETWORK_STATE_ENERGY_SUM_NOT_COMPARED,
                message=(
                    "State energies that could not be held against the sum of the energies stored "
                    f"for the calculations they cite: {'; '.join(skipped)}. They are stored as not "
                    "compared with their reason, and each rests on the stated number alone."
                ),
            )
        )
    return warnings
