"""Unit normalization for CCCBDB-parsed numeric values.

Scope:

* Energy / enthalpy:  ``kJ/mol``  (canonical), also accepts
  ``kcal/mol``, ``hartree``, ``eV``.
* Entropy / heat capacity:  ``J/mol/K``  (canonical), also accepts
  ``cal/mol/K``.
* Frequency:  ``cm^-1``  (canonical).
* Rotational constant:  ``GHz``  (canonical), also accepts ``MHz``.
* Length:  ``angstrom``  (canonical), also accepts ``pm`` / ``bohr``.

Unsupported units raise :class:`UnsupportedUnitError` rather than
silently passing the value through. The parser layer catches that
error and converts it into a per-value warning so a single bad row
does not poison the whole page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Dimension = Literal[
    "energy",
    "entropy_or_heat_capacity",
    "frequency",
    "rotational_constant",
    "length",
]


class UnsupportedUnitError(ValueError):
    """Raised when a raw unit string cannot be mapped to a canonical unit."""

    def __init__(self, raw_units: str, dimension: Dimension):
        super().__init__(
            f"Unsupported unit {raw_units!r} for dimension {dimension!r}"
        )
        self.raw_units = raw_units
        self.dimension = dimension


@dataclass(frozen=True)
class _UnitSpec:
    canonical: str
    factor: float


_KCAL_TO_KJ = 4.184
_HARTREE_TO_KJ_MOL = 2625.4996394798254  # CODATA-ish; documented constant.
_EV_TO_KJ_MOL = 96.48533212331

_ENERGY: dict[str, _UnitSpec] = {
    "kj/mol": _UnitSpec("kJ/mol", 1.0),
    "kj mol-1": _UnitSpec("kJ/mol", 1.0),
    "kj mol^-1": _UnitSpec("kJ/mol", 1.0),
    "kjmol-1": _UnitSpec("kJ/mol", 1.0),
    "kcal/mol": _UnitSpec("kJ/mol", _KCAL_TO_KJ),
    "kcal mol-1": _UnitSpec("kJ/mol", _KCAL_TO_KJ),
    "hartree": _UnitSpec("kJ/mol", _HARTREE_TO_KJ_MOL),
    "ha": _UnitSpec("kJ/mol", _HARTREE_TO_KJ_MOL),
    "ev": _UnitSpec("kJ/mol", _EV_TO_KJ_MOL),
}

_ENTROPY: dict[str, _UnitSpec] = {
    "j/mol/k": _UnitSpec("J/mol/K", 1.0),
    "j mol-1 k-1": _UnitSpec("J/mol/K", 1.0),
    "j mol^-1 k^-1": _UnitSpec("J/mol/K", 1.0),
    "j/(mol k)": _UnitSpec("J/mol/K", 1.0),
    "j k-1 mol-1": _UnitSpec("J/mol/K", 1.0),
    "j k^-1 mol^-1": _UnitSpec("J/mol/K", 1.0),
    "cal/mol/k": _UnitSpec("J/mol/K", _KCAL_TO_KJ),
    "cal mol-1 k-1": _UnitSpec("J/mol/K", _KCAL_TO_KJ),
}

_FREQUENCY: dict[str, _UnitSpec] = {
    "cm-1": _UnitSpec("cm^-1", 1.0),
    "cm^-1": _UnitSpec("cm^-1", 1.0),
    "1/cm": _UnitSpec("cm^-1", 1.0),
    "wavenumber": _UnitSpec("cm^-1", 1.0),
}

_ROTATIONAL: dict[str, _UnitSpec] = {
    "ghz": _UnitSpec("GHz", 1.0),
    "mhz": _UnitSpec("GHz", 1.0e-3),
}

_LENGTH: dict[str, _UnitSpec] = {
    "angstrom": _UnitSpec("angstrom", 1.0),
    "angstroms": _UnitSpec("angstrom", 1.0),
    "a": _UnitSpec("angstrom", 1.0),
    "Å": _UnitSpec("angstrom", 1.0),  # Å
    "pm": _UnitSpec("angstrom", 0.01),
    "bohr": _UnitSpec("angstrom", 0.5291772105),
}

_TABLES: dict[Dimension, dict[str, _UnitSpec]] = {
    "energy": _ENERGY,
    "entropy_or_heat_capacity": _ENTROPY,
    "frequency": _FREQUENCY,
    "rotational_constant": _ROTATIONAL,
    "length": _LENGTH,
}


def _normalize_key(raw_units: str) -> str:
    """Collapse a raw unit string to a comparable lookup key.

    Lowercase, strip whitespace, collapse runs of spaces, drop dot
    separators ("J.mol-1.K-1" -> "j mol-1 k-1").
    """

    key = raw_units.strip().lower()
    key = key.replace(".", " ")
    key = re.sub(r"\s+", " ", key)
    return key


def convert_to_canonical(
    value: float, raw_units: str, dimension: Dimension
) -> tuple[float, str]:
    """Convert ``(value, raw_units)`` into TCKDB's canonical unit for
    the given dimension.

    :param value: The numeric value to convert.
    :param raw_units: The raw unit string as parsed from the page.
    :param dimension: Which physical dimension governs the conversion.
    :returns: ``(value_canonical, canonical_unit_string)``.
    :raises UnsupportedUnitError: If ``raw_units`` is not recognized
        for the given dimension.
    """

    table = _TABLES[dimension]
    spec = table.get(_normalize_key(raw_units))
    if spec is None:
        raise UnsupportedUnitError(raw_units, dimension)
    return value * spec.factor, spec.canonical


def canonical_unit_for(dimension: Dimension) -> str:
    """Return the canonical unit string for a dimension.

    Useful for tests and for stamping the canonical unit on a record
    independently of any conversion call.
    """

    if dimension == "energy":
        return "kJ/mol"
    if dimension == "entropy_or_heat_capacity":
        return "J/mol/K"
    if dimension == "frequency":
        return "cm^-1"
    if dimension == "rotational_constant":
        return "GHz"
    if dimension == "length":
        return "angstrom"
    raise ValueError(f"Unknown dimension: {dimension!r}")
