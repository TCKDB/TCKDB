"""A composed network energy that cites a bare electronic energy and has no freq warns (#669).

``electronic_plus_zpe``, ``atom_and_bond_corrected`` and ``thermal_enthalpy_298k`` add a
vibrational term to an electronic energy. When the cited source is an ``sp`` or ``opt`` and
the upload declares no ``freq`` for that subject, the term has no source at all. An absence,
therefore a warning (ADR 0008), never a refusal. The calculations here are stand-ins carrying
only the three attributes the rule reads, so each case isolates one condition.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.models.common import CalculationType, EnergyCorrectionConvention, EnergyZeroConvention
from app.services.network_energy_sources import (
    COMPOSED_CONVENTIONS,
    W_NETWORK_ENERGY_ZPE_SOURCE_ABSENT,
    ParticipantSource,
    StateEnergyToCompare,
    collect_zpe_source_absent_warnings,
)

CODE = W_NETWORK_ENERGY_ZPE_SOURCE_ABSENT
C = EnergyCorrectionConvention


def _calc(kind: CalculationType, *, species: int | None = None, ts: int | None = None):
    return SimpleNamespace(type=kind, species_entry_id=species, transition_state_entry_id=ts)


def _state(convention, *participants: ParticipantSource, index: int = 0) -> StateEnergyToCompare:
    return StateEnergyToCompare(
        index=index,
        state_key="w1",
        energy_kj_mol=0.0,
        energy_zero_convention=EnergyZeroConvention.lowest_state,
        correction_convention=convention,
        participants=participants,
    )


def _participant(name, calculation, *, zpe=None, is_atom=False) -> ParticipantSource:
    return ParticipantSource(name, 1, calculation, zpe, is_atom=is_atom)


def _warn(energies=(), barriers=(), *, species=(), ts=()):
    return collect_zpe_source_absent_warnings(
        list(energies), list(barriers), species_entries_with_freq=set(species), transition_state_entries_with_freq=set(ts)
    )


@pytest.mark.parametrize("convention", sorted(COMPOSED_CONVENTIONS, key=lambda c: c.value))
@pytest.mark.parametrize("bare", [CalculationType.sp, CalculationType.opt])
def test_a_composed_state_energy_on_a_bare_energy_with_no_freq_warns(convention, bare) -> None:
    (warning,) = _warn([_state(convention, _participant("ethyl", _calc(bare, species=1)))])
    assert warning.code == CODE
    assert warning.field == "solve.state_energies[0]"
    assert "'ethyl'" in warning.message and convention.value in warning.message


def test_the_three_composed_conventions_are_exactly_those_with_a_vibrational_term() -> None:
    assert {c.value for c in COMPOSED_CONVENTIONS} == {
        "electronic_plus_zpe",
        "atom_and_bond_corrected",
        "thermal_enthalpy_298k",
    }


@pytest.mark.parametrize("convention", [C.electronic_only, C.other])
def test_a_convention_without_a_stated_vibrational_term_is_silent(convention) -> None:
    assert _warn([_state(convention, _participant("ethyl", _calc(CalculationType.sp, species=1)))]) == []


def test_each_thing_that_supplies_a_zpe_source_silences_it() -> None:
    sp = _calc(CalculationType.sp, species=1)
    freq = _calc(CalculationType.freq, species=1)
    conv = C.electronic_plus_zpe
    assert _warn([_state(conv, _participant("a", sp, zpe=freq))]) == [], "a cited zpe_calculation"
    assert _warn([_state(conv, _participant("a", sp))], species=[1]) == [], "a freq of the species in the upload"
    assert _warn([_state(conv, _participant("a", sp, is_atom=True))]) == [], "an atom has no vibrations"
    assert _warn([_state(conv, _participant("a", _calc(CalculationType.composite, species=1)))]) == [], "a composite's E0"
    assert _warn([_state(conv, _participant("a", freq))]) == [], "a freq cited directly is its own source"
    assert _warn([_state(conv, _participant("a", None))]) == [], "nothing cited: nothing claimed"
    other_species = _warn([_state(conv, _participant("a", sp))], species=[2])
    assert len(other_species) == 1, "another species' freq is not this species' source"


def test_one_warning_per_energy_names_every_participant_missing_a_source() -> None:
    conv = C.electronic_plus_zpe
    state = _state(
        conv,
        _participant("a", _calc(CalculationType.sp, species=1)),
        _participant("b", _calc(CalculationType.opt, species=2)),
        _participant("c", _calc(CalculationType.sp, species=3)),
    )
    (warning,) = _warn([state], species=[3])
    assert "'a'" in warning.message and "'b'" in warning.message and "'c'" not in warning.message


def test_a_composed_barrier_on_a_bare_energy_warns_unless_the_ts_has_a_freq() -> None:
    barrier = (2, "ts_elim", C.electronic_plus_zpe, _calc(CalculationType.sp, ts=7))
    (warning,) = _warn(barriers=[barrier])
    assert warning.code == CODE and warning.field == "solve.channel_barriers[2]"
    assert "ts_elim" in warning.message
    assert _warn(barriers=[barrier], ts=[7]) == []
    assert _warn(barriers=[(0, "t", C.electronic_only, _calc(CalculationType.sp, ts=7))]) == []
    assert _warn(barriers=[(0, "t", C.electronic_plus_zpe, None)]) == []
    assert _warn(barriers=[(0, "t", C.electronic_plus_zpe, _calc(CalculationType.composite, ts=7))]) == []
