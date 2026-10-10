"""``compare_state_energy_sums`` for ``electronic_plus_zpe`` from an sp and a scaled freq ZPE (#693).

Pure, over stand-ins that carry exactly the attributes the function reads (see
``test_network_state_energy_sums``). The numbers are written out by hand.
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
    compare_state_energy_sums,
)

_E0 = EnergyCorrectionConvention.electronic_plus_zpe


def _sp(hartree: float):
    return SimpleNamespace(type=CalculationType.sp, sp_result=SimpleNamespace(electronic_energy_hartree=hartree))


def _freq(zpe: float | None):
    return SimpleNamespace(
        type=CalculationType.freq, freq_result=None if zpe is None else SimpleNamespace(zpe_hartree=zpe)
    )


def _participant(species, nu, electronic, zpe, *, geometry=frozenset({1}), zpe_geometry=frozenset({1})):
    return ParticipantSource(species, nu, electronic, _freq(zpe), geometry, zpe_geometry, False)


def _state(kj, participants, *, scale) -> StateEnergyToCompare:
    return StateEnergyToCompare(
        index=0,
        state_key="s",
        energy_kj_mol=kj,
        energy_zero_convention=EnergyZeroConvention.absolute,
        correction_convention=_E0,
        participants=tuple(participants),
        zpe_scale_factor=scale,
    )


def test_an_sp_plus_scaled_zpe_is_summed_with_the_stated_factor_and_coefficients() -> None:
    # 2 A + B: 2 * (-10.0 + 0.9 * 0.05) + (-3.5 + 0.9 * 0.02) = -23.392
    participants = [_participant("a", 2, _sp(-10.0), 0.05), _participant("b", 1, _sp(-3.5), 0.02)]
    assert compare_state_energy_sums([_state(-23.392 * H, participants, scale=0.9)])[0].status == "agrees"


def test_the_scale_is_used_exactly_so_the_unscaled_sum_contradicts_another_scale() -> None:
    participants = [_participant("a", 1, _sp(-10.0), 0.05)]
    unscaled = (-10.0 + 0.05) * H
    with pytest.raises(CodedValueError) as raised:
        compare_state_energy_sums([_state(unscaled, participants, scale=0.9)])
    assert raised.value.code == "network_state_energy_sum_mismatch"
    assert raised.value.context["zpe_scale_factor"] == 0.9
    # The same number at the scale it was built with agrees.
    assert compare_state_energy_sums([_state(unscaled, participants, scale=1.0)])[0].status == "agrees"


def test_the_factors_own_rounding_widens_the_tolerance() -> None:
    """ZPE 0.2 Eh: a factor good to 4 decimals moves the sum by up to 1e-5 Eh, past the 1e-6 floor."""
    participants = [_participant("a", 1, _sp(-10.0), 0.2)]
    stated = (-10.0 + 0.98 * 0.2 + 8e-6) * H
    assert compare_state_energy_sums([_state(stated, participants, scale=0.98)])[0].status == "agrees"


def test_an_unstated_scale_is_never_one_even_when_the_unscaled_sum_agrees() -> None:
    participants = [_participant("a", 1, _sp(-10.0), 0.05)]
    (result,) = compare_state_energy_sums([_state((-10.0 + 0.05) * H, participants, scale=None)])
    assert (result.status, result.reason) == ("not_compared", "zpe_scaling_unstated")


@pytest.mark.parametrize(
    ("geometry", "zpe_geometry"),
    [
        (frozenset({1}), frozenset({2})),  # different geometries
        (frozenset({1, 2}), frozenset({1, 2})),  # declared more than once: not a pairing
        (None, None),  # not looked up: nothing is paired
    ],
)
def test_a_zpe_not_at_the_sps_geometry_is_not_paired_and_none_is_borrowed(geometry, zpe_geometry) -> None:
    participants = [_participant("a", 1, _sp(-10.0), 0.05, geometry=geometry, zpe_geometry=zpe_geometry)]
    (result,) = compare_state_energy_sums([_state(1.0, participants, scale=0.9)])
    assert (result.status, result.reason) == ("not_compared", "geometry_not_paired")


def test_a_freq_that_stores_no_zpe_is_not_compared() -> None:
    (result,) = compare_state_energy_sums([_state(1.0, [_participant("a", 1, _sp(-10.0), None)], scale=0.9)])
    assert (result.status, result.reason) == ("not_compared", "zpe_not_stated")


def test_an_atoms_sp_is_its_e0_and_needs_no_freq_or_scale() -> None:
    atom = ParticipantSource("h", 1, _sp(-0.5), is_atom=True)
    molecule = _participant("m", 1, _sp(-10.0), 0.05)
    mixed = _state((-0.5 + -10.0 + 0.9 * 0.05) * H, [atom, molecule], scale=0.9)
    assert compare_state_energy_sums([mixed])[0].status == "agrees"
    assert compare_state_energy_sums([_state(-0.5 * H, [atom], scale=None)])[0].status == "agrees"
    # A wrong atom energy is a contradiction, so the atom branch really compares.
    with pytest.raises(CodedValueError):
        compare_state_energy_sums([_state(-0.6 * H, [atom], scale=None)])


def test_a_molecules_sp_without_a_freq_is_not_an_e0() -> None:
    molecule = ParticipantSource("m", 1, _sp(-10.0), is_atom=False)
    (result,) = compare_state_energy_sums([_state(-10.0 * H, [molecule], scale=0.9)])
    assert (result.status, result.reason) == ("not_compared", "zpe_not_in_source")
