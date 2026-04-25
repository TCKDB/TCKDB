"""Parse kinetics from Arkane output.py files.

Extracts the final ``kinetics(...)`` block which contains the fitted
modified Arrhenius parameters (with tunneling included).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ArkaneKinetics:
    """Parsed modified Arrhenius kinetics from an Arkane output."""

    label: str
    a: float
    a_units: str  # e.g. "cm^3/(mol*s)", "s^-1"
    n: float
    ea: float
    ea_units: str  # e.g. "kJ/mol"
    tmin_k: float
    tmax_k: float
    a_uncertainty: float | None = None    # multiplicative uncertainty: A *|/ a_uncertainty
    n_uncertainty: float | None = None    # additive uncertainty: n +/- n_uncertainty
    ea_uncertainty: float | None = None   # additive uncertainty: Ea +/- ea_uncertainty (same units as ea_units)
    comment: str | None = None


# Unit string → TCKDB ArrheniusAUnits token
_A_UNITS_MAP: dict[str, str] = {
    "s^-1": "per_s",
    "cm^3/(mol*s)": "cm3_mol_s",
    "cm^3/(molecule*s)": "cm3_molecule_s",
    "m^3/(mol*s)": "m3_mol_s",
    "cm^6/(mol^2*s)": "cm6_mol2_s",
}

# Unit string → TCKDB ActivationEnergyUnits token
_EA_UNITS_MAP: dict[str, str] = {
    "J/mol": "j_mol",
    "kJ/mol": "kj_mol",
    "cal/mol": "cal_mol",
    "kcal/mol": "kcal_mol",
}


def parse_arkane_kinetics(text: str) -> ArkaneKinetics:
    """Parse the ``kinetics(...)`` block from Arkane output.py text.

    The block looks like::

        kinetics(
            label = '...',
            kinetics = Arrhenius(
                A = (1.18095e-06, 'cm^3/(mol*s)'),
                n = 4.78345,
                Ea = (16.6022, 'kJ/mol'),
                T0 = (1, 'K'),
                Tmin = (300, 'K'),
                Tmax = (3000, 'K'),
                comment = '...',
            ),
        )
    """
    # Find the kinetics(...) block — take the last one in the file
    # Use a simple approach: find "kinetics(" at the start of a line
    blocks = list(re.finditer(r"^kinetics\(", text, re.MULTILINE))
    if not blocks:
        raise ValueError("No kinetics() block found in Arkane output.")

    block_start = blocks[-1].start()
    block_text = text[block_start:]

    # Extract label
    label_m = re.search(r"label\s*=\s*['\"](.+?)['\"]", block_text)
    label = label_m.group(1) if label_m else ""

    # Helper: match a parameter tuple that may span multiple lines, e.g.
    #   A = (500290, 'cm^3/(mol*s)')        — single-line
    #   Ea = (                               — multi-line
    #       -2.08113,
    #       'kJ/mol',
    #   )
    def _parse_tuple_param(name: str) -> tuple[float, str] | None:
        pattern = rf"{name}\s*=\s*\(\s*([-\d.eE+]+)\s*,\s*['\"](.+?)['\"]\s*,?\s*\)"
        m = re.search(pattern, block_text, re.DOTALL)
        if m:
            return float(m.group(1)), m.group(2)
        return None

    # Extract A = (value, 'units')
    a_parsed = _parse_tuple_param("A")
    if not a_parsed:
        raise ValueError("Could not parse A parameter from kinetics block.")
    a_val, a_units = a_parsed

    # Extract n = value
    n_m = re.search(r"\bn\s*=\s*([-\d.eE+]+)", block_text)
    if not n_m:
        raise ValueError("Could not parse n parameter from kinetics block.")
    n_val = float(n_m.group(1))

    # Extract Ea = (value, 'units')
    ea_parsed = _parse_tuple_param("Ea")
    if not ea_parsed:
        raise ValueError("Could not parse Ea parameter from kinetics block.")
    ea_val, ea_units = ea_parsed

    # Extract Tmin / Tmax
    tmin_parsed = _parse_tuple_param("Tmin")
    tmin_k = tmin_parsed[0] if tmin_parsed else 300.0

    tmax_parsed = _parse_tuple_param("Tmax")
    tmax_k = tmax_parsed[0] if tmax_parsed else 3000.0

    # Extract comment
    comment_m = re.search(r"comment\s*=\s*['\"](.+?)['\"]", block_text, re.DOTALL)
    comment = comment_m.group(1).strip() if comment_m else None

    # Extract uncertainties from comment string
    # Format: "dA = *|/ 1.13881, dn = +|- 0.016931, dEa = +|- 0.0968239 kJ/mol"
    a_unc = n_unc = ea_unc = None
    if comment:
        da_m = re.search(r"dA\s*=\s*\*\|/\s*([\d.eE+-]+)", comment)
        dn_m = re.search(r"dn\s*=\s*\+\|-\s*([\d.eE+-]+)", comment)
        dea_m = re.search(r"dEa\s*=\s*\+\|-\s*([\d.eE+-]+)", comment)
        if da_m:
            a_unc = float(da_m.group(1))
        if dn_m:
            n_unc = float(dn_m.group(1))
        if dea_m:
            ea_unc = float(dea_m.group(1))

    return ArkaneKinetics(
        label=label,
        a=a_val,
        a_units=a_units,
        n=n_val,
        ea=ea_val,
        ea_units=ea_units,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        a_uncertainty=a_unc,
        n_uncertainty=n_unc,
        ea_uncertainty=ea_unc,
        comment=comment,
    )


def parse_arkane_kinetics_from_file(path: str | Path) -> ArkaneKinetics:
    text = Path(path).read_text()
    return parse_arkane_kinetics(text)


def map_a_units(raw: str) -> str:
    """Map Arkane A-units string to TCKDB ArrheniusAUnits enum value."""
    token = _A_UNITS_MAP.get(raw)
    if token is None:
        raise ValueError(f"Unknown A-units: {raw!r}. Known: {list(_A_UNITS_MAP)}")
    return token


def map_ea_units(raw: str) -> str:
    """Map Arkane Ea-units string to TCKDB ActivationEnergyUnits enum value."""
    token = _EA_UNITS_MAP.get(raw)
    if token is None:
        raise ValueError(f"Unknown Ea-units: {raw!r}. Known: {list(_EA_UNITS_MAP)}")
    return token
