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
(``app.services.transition_state_validation``, #637); this module is that rule
for the network route. Both read one table of which types carry which energy
kind, :mod:`app.services.energy_source_types` (#669), which includes the
``composite`` calculation type (ADR 0021): it carries both an electronic energy
and an E0 in ``calc_composite_result``.

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

The floor, for every kind and for the two energy roles, is the shared table's
electronic set union its E0 set: ``sp``, ``opt``, ``freq`` or ``composite``.
``irc``, ``scan`` and ``path_search`` report the energies of points along a
path, and ``conf`` is a conformer search, so none is ever the source of a state
or barrier energy.

The frequency roles take a ``freq`` and nothing else (#669). ``well_freq`` and
``barrier_freq`` name the job whose modes the master equation reads, and a
``composite`` stores only a ZPE, not the modes. Citing an ``sp`` there would
also be a mistake the energy roles above cannot see.

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
* ``electronic_plus_zpe``, per participant, one of:

  - a ``composite`` that stores an ``e0`` (its E0);
  - an ``sp`` (or ``opt``) electronic energy plus the zero-point energy of the participant's own
    ``freq``, named as ``zpe_calculation_key`` (#693): ``E + s * ZPE`` with the state's declared
    ``zpe_scale_factor`` ``s``. The two must be at one geometry (an sp's or freq's input, an opt's
    final), each declared exactly once, as #677 pairs a transition state's. TCKDB stores the
    producer's unscaled ZPE, so with no factor stated the state is *not compared*
    (``zpe_scaling_unstated``) whatever the numbers are: unity is never assumed, even when the
    unscaled sum happens to agree. A freq with no stored ZPE is ``zpe_not_stated``; an sp and a freq
    not at one geometry are ``geometry_not_paired``, and no ZPE is ever borrowed from a sibling;
  - a single atom's ``sp``/``opt``: it has no vibrations, so its electronic energy is its E0. An
    atom is read from the cited calculation's own stored geometry (one atom), never inferred.

  Any other participant (an ``sp``/``opt`` with no ZPE cited, a bare ``freq``) cannot form the sum:
  the state is *not compared* (``zpe_not_in_source``), never guessed from another calculation.
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

Three bands, because a stated kJ/mol is rarely the unrounded hartree conversion. Within the
printed-precision tolerance the energies agree. Beyond it but within an honest-rounding allowance
(half a rounding unit per stated energy, ``energy_precision_kj_mol`` when stated and 1 kcal/mol
otherwise, plus 1e-6 of the value on an absolute energy for the spread of conversion constants) the
stated number may be a correctly rounded or converted one, which cannot be told from a wrong one:
it is stored as not compared, ``stated_precision_unknown``, with a warning, unless the producer
stated the unit, in which case the rounding is accounted for and it agrees. Only beyond the
allowance is the number contradicted and the upload refused. Precision is never inferred from the
digits of the number.

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

import functools
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from tckdb_schemas.fragments.calculation import (
    COMPOSITE_PRINTED_ROUNDING_HARTREE,
    composite_arithmetic_tolerance_hartree,
)
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
from app.services.energy_source_types import (
    ELECTRONIC_ENERGY_SOURCE_TYPES,
    FREQUENCY_ROLE_TYPES,
    STATIONARY_POINT_ENERGY_TYPES,
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

#: A state or barrier energy states a convention that includes a vibrational term but cites only a
#: bare electronic energy (an ``sp`` or ``opt``) for it, and the upload carries no ``freq`` of that
#: subject anywhere. An absence, so a warning (ADR 0008): the energy may well be right.
W_NETWORK_ENERGY_ZPE_SOURCE_ABSENT = "network_energy_zpe_source_absent"

#: The conventions that add a vibrational (zero-point or thermal) term to an electronic energy.
#: ``electronic_only`` does not, and ``other`` says nothing about what it includes.
COMPOSED_CONVENTIONS: frozenset[EnergyCorrectionConvention] = frozenset(
    {
        EnergyCorrectionConvention.electronic_plus_zpe,
        EnergyCorrectionConvention.atom_and_bond_corrected,
        EnergyCorrectionConvention.thermal_enthalpy_298k,
    }
)

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
NOT_COMPARED_STATED_PRECISION_UNKNOWN = "stated_precision_unknown"
NOT_COMPARED_ZPE_NOT_STATED = "zpe_not_stated"
NOT_COMPARED_GEOMETRY_NOT_PAIRED = "geometry_not_paired"
NOT_COMPARED_ZPE_SCALING_UNSTATED = "zpe_scaling_unstated"

#: A stated ``zpe_scale_factor`` is expected to carry at least four decimals (or be exactly the
#: multiplier used), so its rounding error is at most half a unit of the fourth place, 5e-5. The
#: same bound the transition-state energy check uses for its ``zpe_scale_factor``.
_ZPE_SCALE_FACTOR_PRINTED_ERROR = 5e-5

#: The rounding unit assumed for a stated energy whose producer states none: 1 kcal/mol, the
#: coarsest unit energies are routinely reported in. Half of it (about 2.09 kJ/mol) is the
#: honest-rounding allowance per stated energy. Precision is never inferred from the digits of the
#: JSON number.
DEFAULT_ENERGY_PRECISION_KJ_MOL = 4.184

#: Relative spread of the hartree-to-kJ/mol constants in use (2625.4996, 2625.5, 627.509 x 4.184):
#: an absolute energy converted with another constant is off by this fraction of itself.
CONVERSION_CONSTANT_SPREAD = 1e-6

#: Slack for float noise in ``|stated - stored| <= tolerance``; the value
#: ``tckdb_schemas.composite_total`` and the transition-state comparison use.
_TOLERANCE_FLOAT_SLACK = 1e-12

#: Calculation types that report the energy of a stationary point at all, and the
#: ones that carry a bare electronic energy: the shared table (#669).
_STATIONARY_POINT_ENERGY_TYPES: frozenset[CalculationType] = STATIONARY_POINT_ENERGY_TYPES
_ELECTRONIC_ENERGY_TYPES: frozenset[CalculationType] = ELECTRONIC_ENERGY_SOURCE_TYPES

#: Accepted source types by the energy kind a state or barrier states. A kind
#: absent from this map falls back to the floor (see the module docstring).
_SOURCE_TYPES_BY_CORRECTION: dict[
    EnergyCorrectionConvention, frozenset[CalculationType]
] = {
    EnergyCorrectionConvention.electronic_only: _ELECTRONIC_ENERGY_TYPES,
}

#: The ``source_calculations`` roles that name a frequency job: the modes are read, so
#: only a ``freq`` will do.
_FREQUENCY_ROLES: frozenset[NetworkSolveCalculationRole] = frozenset(
    {
        NetworkSolveCalculationRole.well_freq,
        NetworkSolveCalculationRole.barrier_freq,
    }
)

#: The ``source_calculations`` roles that name an energy source. The
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


def assert_state_zpe_source_type(calculation: Calculation, *, field: str) -> None:
    """Refuse a ``zpe_calculation_key`` that is not a ``freq`` calculation (#693).

    Only a frequency calculation stores a zero-point energy.

    :raises CodedValueError: ``network_energy_source_type_mismatch``.
    """
    if calculation.type == CalculationType.freq:
        return
    _refuse(
        calculation,
        frozenset({CalculationType.freq}),
        field=field,
        stated="zpe_calculation_key",
        stated_value="freq",
    )


def assert_network_source_role_type(
    calculation: Calculation,
    role: NetworkSolveCalculationRole,
    *,
    field: str,
) -> None:
    """Refuse a role link to a calculation type that cannot play the role.

    ``well_energy`` / ``barrier_energy`` state no energy kind, so only the floor
    applies (a type with no energy is refused). ``well_freq`` / ``barrier_freq``
    take a ``freq`` only (#669). Other roles are not constrained here.

    :raises CodedValueError: ``network_energy_source_type_mismatch``.
    """
    role = NetworkSolveCalculationRole(role)
    if role in _FREQUENCY_ROLES:
        if calculation.type not in FREQUENCY_ROLE_TYPES:
            _refuse(
                calculation,
                FREQUENCY_ROLE_TYPES,
                field=field,
                stated="role",
                stated_value=role.value,
            )
        return
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
    """One participant of a state, and the calculation cited for it (None: none cited).

    ``zpe_calculation`` is the participant's own ``freq`` whose zero-point energy is added to the
    energy of ``calculation`` (``electronic_plus_zpe``, #693). ``electronic_geometry_ids`` and
    ``zpe_geometry_ids`` are the geometries the two calculations' energies are at, read from the
    persisted rows by the caller; ``None`` means not looked up, which pairs nothing. ``is_atom`` is
    true only when the cited calculation's stored geometry is a single atom.
    """

    species_key: str
    stoichiometry: int
    calculation: Calculation | None
    zpe_calculation: Calculation | None = None
    electronic_geometry_ids: frozenset[int] | None = None
    zpe_geometry_ids: frozenset[int] | None = None
    is_atom: bool = False


@dataclass(frozen=True)
class StateEnergyToCompare:
    """A stated state energy with every participant of its state and their sources."""

    index: int
    state_key: str
    energy_kj_mol: float
    energy_zero_convention: EnergyZeroConvention
    correction_convention: EnergyCorrectionConvention
    participants: tuple[ParticipantSource, ...]
    energy_precision_kj_mol: float | None = None
    zpe_scale_factor: float | None = None

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


def _electronic_energy_hartree(calculation: Calculation) -> float | None:
    """The bare electronic energy a calculation stores (sp energy, opt final, composite electronic)."""
    if calculation.type == CalculationType.sp:
        sp = calculation.sp_result
        return None if sp is None else sp.electronic_energy_hartree
    if calculation.type == CalculationType.opt:
        opt = calculation.opt_result
        return None if opt is None else opt.final_energy_hartree
    if calculation.type == CalculationType.composite:
        composite = calculation.composite_result
        return None if composite is None else composite.electronic_energy_hartree
    return None


def _stored_energy_hartree(
    participant: ParticipantSource,
    correction: EnergyCorrectionConvention,
    zpe_scale_factor: float | None,
) -> tuple[float | None, float, str | None]:
    """``(energy, rounded_weight, None)`` or ``(None, 0, reason)``: the stored energy of the kind stated.

    ``rounded_weight`` is how many rounded printed quantities one unit of the participant's
    stoichiometry contributes to the comparison (1 for a bare energy; ``1 + s`` plus the effect of
    the factor's own rounding for an sp plus a scaled ZPE).
    """
    calculation = participant.calculation
    assert calculation is not None
    if correction != EnergyCorrectionConvention.electronic_plus_zpe:
        value = _electronic_energy_hartree(calculation)
        return (value, 1.0, None) if value is not None else (None, 0.0, NOT_COMPARED_STORED_ENERGY_NOT_STATED)

    zpe_calculation = participant.zpe_calculation
    if zpe_calculation is not None:
        # An electronic energy plus the zero-point energy of the participant's own freq.
        electronic = _electronic_energy_hartree(calculation)
        if electronic is None:
            return None, 0.0, NOT_COMPARED_STORED_ENERGY_NOT_STATED
        freq = zpe_calculation.freq_result
        zpe = None if freq is None else freq.zpe_hartree
        if zpe is None:
            return None, 0.0, NOT_COMPARED_ZPE_NOT_STATED
        electronic_geometry = participant.electronic_geometry_ids
        if (
            electronic_geometry is None
            or len(electronic_geometry) != 1
            or electronic_geometry != participant.zpe_geometry_ids
        ):
            return None, 0.0, NOT_COMPARED_GEOMETRY_NOT_PAIRED
        if zpe_scale_factor is None:
            return None, 0.0, NOT_COMPARED_ZPE_SCALING_UNSTATED
        # E, ZPE (weight s) and the factor itself, stated to at least four decimals: a relative
        # error of at most 5e-5 moves the term by 5e-5 * zpe.
        weight = 1.0 + zpe_scale_factor + abs(zpe) * _ZPE_SCALE_FACTOR_PRINTED_ERROR / COMPOSITE_PRINTED_ROUNDING_HARTREE
        return electronic + zpe_scale_factor * zpe, weight, None
    if calculation.type == CalculationType.composite:
        composite = calculation.composite_result
        value = None if composite is None else composite.e0_hartree
        return (value, 1.0, None) if value is not None else (None, 0.0, NOT_COMPARED_STORED_ENERGY_NOT_STATED)
    if participant.is_atom and calculation.type in (CalculationType.sp, CalculationType.opt):
        # An atom has no vibrations: its zero-point energy is 0 and its electronic energy is its E0.
        value = _electronic_energy_hartree(calculation)
        return (value, 1.0, None) if value is not None else (None, 0.0, NOT_COMPARED_STORED_ENERGY_NOT_STATED)
    return None, 0.0, NOT_COMPARED_ZPE_NOT_IN_SOURCE


@dataclass(frozen=True)
class _Prepared:
    energy: StateEnergyToCompare
    stated_hartree: float
    stored_hartree: float
    weight: float  # rounded printed quantities of the weighted sum: each source enters nu times


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
    weight = 0.0
    for participant in energy.participants:
        value, term_weight, reason = _stored_energy_hartree(participant, correction, energy.zpe_scale_factor)
        if value is None:
            return SumComparison(COMPARISON_NOT_COMPARED, reason)
        total += participant.stoichiometry * value
        weight += participant.stoichiometry * term_weight
    return _Prepared(energy, energy.energy_kj_mol / HARTREE_TO_KJ_MOL, total, weight)


def _allowance_kj_mol(precision_kj_mol: float | None) -> tuple[float, bool]:
    """Half a rounding unit of one stated energy, and whether that unit was stated.

    The unit is the producer's ``energy_precision_kj_mol`` when it states one, otherwise the
    coarse default of 1 kcal/mol (4.184 kJ/mol), so half of it is about 2.09 kJ/mol. Precision is
    never inferred from the digits of the JSON number.
    """
    if precision_kj_mol is None:
        return DEFAULT_ENERGY_PRECISION_KJ_MOL / 2.0, False
    return precision_kj_mol / 2.0, True


def _classify(gap_hartree: float, tolerance_hartree: float, allowance_kj_mol: float, *, all_stated: bool) -> str:
    """``agrees``, ``imprecise`` or ``bad`` for one comparison gap, in hartree.

    Three bands (the pattern of #677's scaled-ZPE energies): within the printed-precision
    tolerance the energies agree; beyond it but within the honest-rounding allowance the stated
    value may be a correctly rounded or unit-converted one, which cannot be told from a wrong one,
    so it is ``imprecise`` (stored as not compared) unless the producer stated its precision, in
    which case the rounding is accounted for and it agrees; only beyond the allowance is it
    contradicted.
    """
    gap = abs(gap_hartree)
    if gap <= tolerance_hartree + _TOLERANCE_FLOAT_SLACK:
        return "agrees"
    if gap <= tolerance_hartree + allowance_kj_mol / HARTREE_TO_KJ_MOL + _TOLERANCE_FLOAT_SLACK:
        return "agrees" if all_stated else "imprecise"
    return "bad"


def _sum_mismatch(
    prepared: _Prepared,
    *,
    difference_hartree: float,
    tolerance_hartree: float,
    allowance_kj_mol: float,
    against: _Prepared | None,
    inconsistent_with: Sequence[_Prepared] = (),
) -> CodedValueError:
    energy = prepared.energy
    zero = EnergyZeroConvention(energy.energy_zero_convention).value
    gap = abs(difference_hartree) * HARTREE_TO_KJ_MOL
    bound = tolerance_hartree * HARTREE_TO_KJ_MOL + allowance_kj_mol
    context: dict[str, object] = {
        "field": energy.field,
        "state_key": energy.state_key,
        "energy_zero_convention": zero,
        "correction_convention": EnergyCorrectionConvention(energy.correction_convention).value,
        "stated_energy_kj_mol": energy.energy_kj_mol,
        "stored_sum_kj_mol": prepared.stored_hartree * HARTREE_TO_KJ_MOL,
        "difference_kj_mol": difference_hartree * HARTREE_TO_KJ_MOL,
        "tolerance_kj_mol": tolerance_hartree * HARTREE_TO_KJ_MOL,
        "allowance_kj_mol": allowance_kj_mol,
    }
    if energy.zpe_scale_factor is not None:
        context["zpe_scale_factor"] = energy.zpe_scale_factor
    if against is None:
        message = (
            f"{energy.field} states {energy.energy_kj_mol!r} kJ/mol for the state "
            f"'{energy.state_key}', but the energies stored for the calculations it cites, summed "
            f"over the state's species by stoichiometry, give "
            f"{prepared.stored_hartree * HARTREE_TO_KJ_MOL!r} kJ/mol (difference {gap:.3e}, beyond "
            f"the {bound:.3e} that printed-precision and rounding of the stated number explain). "
            "State the sum of the cited energies, or cite the right calculations."
        )
    else:
        context["compared_with_state_key"] = against.energy.state_key
        context["compared_with_field"] = against.energy.field
        context["inconsistent_state_keys"] = [p.energy.state_key for p in inconsistent_with]
        stated_gap = (prepared.stated_hartree - against.stated_hartree) * HARTREE_TO_KJ_MOL
        stored_gap = (prepared.stored_hartree - against.stored_hartree) * HARTREE_TO_KJ_MOL
        message = (
            f"{energy.field}: on the shared '{zero}' zero, the stated energies of the states "
            f"'{energy.state_key}' and '{against.energy.state_key}' differ by {stated_gap!r} "
            f"kJ/mol, but the sums of the energies stored for the calculations they cite differ "
            f"by {stored_gap!r} kJ/mol (disagreement {gap:.3e}, beyond the {bound:.3e} that "
            "printed-precision and rounding explain). Either state may be the wrong one: one of the two "
            "states' energies or sources "
            "is wrong."
        )
    return CodedValueError(E_NETWORK_STATE_ENERGY_SUM_MISMATCH, message, context=context)


_IMPRECISE = SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_STATED_PRECISION_UNKNOWN)


def _pair_of(
    pairs: dict[tuple[int, int], tuple[str, float, float, float]], a: int, b: int
) -> tuple[str, float, float, float]:
    return pairs[(min(a, b), max(a, b))]


def compare_state_energy_sums(energies: Sequence[StateEnergyToCompare]) -> list[SumComparison]:
    """Hold every stated state energy of a solve against the sum of its cited sources.

    Reads the stored energies off the persisted calculation rows, never the payload. Returns one
    :class:`SumComparison` per input, in order: ``agrees``, or ``not_compared`` with a reason
    (see the module docstring for which conventions have a defined sum). A stated energy that
    contradicts its sum beyond printed precision *and* beyond the honest-rounding allowance
    raises. Between the two the energy is ``not_compared`` / ``stated_precision_unknown``, unless
    the producer stated ``energy_precision_kj_mol`` for it.

    Several states on a shared zero are compared pairwise, and a pair beyond the allowance always
    refuses. The majority vote only picks the state named: the outlier inconsistent with a strict
    majority of the others, else the state with the most bad pairs. Its partners are listed in
    ``inconsistent_state_keys``; with two states either may be the wrong one.

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
            # One stated energy and one weighted sum: n = 1 + sum(nu_i). The allowance is half a
            # rounding unit of the stated energy plus the spread of hartree-to-kJ/mol constants
            # in use (about 1e-6 relative) on an energy this large.
            half_unit, stated = _allowance_kj_mol(prepared.energy.energy_precision_kj_mol)
            allowance = half_unit + CONVERSION_CONSTANT_SPREAD * abs(prepared.energy.energy_kj_mol)
            tolerance = composite_arithmetic_tolerance_hartree(1 + prepared.weight)
            difference = prepared.stated_hartree - prepared.stored_hartree
            band = _classify(difference, tolerance, allowance, all_stated=stated)
            if band == "bad":
                raise _sum_mismatch(
                    prepared,
                    difference_hartree=difference,
                    tolerance_hartree=tolerance,
                    allowance_kj_mol=allowance,
                    against=None,
                )
            results[position] = _AGREES if band == "agrees" else _IMPRECISE
        else:
            key = (zero, EnergyCorrectionConvention(prepared.energy.correction_convention))
            shared.setdefault(key, []).append((position, prepared))

    for group in shared.values():
        if len(group) < 2:
            results[group[0][0]] = SumComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_NO_SECOND_STATE)
            continue
        # pair (a, b) -> (band, tolerance, allowance, gap). Two stated energies and the two
        # weighted sums: n = 2 + sum(nu_a) + sum(nu_b).
        pairs: dict[tuple[int, int], tuple[str, float, float, float]] = {}
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                prepared_a, prepared_b = group[a][1], group[b][1]
                unit_a, stated_a = _allowance_kj_mol(prepared_a.energy.energy_precision_kj_mol)
                unit_b, stated_b = _allowance_kj_mol(prepared_b.energy.energy_precision_kj_mol)
                tolerance = composite_arithmetic_tolerance_hartree(2 + prepared_a.weight + prepared_b.weight)
                difference = (prepared_a.stated_hartree - prepared_b.stated_hartree) - (
                    prepared_a.stored_hartree - prepared_b.stored_hartree
                )
                allowance = unit_a + unit_b
                band = _classify(difference, tolerance, allowance, all_stated=stated_a and stated_b)
                pairs[(a, b)] = (band, tolerance, allowance, difference)

        _pair = functools.partial(_pair_of, pairs)

        count = len(group)
        inconsistent = {
            a: [b for b in range(count) if b != a and _pair(a, b)[0] == "bad"] for a in range(count)
        }
        # Any pair beyond the allowance is a provable contradiction: refuse. The majority vote only
        # chooses whom to name: the outlier inconsistent with a strict majority of the others; when
        # nobody has a majority (two states, or a chain of drifting offsets) the state with the
        # most bad pairs, the earliest on a tie.
        in_conflict = [a for a in range(count) if inconsistent[a]]
        if in_conflict:
            outliers = [a for a in in_conflict if 2 * len(inconsistent[a]) > count - 1]
            a = outliers[0] if outliers else max(in_conflict, key=lambda x: (len(inconsistent[x]), -x))
            partners = sorted(inconsistent[a], key=lambda b: group[b][0])
            b = partners[0]
            _band, tolerance, allowance, difference = _pair(a, b)
            raise _sum_mismatch(
                group[a][1],
                difference_hartree=difference,
                tolerance_hartree=tolerance,
                allowance_kj_mol=allowance,
                against=group[b][1],
                inconsistent_with=[group[p][1] for p in partners],
            )
        for a in range(count):
            bands = [_pair(a, b)[0] for b in range(count) if b != a]
            results[group[a][0]] = _IMPRECISE if "imprecise" in bands else _AGREES

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
    skipped: list[dict[str, str]] = []
    reasons: set[str] = set()
    for energy, comparison in zip(energies, comparisons, strict=True):
        cited = [p for p in energy.participants if p.calculation is not None]
        if cited and len(cited) < len(energy.participants):
            missing = [p.species_key for p in energy.participants if p.calculation is None]
            warnings.append(
                UploadWarning.from_code(
                    field=f"solve.state_energies[{energy.index}]",
                    code=W_NETWORK_STATE_ENERGY_SOURCES_PARTIAL,
                    context={
                        "participants": len(energy.participants),
                        "cited_participants": len(cited),
                        "depositor_supplied": {"state_key": energy.state_key, "uncited_species_keys": missing},
                    },
                )
            )
        elif comparison.status == COMPARISON_NOT_COMPARED and comparison.reason != NOT_COMPARED_NO_SOURCE_STATED:
            skipped.append({"state_key": energy.state_key, "reason": comparison.reason or ""})
            if comparison.reason:
                reasons.add(comparison.reason)
    if skipped:
        warnings.append(
            UploadWarning.from_code(
                field="solve.state_energies",
                code=W_NETWORK_STATE_ENERGY_SUM_NOT_COMPARED,
                context={"reasons": sorted(reasons), "depositor_supplied": {"not_compared": skipped}},
            )
        )
    return warnings


def collect_zpe_source_absent_warnings(
    energies: Sequence[StateEnergyToCompare],
    barriers: Sequence[tuple[int, str, EnergyCorrectionConvention, Calculation | None]],
    *,
    species_entries_with_freq: Collection[int],
    transition_state_entries_with_freq: Collection[int],
) -> list[UploadWarning]:
    """Warn when a composed energy cites a bare electronic energy and the upload has no freq for it (#669).

    A composed convention (``electronic_plus_zpe``, ``atom_and_bond_corrected``,
    ``thermal_enthalpy_298k``) adds a vibrational term to an electronic energy. Citing an ``sp`` or
    ``opt`` for it with no ``freq`` of that subject anywhere in the upload leaves the vibrational term
    with no source at all: the energy is stored as stated and nothing says where its ZPE came from.
    A ``composite`` holds its own E0 and a single atom has no vibrations, so neither is flagged; a
    ``zpe_calculation_key`` or any ``freq`` the upload declares for the subject counts as a source
    (whether it is the *right* one is the state-energy sum check's business).

    :param energies: The solve's state energies, as built for the sum check.
    :param barriers: ``(index, transition_state_key, convention, cited calculation or None)`` per barrier.
    :param species_entries_with_freq: Species entries that own a ``freq`` calculation in this upload.
    :param transition_state_entries_with_freq: Transition-state entries that own one.
    """
    bare = (CalculationType.sp, CalculationType.opt)
    warnings: list[UploadWarning] = []
    for energy in energies:
        if energy.correction_convention not in COMPOSED_CONVENTIONS:
            continue
        missing = [
            p.species_key
            for p in energy.participants
            if p.calculation is not None
            and p.calculation.type in bare
            and not p.is_atom
            and p.zpe_calculation is None
            and p.calculation.species_entry_id not in species_entries_with_freq
        ]
        if missing:
            warnings.append(
                UploadWarning.from_code(
                    field=f"solve.state_energies[{energy.index}]",
                    code=W_NETWORK_ENERGY_ZPE_SOURCE_ABSENT,
                    context={
                        "correction_convention": energy.correction_convention.value,
                        "subject": "state_energy",
                        "depositor_supplied": {"state_key": energy.state_key, "species_keys": missing},
                    },
                )
            )
    for index, ts_key, convention, calculation in barriers:
        if (
            convention in COMPOSED_CONVENTIONS
            and calculation is not None
            and calculation.type in bare
            and calculation.transition_state_entry_id not in transition_state_entries_with_freq
        ):
            warnings.append(
                UploadWarning.from_code(
                    field=f"solve.channel_barriers[{index}]",
                    code=W_NETWORK_ENERGY_ZPE_SOURCE_ABSENT,
                    context={
                        "correction_convention": convention.value,
                        "subject": "channel_barrier",
                        "depositor_supplied": {"transition_state_key": ts_key},
                    },
                )
            )
    return warnings
