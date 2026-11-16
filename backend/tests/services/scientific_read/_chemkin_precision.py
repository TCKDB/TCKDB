"""Helpers for checking CHEMKIN rate lines against the stored fit to the digits written."""

from __future__ import annotations

import math

#: cal mol-1 K-1, the gas constant in the units of the REACTIONS CAL/MOLE line.
R_CAL_MOL_K = 1.987204258
#: Relative difference between the gas constants of the stored-fit evaluation (kJ) and Cantera, with margin.
GAS_CONSTANT_SLACK = 1.0e-10


def half_unit(text: str) -> float:
    """Half a unit in the last written place of ``text`` (``1.45978000E+03``)."""
    mantissa, _, exponent = text.lower().partition("e")
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    return 0.5 * 10.0 ** (int(exponent or 0) - decimals)


def written_rate_bound(a_text: str, n_text: str, ea_text: str, temperature_k: float) -> float:
    """Largest |ln(k_file / k_stored)| the written digits allow: the half unit of A over A, the half unit of n times
    ln T, and the half unit of Ea (cal/mol) over RT."""
    return (
        half_unit(a_text) / abs(float(a_text))
        + half_unit(n_text) * abs(math.log(temperature_k))
        + half_unit(ea_text) / (R_CAL_MOL_K * temperature_k)
        + GAS_CONSTANT_SLACK
    )
