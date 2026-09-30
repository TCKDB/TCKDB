"""``Kinetics.modified_arrhenius(T0=...)`` sends the fit's own reference temperature (#620)."""

from __future__ import annotations

import math

import pytest

from tckdb_client.builders import Kinetics
from tckdb_client.builders.validation import TCKDBBuilderValidationError


def _payload(kinetics: Kinetics) -> dict:
    return kinetics.to_payload(
        reactant_keys=["a", "b"], product_keys=["c"], calc_key_lookup=lambda calc: "unused"
    )


def _kinetics(**kwargs) -> Kinetics:
    return Kinetics.modified_arrhenius(A=1.0e10, A_units="cm3/mol/s", n=2.0, Ea=10.0, **kwargs)


def test_t0_is_sent_as_t0_k_when_given():
    assert _payload(_kinetics(T0=298.15))["t0_k"] == 298.15


def test_a_payload_without_t0_is_byte_identical_to_the_one_before_the_field_existed():
    """No ``t0_k`` key at all, not ``t0_k: 1.0``: nothing sent before changes."""
    assert "t0_k" not in _payload(_kinetics())
    assert "t0_k" not in _payload(_kinetics(T0=1.0))


def test_the_a_is_not_rescaled_by_the_builder():
    """``A`` is sent as given: T0 says what it was fitted at, the builder does
    not fold ``A / T0**n`` into it."""
    assert _payload(_kinetics(T0=298.15))["a"] == 1.0e10


@pytest.mark.parametrize("bad", [0, 0.0, -1.0, math.inf, math.nan, "298", True])
def test_a_bad_t0_is_refused_before_anything_is_sent(bad):
    _kinetics(T0=298.15)
    with pytest.raises(TCKDBBuilderValidationError, match="Kinetics.T0"):
        _kinetics(T0=bad)
