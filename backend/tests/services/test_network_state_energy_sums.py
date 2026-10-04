"""``compare_state_energy_sums``: which sums are defined, and what each outcome is (#678).

A pure function over persisted-calculation-shaped objects, so the stored-energy kinds the upload
fixtures cannot easily build (a composite's E0, an opt's final energy, a result row that is
missing) are covered here with stand-ins that carry exactly the attributes the function reads.
The numbers are written out by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.error_contract import CodedValueError
from app.chemistry.units import HARTREE_TO_KJ_MOL as H
from app.db.models.common import CalculationType, EnergyCorrectionConvention, EnergyZeroConvention
from app.services.network_energy_sources import (
    ParticipantSource,
    StateEnergyToCompare,
    collect_state_energy_source_warnings,
    compare_state_energy_sums,
)

_ABS = EnergyZeroConvention.absolute
_ELEC = EnergyCorrectionConvention.electronic_only
_E0 = EnergyCorrectionConvention.electronic_plus_zpe


def _sp(hartree: float | None):
    return SimpleNamespace(
        type=CalculationType.sp,
        sp_result=None if hartree is None else SimpleNamespace(electronic_energy_hartree=hartree),
    )


def _opt(hartree: float):
    return SimpleNamespace(type=CalculationType.opt, opt_result=SimpleNamespace(final_energy_hartree=hartree))


def _composite(electronic: float | None, e0: float | None):
    return SimpleNamespace(
        type=CalculationType.composite,
        composite_result=SimpleNamespace(electronic_energy_hartree=electronic, e0_hartree=e0),
    )


def _freq():
    return SimpleNamespace(type=CalculationType.freq)


def _state(index, key, kj, participants, *, zero=_ABS, correction=_ELEC) -> StateEnergyToCompare:
    return StateEnergyToCompare(
        index=index,
        state_key=key,
        energy_kj_mol=kj,
        energy_zero_convention=zero,
        correction_convention=correction,
        participants=tuple(ParticipantSource(*p) for p in participants),
    )


def test_an_sp_and_an_opt_are_summed_with_their_coefficients() -> None:
    state = _state(0, "s", (2 * -10.0 + -3.5) * H, [("a", 2, _sp(-10.0)), ("b", 1, _opt(-3.5))])
    (result,) = compare_state_energy_sums([state])
    assert (result.status, result.reason) == ("agrees", None)


def test_a_composite_contributes_its_electronic_energy_for_electronic_only() -> None:
    state = _state(0, "s", -7.0 * H, [("a", 1, _composite(-7.0, -6.9))])
    assert compare_state_energy_sums([state])[0].status == "agrees"


def test_electronic_plus_zpe_sums_composite_e0_values() -> None:
    state = _state(
        0, "s", (-10.9 + -3.4) * H, [("a", 1, _composite(-11.0, -10.9)), ("b", 1, _composite(-3.5, -3.4))],
        correction=_E0,
    )
    assert compare_state_energy_sums([state])[0].status == "agrees"


def test_electronic_plus_zpe_that_contradicts_the_e0_sum_is_refused() -> None:
    electronic_sum = (-11.0 + -3.5) * H  # a depositor who sent the ZPE-free sum as an E0
    state = _state(
        0, "s", electronic_sum, [("a", 1, _composite(-11.0, -10.9)), ("b", 1, _composite(-3.5, -3.4))], correction=_E0
    )
    with pytest.raises(CodedValueError) as raised:
        compare_state_energy_sums([state])
    assert raised.value.code == "network_state_energy_sum_mismatch"


@pytest.mark.parametrize(
    ("calculation", "reason"),
    [
        (_sp(-10.0), "zpe_not_in_source"),  # an sp carries no zero-point energy
        (_opt(-10.0), "zpe_not_in_source"),
        (_freq(), "zpe_not_in_source"),  # a freq carries no electronic energy
        (_composite(-11.0, None), "stored_energy_not_stated"),
    ],
)
def test_electronic_plus_zpe_without_an_e0_in_the_source_is_not_compared(calculation, reason) -> None:
    state = _state(0, "s", 1.0, [("a", 1, calculation)], correction=_E0)
    (result,) = compare_state_energy_sums([state])
    assert (result.status, result.reason) == ("not_compared", reason)


def test_a_source_that_stores_no_energy_is_not_compared_and_never_a_contradiction() -> None:
    state = _state(0, "s", 1.0, [("a", 1, _sp(None))])
    assert compare_state_energy_sums([state])[0].reason == "stored_energy_not_stated"
    assert compare_state_energy_sums([_state(0, "s", 1.0, [("a", 1, _freq())])])[0].reason == "stored_energy_not_stated"


@pytest.mark.parametrize(
    "correction",
    [
        EnergyCorrectionConvention.atom_and_bond_corrected,
        EnergyCorrectionConvention.thermal_enthalpy_298k,
        EnergyCorrectionConvention.other,
    ],
)
def test_corrections_not_stored_per_source_are_not_summable(correction) -> None:
    state = _state(0, "s", 1.0, [("a", 1, _sp(-10.0))], correction=correction)
    assert compare_state_energy_sums([state])[0].reason == "convention_not_summable"


@pytest.mark.parametrize("zero", [EnergyZeroConvention.separated_reactants, EnergyZeroConvention.other])
def test_a_zero_no_other_state_shares_is_not_comparable(zero) -> None:
    state = _state(0, "s", 1.0, [("a", 1, _sp(-10.0))], zero=zero)
    assert compare_state_energy_sums([state])[0].reason == "energy_zero_not_comparable"


def test_sources_covering_some_participants_are_incomplete_not_summed() -> None:
    state = _state(0, "s", 1.0, [("a", 1, _sp(-10.0)), ("b", 1, None)])
    assert compare_state_energy_sums([state])[0].reason == "sources_incomplete"


def test_no_source_is_not_compared_for_any_convention() -> None:
    state = _state(0, "s", 1.0, [("a", 1, None), ("b", 1, None)], correction=EnergyCorrectionConvention.other)
    assert compare_state_energy_sums([state])[0].reason == "no_source_stated"


def test_an_unvalidated_string_convention_is_normalised() -> None:
    state = _state(0, "s", -10.0 * H, [("a", 1, _sp(-10.0))], zero="absolute", correction="electronic_only")
    assert compare_state_energy_sums([state])[0].status == "agrees"


def test_a_shared_zero_blames_the_state_off_the_reference_at_its_own_index() -> None:
    zero = EnergyZeroConvention.lowest_state
    states = [
        _state(0, "low", 0.0, [("a", 1, _sp(-10.0))], zero=zero),
        _state(1, "ok", (-9.0 - -10.0) * H, [("b", 1, _sp(-9.0))], zero=zero),
        _state(2, "off", (-8.0 - -10.0) * H + 1.0, [("c", 1, _sp(-8.0))], zero=zero),
    ]
    with pytest.raises(CodedValueError) as raised:
        compare_state_energy_sums(states)
    assert raised.value.context["field"] == "solve.state_energies[2].energy_kj_mol"
    assert raised.value.context["compared_with_state_key"] == "low"


def test_a_shared_zero_with_a_non_summable_state_still_compares_the_others() -> None:
    zero = EnergyZeroConvention.lowest_state
    states = [
        _state(0, "low", 0.0, [("a", 1, _sp(-10.0))], zero=zero),
        _state(1, "ok", (-9.0 - -10.0) * H, [("b", 1, _sp(-9.0))], zero=zero),
        _state(2, "unsourced", 5.0, [("c", 1, None)], zero=zero),
    ]
    results = compare_state_energy_sums(states)
    assert [r.status for r in results] == ["agrees", "agrees", "not_compared"]
    assert results[2].reason == "no_source_stated"


def test_the_warnings_name_partial_sources_once_and_skip_unsourced_states() -> None:
    states = [
        _state(0, "partial", 1.0, [("a", 1, _sp(-10.0)), ("b", 1, None)]),
        _state(1, "unsourced", 1.0, [("c", 1, None)]),
        _state(2, "convention", 1.0, [("d", 1, _sp(-1.0))], correction=EnergyCorrectionConvention.other),
    ]
    results = compare_state_energy_sums(states)
    codes = [(w.code, w.field) for w in collect_state_energy_source_warnings(states, results)]
    assert codes == [
        ("network_state_energy_sources_partial", "solve.state_energies[0]"),
        ("network_state_energy_sum_not_compared", "solve.state_energies"),
    ]
