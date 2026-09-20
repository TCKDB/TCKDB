"""Unit tests for exporter.py branches the round-trip corpus does not reach (C-Q3).

``tests/test_round_trip.py`` covers the two pinned-read cases end to end
(``energy_v1``, ``hessian_v1``, and their v2 siblings). Neither pinned case
has a linked same-level ``sp`` calculation, an ambiguous geometry identity,
a missing energy, or a ref-only handle on a deployment that hides
``calculation_id`` -- so those branches are exercised here directly against
hand-built stub responses, reusing the same pinned ``calculation.json``/
``geometries.json``/``hessian.json`` shapes as a starting point.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tckdb_qcschema.errors import (
    E_EXPORT_CALCULATION_ID_UNAVAILABLE,
    E_EXPORT_ENERGY_UNAVAILABLE,
    E_EXPORT_GEOMETRY_UNAVAILABLE,
    E_EXPORT_HESSIAN_UNAVAILABLE,
    E_EXPORT_IDENTITY_UNAVAILABLE,
    QCSchemaAdapterError,
)
from tckdb_qcschema.exporter import export_calculation

READS = Path(__file__).parent / "fixtures" / "reads"


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


def _load(case: str) -> tuple[dict, dict, dict]:
    case_dir = READS / case
    calculation = json.loads((case_dir / "calculation.json").read_text())
    geometries = json.loads((case_dir / "geometries.json").read_text())
    hessian = json.loads((case_dir / "hessian.json").read_text())
    return calculation, geometries, hessian


class _Client:
    def __init__(self, calculation, geometries, hessian, *, search_records=None):
        self.calculation = calculation
        self.geometries = geometries
        self.hessian_response = hessian
        self.search_records = search_records or []
        self.search_calls: list[dict] = []

    def get_calculation(self, calculation_ref_or_id, *, include=None, profile=None):
        del calculation_ref_or_id, include, profile
        return self.calculation

    def get_geometry(self, geometry_handle, *, include=None, profile=None):
        del include, profile
        return self.geometries[str(geometry_handle)]

    def get_calculation_hessian(self, calculation_id):
        del calculation_id
        if self.hessian_response["status_code"] != 200:
            raise _FakeHTTPError(self.hessian_response["status_code"])
        return self.hessian_response["body"]

    def search_calculations(self, **filters):
        self.search_calls.append(filters)
        return {"records": self.search_records}

    def status(self):
        return {}


def test_freq_export_uses_same_level_sp_energy_when_present():
    """Opportunistic enrichment: a same-conformer, same-LOT sp sibling
    supplies properties.return_energy on a freq export.
    """
    calculation, geometries, hessian = _load("hessian_v1")
    sibling_energy = -74.5
    sibling_record = copy.deepcopy(calculation["record"])
    sibling_record["calculation"]["type"] = "sp"
    sibling_record["results"] = {
        "kind": "sp",
        "sp": {"electronic_energy_hartree": sibling_energy},
    }
    client = _Client(
        calculation, geometries, hessian, search_records=[sibling_record]
    )

    exported = export_calculation(client, 2)

    assert exported["properties"]["return_energy"] == sibling_energy
    # The search was scoped to this record's own conformer + level of theory.
    assert len(client.search_calls) == 1
    call = client.search_calls[0]
    assert call["calculation_type"] == "sp"
    assert call["conformer_observation_ref"] == calculation["record"]["conformer"][
        "conformer_observation_ref"
    ]
    assert call["lot_ref"] == calculation["record"]["level_of_theory"][
        "level_of_theory_ref"
    ]


def test_freq_export_ignores_a_sibling_with_no_recorded_energy():
    calculation, geometries, hessian = _load("hessian_v1")
    sibling_record = copy.deepcopy(calculation["record"])
    sibling_record["calculation"]["type"] = "sp"
    sibling_record["results"] = {
        "kind": "sp",
        "sp": {"electronic_energy_hartree": None},
    }
    client = _Client(
        calculation, geometries, hessian, search_records=[sibling_record]
    )

    exported = export_calculation(client, 2)

    assert "return_energy" not in (exported.get("properties") or {})


def test_export_refuses_calculation_id_unavailable_for_ref_only_handle():
    """No calculation_id in the scientific read, and the caller's handle
    is a public ref rather than an integer: refused, not guessed.
    """
    calculation, geometries, hessian = _load("hessian_v1")
    assert "calculation_id" not in calculation["record"]["calculation"]
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, "calc_bfz6d34evyggcvj4sen7iycu5a")
    assert excinfo.value.code == E_EXPORT_CALCULATION_ID_UNAVAILABLE


def test_export_accepts_a_digit_string_handle_for_freq():
    """A digit-string handle (e.g. from a CLI argument) is treated as an
    integer id, same as a real ``int``.
    """
    calculation, geometries, hessian = _load("hessian_v1")
    client = _Client(calculation, geometries, hessian)

    exported = export_calculation(client, "2")
    assert exported["input_data"]["specification"]["driver"] == "hessian"


def test_export_refuses_hessian_unavailable_on_404():
    calculation, geometries, hessian = _load("hessian_v1")
    hessian = {"status_code": 404, "body": {"code": "hessian_not_found"}}
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 2)
    assert excinfo.value.code == E_EXPORT_HESSIAN_UNAVAILABLE


def test_export_refuses_energy_unavailable_for_sp_with_no_result():
    calculation, geometries, hessian = _load("energy_v1")
    calculation = copy.deepcopy(calculation)
    calculation["record"]["results"] = None
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_ENERGY_UNAVAILABLE


def test_export_refuses_geometry_unavailable_for_sp_with_no_linked_geometry():
    calculation, geometries, hessian = _load("energy_v1")
    calculation = copy.deepcopy(calculation)
    calculation["record"]["input_geometries"] = []
    calculation["record"]["output_geometries"] = []
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_GEOMETRY_UNAVAILABLE


def test_export_refuses_identity_unavailable_when_geometry_owner_is_ambiguous():
    calculation, geometries, hessian = _load("energy_v1")
    geometries = copy.deepcopy(geometries)
    only_geometry = next(iter(geometries.values()))
    only_geometry["identity"] = {
        "kind": None,
        "species_entry": None,
        "transition_state_entry": None,
        "ambiguous_owners": [
            {"kind": "species_entry", "ref": "spe_a"},
            {"kind": "species_entry", "ref": "spe_b"},
        ],
    }
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_IDENTITY_UNAVAILABLE
