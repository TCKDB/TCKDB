"""Builder: CCCBDB thermo record → ``ThermoUploadRequest`` dict.

Field mapping from Phase 1 ``property_kind`` tokens:

* ``hf_298``            → ``thermo.h298_kj_mol``
* ``hf_298 uncertainty`` → ``thermo.h298_uncertainty_kj_mol``
* ``s_298``             → ``thermo.s298_j_mol_k``
* ``s_298 uncertainty`` → ``thermo.s298_uncertainty_j_mol_k``
* ``cp_298``            → ``thermo.points[T=298.15].cp_j_mol_k``
* ``hf_0``              → no first-class field; preserved as warning +
  external_source.unparsed.
* ``h_298_minus_h_0``   → ``thermo.points[T=298.15].h_kj_mol``

Per-value references (``Gurvich``, ``TRC``, ``Pedley``, …) have no
first-class home on ``ThermoUploadRequest`` or ``ThermoPointCreate``,
so the builder hands them back through ``external_source.per_value_references``
keyed by the Phase 1 ``property_kind`` token.
"""

from __future__ import annotations

from typing import Any

from app.importers.cccbdb.builders.common import value_ref_to_dict
from app.importers.cccbdb.models import (
    CCCBDBExperimentalSpeciesRecord,
    CCCBDBThermoValue,
)


def build_thermo_payload(
    record: CCCBDBExperimentalSpeciesRecord,
    species_entry_payload: dict[str, Any] | None,
    warnings: list[str],
    per_value_refs: dict[str, dict[str, Any]],
    unparsed: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a ``ThermoUploadRequest``-compatible dict.

    Returns ``None`` if Phase 1 produced no thermo values at all.
    """

    if not record.thermo.values:
        return None

    by_kind: dict[str, CCCBDBThermoValue] = {}
    for value in record.thermo.values:
        by_kind[value.property_kind] = value
        ref_dict = value_ref_to_dict(value.reference)
        if ref_dict is not None:
            per_value_refs[value.property_kind] = ref_dict

    payload: dict[str, Any] = {
        "scientific_origin": "experimental",
    }
    if species_entry_payload is not None:
        payload["species_entry"] = species_entry_payload

    hf_298 = by_kind.get("hf_298")
    if hf_298 is not None:
        payload["h298_kj_mol"] = hf_298.value
        if hf_298.uncertainty is not None:
            payload["h298_uncertainty_kj_mol"] = hf_298.uncertainty

    s_298 = by_kind.get("s_298")
    if s_298 is not None:
        payload["s298_j_mol_k"] = s_298.value
        if s_298.uncertainty is not None:
            payload["s298_uncertainty_j_mol_k"] = s_298.uncertainty

    points = _build_points(by_kind)
    if points:
        payload["points"] = points

    if "hf_0" in by_kind:
        hf_0 = by_kind["hf_0"]
        unparsed["hf_0"] = {
            "value": hf_0.value,
            "canonical_units": hf_0.canonical_units,
            "uncertainty": hf_0.uncertainty,
            "raw_value": hf_0.raw_value,
            "raw_units": hf_0.raw_units,
        }
        warnings.append(
            "thermo: hf_0 has no first-class TCKDB field; preserved "
            "in external_source.unparsed"
        )

    return payload


def _build_points(
    by_kind: dict[str, CCCBDBThermoValue],
) -> list[dict[str, Any]]:
    """Collapse ``cp_298`` and ``h_298_minus_h_0`` into one 298.15 K point.

    ``ThermoPointCreate`` enforces uniqueness on ``temperature_k`` via
    a validator on ``ThermoCreate``, so we must merge any 298.15 K
    quantities into a single dict.
    """

    point_298: dict[str, Any] = {}
    cp = by_kind.get("cp_298")
    if cp is not None:
        point_298["cp_j_mol_k"] = cp.value
    h_diff = by_kind.get("h_298_minus_h_0")
    if h_diff is not None:
        point_298["h_kj_mol"] = h_diff.value

    points: list[dict[str, Any]] = []
    if point_298:
        point_298["temperature_k"] = 298.15
        points.append(point_298)
    return points
