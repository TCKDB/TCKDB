"""Parse ARC species YAML files (Arkane output format).

Extracts species identity (SMILES, charge, multiplicity) and
thermochemistry (NASA polynomials, H298, S298, tabulated Cp).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---- Unit conversion constants ----
KCAL_TO_KJ = 4.184
CAL_TO_J = 4.184


@dataclass
class NASAPolynomial:
    """7-coefficient NASA polynomial for one temperature range."""

    tmin_k: float
    tmax_k: float
    coeffs: list[float]  # [a1, a2, a3, a4, a5, a6, a7]


@dataclass
class ThermoPoint:
    """Tabulated thermodynamic data at one temperature."""

    temperature_k: float
    cp_j_mol_k: float


@dataclass
class SpeciesThermo:
    """Thermochemistry data extracted from an ARC species YAML."""

    h298_kj_mol: float
    s298_j_mol_k: float
    tmin_k: float
    tmax_k: float
    low_poly: NASAPolynomial  # polynomial1
    high_poly: NASAPolynomial  # polynomial2
    points: list[ThermoPoint] = field(default_factory=list)


@dataclass
class SpeciesData:
    """Parsed species data from an ARC YAML file."""

    label: str
    smiles: str
    inchi: str
    inchi_key: str
    charge: int
    multiplicity: int
    formula: str
    thermo: SpeciesThermo | None


def _get_scalar_value(node: dict) -> float:
    """Extract value from an Arkane ScalarQuantity node."""
    return float(node["value"])


def _get_array_values(node: dict) -> list[float]:
    """Extract values from an Arkane ArrayQuantity or bare np_array node.

    Handles two formats:
    - ArrayQuantity: ``{class: ArrayQuantity, value: {class: np_array, object: [...]}}``
    - Bare np_array: ``{class: np_array, object: [...]}``
    """
    # Bare np_array (e.g. NASA coeffs)
    if node.get("class") == "np_array" and "object" in node:
        return [float(v) for v in node["object"]]

    # ArrayQuantity wrapper
    inner = node.get("value")
    if inner is None:
        raise ValueError(f"Cannot parse array quantity (no 'value' or 'object' key): {list(node.keys())}")
    if isinstance(inner, dict) and inner.get("class") == "np_array":
        return [float(v) for v in inner["object"]]
    if isinstance(inner, list):
        return [float(v) for v in inner]
    raise ValueError(f"Cannot parse array quantity: {inner}")


def _parse_nasa_polynomial(node: dict) -> NASAPolynomial:
    tmin = _get_scalar_value(node["Tmin"])
    tmax = _get_scalar_value(node["Tmax"])
    coeffs = _get_array_values(node["coeffs"])
    if len(coeffs) != 7:
        raise ValueError(f"Expected 7 NASA coefficients, got {len(coeffs)}")
    return NASAPolynomial(tmin_k=tmin, tmax_k=tmax, coeffs=coeffs)


def _parse_thermo(data: dict) -> SpeciesThermo | None:
    """Parse thermo and thermo_data sections from the YAML."""
    thermo_node = data.get("thermo")
    thermo_data_node = data.get("thermo_data")

    if thermo_node is None:
        return None

    # NASA polynomials
    polys = thermo_node.get("polynomials", {})
    low_poly = _parse_nasa_polynomial(polys["polynomial1"])
    high_poly = _parse_nasa_polynomial(polys["polynomial2"])

    tmin_k = _get_scalar_value(thermo_node["Tmin"])
    tmax_k = _get_scalar_value(thermo_node["Tmax"])

    # Thermo data (H298, S298, Cp)
    h298_kj_mol = 0.0
    s298_j_mol_k = 0.0
    points: list[ThermoPoint] = []

    if thermo_data_node:
        # H298 is in kcal/mol → convert to kJ/mol
        h298_raw = _get_scalar_value(thermo_data_node["H298"])
        h298_units = thermo_data_node["H298"].get("units", "kcal/mol")
        if "kcal" in h298_units:
            h298_kj_mol = h298_raw * KCAL_TO_KJ
        elif "kJ" in h298_units:
            h298_kj_mol = h298_raw
        else:
            h298_kj_mol = h298_raw  # assume kJ/mol

        # S298 is in cal/(mol*K) → convert to J/(mol*K)
        s298_raw = _get_scalar_value(thermo_data_node["S298"])
        s298_units = thermo_data_node["S298"].get("units", "cal/(mol*K)")
        if "cal" in s298_units and "kcal" not in s298_units:
            s298_j_mol_k = s298_raw * CAL_TO_J
        elif "J" in s298_units:
            s298_j_mol_k = s298_raw
        else:
            s298_j_mol_k = s298_raw

        # Tabulated Cp data
        if "Cpdata" in thermo_data_node and "Tdata" in thermo_data_node:
            cp_values = _get_array_values(thermo_data_node["Cpdata"])
            t_values = _get_array_values(thermo_data_node["Tdata"])
            cp_units = thermo_data_node["Cpdata"].get("units", "cal/(mol*K)")

            for t, cp in zip(t_values, cp_values):
                if "cal" in cp_units and "kcal" not in cp_units:
                    cp_j = cp * CAL_TO_J
                else:
                    cp_j = cp
                points.append(ThermoPoint(temperature_k=t, cp_j_mol_k=cp_j))

    return SpeciesThermo(
        h298_kj_mol=h298_kj_mol,
        s298_j_mol_k=s298_j_mol_k,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        low_poly=low_poly,
        high_poly=high_poly,
        points=points,
    )


def parse_species_yaml(path: str | Path) -> SpeciesData:
    """Parse an ARC species YAML file."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    return SpeciesData(
        label=data["label"],
        smiles=data["smiles"],
        inchi=data.get("inchi", ""),
        inchi_key=data.get("inchi_key", ""),
        charge=int(data.get("charge", 0)),
        multiplicity=int(data["multiplicity"]),
        formula=data.get("formula", ""),
        thermo=_parse_thermo(data),
    )
