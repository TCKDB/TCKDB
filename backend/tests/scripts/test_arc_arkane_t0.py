"""Arkane's ``T0`` reaches the bundle as ``t0_k`` instead of being dropped (#620)."""

import pytest

from scripts.arc_ingestion.arkane_parser import parse_arkane_kinetics
from scripts.arc_ingestion.builder import _build_kinetics_payload

_TEMPLATE = """
kinetics(
    label = 'A + B <=> TS',
    kinetics = Arrhenius(
        A = (1.0e10, 'cm^3/(mol*s)'),
        n = 2.0,
        Ea = (10.0, 'kJ/mol'),
        T0 = {t0},
        Tmin = (300, 'K'),
        Tmax = (2000, 'K'),
    ),
)
"""


def _payload(t0: str) -> dict:
    kinetics = parse_arkane_kinetics(_TEMPLATE.format(t0=t0))
    return _build_kinetics_payload(kinetics, ["a", "b"], ["c"])


def test_a_fitted_t0_is_forwarded_and_a_is_not_rescaled():
    parsed = parse_arkane_kinetics(_TEMPLATE.format(t0="(298.15, 'K')"))
    assert parsed.t0_k == 298.15
    payload = _payload("(298.15, 'K')")
    assert payload["t0_k"] == 298.15
    assert payload["a"] == 1.0e10


def test_t0_of_one_kelvin_or_absent_sends_no_t0_k():
    assert "t0_k" not in _payload("(1, 'K')")
    kinetics = parse_arkane_kinetics(_TEMPLATE.replace("        T0 = {t0},\n", ""))
    assert kinetics.t0_k == 1.0


def test_a_t0_in_other_units_is_refused():
    with pytest.raises(ValueError, match="T0 must be in K"):
        parse_arkane_kinetics(_TEMPLATE.format(t0="(25, 'C')"))
