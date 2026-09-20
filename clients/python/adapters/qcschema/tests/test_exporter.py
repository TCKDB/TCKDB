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
    E_EXPORT_GEOMETRY_MISMATCH,
    E_EXPORT_GEOMETRY_UNAVAILABLE,
    E_EXPORT_HESSIAN_UNAUTHORIZED,
    E_EXPORT_HESSIAN_UNAVAILABLE,
    E_EXPORT_IDENTITY_UNAVAILABLE,
    E_EXPORT_ISOTOPES_UNAVAILABLE,
    QCSchemaAdapterError,
)
from tckdb_qcschema.exporter import export_calculation

READS = Path(__file__).parent / "fixtures" / "reads"


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


def _load(case: str) -> tuple[dict, dict, dict, dict]:
    case_dir = READS / case
    calculation = json.loads((case_dir / "calculation.json").read_text())
    geometries = json.loads((case_dir / "geometries.json").read_text())
    hessian = json.loads((case_dir / "hessian.json").read_text())
    legacy_geometry = json.loads((case_dir / "legacy_geometry.json").read_text())
    return calculation, geometries, hessian, legacy_geometry


class _Client:
    def __init__(
        self,
        calculation,
        geometries,
        hessian,
        *,
        search_records=None,
        legacy_geometry=None,
        legacy_geometry_error=None,
    ):
        self.calculation = calculation
        self.geometries = geometries
        self.hessian_response = hessian
        self.search_records = search_records or []
        self.search_calls: list[dict] = []
        self.legacy_geometry = legacy_geometry
        self.legacy_geometry_error = legacy_geometry_error
        self.get_json_calls: list[str] = []

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

    def get_json(self, path):
        self.get_json_calls.append(path)
        if self.legacy_geometry_error is not None:
            raise self.legacy_geometry_error
        assert self.legacy_geometry is not None, (
            f"unexpected get_json({path!r}): this test's _Client was not "
            "given a legacy_geometry fixture"
        )
        assert path == self.legacy_geometry["request_path"], (
            f"get_json called with {path!r}, expected "
            f"{self.legacy_geometry['request_path']!r}"
        )
        return self.legacy_geometry["response"]

    def status(self):
        return {}


def test_freq_export_uses_same_level_sp_energy_when_present():
    """Opportunistic enrichment: a same-conformer, same-LOT sp sibling
    supplies properties.return_energy on a freq export.
    """
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    sibling_energy = -74.5
    sibling_record = copy.deepcopy(calculation["record"])
    sibling_record["calculation"]["type"] = "sp"
    sibling_record["results"] = {
        "kind": "sp",
        "sp": {"electronic_energy_hartree": sibling_energy},
    }
    client = _Client(
        calculation,
        geometries,
        hessian,
        search_records=[sibling_record],
        legacy_geometry=legacy_geometry,
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
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    sibling_record = copy.deepcopy(calculation["record"])
    sibling_record["calculation"]["type"] = "sp"
    sibling_record["results"] = {
        "kind": "sp",
        "sp": {"electronic_energy_hartree": None},
    }
    client = _Client(
        calculation,
        geometries,
        hessian,
        search_records=[sibling_record],
        legacy_geometry=legacy_geometry,
    )

    exported = export_calculation(client, 2)

    assert "return_energy" not in (exported.get("properties") or {})


def test_export_refuses_calculation_id_unavailable_for_ref_only_handle():
    """No calculation_id in the scientific read, and the caller's handle
    is a public ref rather than an integer: refused, not guessed.
    """
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    assert "calculation_id" not in calculation["record"]["calculation"]
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, "calc_bfz6d34evyggcvj4sen7iycu5a")
    assert excinfo.value.code == E_EXPORT_CALCULATION_ID_UNAVAILABLE


def test_export_accepts_a_digit_string_handle_for_freq():
    """A digit-string handle (e.g. from a CLI argument) is treated as an
    integer id, same as a real ``int``.
    """
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    exported = export_calculation(client, "2")
    assert exported["input_data"]["specification"]["driver"] == "hessian"


def test_export_refuses_hessian_unavailable_on_404():
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    hessian = {"status_code": 404, "body": {"code": "hessian_not_found"}}
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 2)
    assert excinfo.value.code == E_EXPORT_HESSIAN_UNAVAILABLE


def test_export_refuses_hessian_unauthorized_on_401():
    """A raw 401 from the Hessian read is narrowed into a coded refusal,
    distinct from the 404 ("no stored Hessian") case above -- the caller
    needs to know "configure an API key", not "this calculation has no
    Hessian".
    """
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    hessian = {"status_code": 401, "body": {"code": "unauthorized"}}
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 2)
    assert excinfo.value.code == E_EXPORT_HESSIAN_UNAUTHORIZED


def test_export_refuses_energy_unavailable_for_sp_with_no_result():
    calculation, geometries, hessian, _legacy_geometry = _load("energy_v1")
    calculation = copy.deepcopy(calculation)
    calculation["record"]["results"] = None
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_ENERGY_UNAVAILABLE


def test_export_refuses_geometry_unavailable_for_sp_with_no_linked_geometry():
    calculation, geometries, hessian, _legacy_geometry = _load("energy_v1")
    calculation = copy.deepcopy(calculation)
    calculation["record"]["input_geometries"] = []
    calculation["record"]["output_geometries"] = []
    client = _Client(calculation, geometries, hessian)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_GEOMETRY_UNAVAILABLE


def test_export_refuses_identity_unavailable_when_geometry_owner_is_ambiguous():
    calculation, geometries, hessian, legacy_geometry = _load("energy_v1")
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
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_IDENTITY_UNAVAILABLE


# ---------------------------------------------------------------------
# Isotopes (C-Q3 review round 2)
# ---------------------------------------------------------------------


def test_sp_export_uses_recorded_nonstandard_isotopes():
    """D2O-style geometry (O, D(2), H): a recorded ``isotope_mass_number``
    is used verbatim, a ``null`` one falls back to the standard nuclide for
    *that* atom only. This is the test that must go RED against the old
    ``to_A``-for-every-atom line -- see the mutation table in the PR body.
    """
    calculation, geometries, hessian, legacy_geometry = _load("energy_v1")
    legacy_geometry = copy.deepcopy(legacy_geometry)
    atoms = legacy_geometry["response"]["items"][0]["atoms"]
    deuterium = next(a for a in atoms if a["atom_index"] == 2)
    assert deuterium["element"].strip() == "H"
    deuterium["isotope_mass_number"] = 2
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    exported = export_calculation(client, 1)

    assert exported["molecule"]["mass_numbers"] == [16, 2, 1]
    # qcelemental derives masses itself from symbols + mass_numbers -- this
    # adapter never invents one.
    masses = exported["molecule"]["masses"]
    assert masses[1] == pytest.approx(2.01410178, abs=1e-6)


def test_freq_export_uses_recorded_nonstandard_isotopes():
    """Same D2O check on the freq (``GET /geometries/{id}``) path."""
    calculation, geometries, hessian, legacy_geometry = _load("hessian_v1")
    legacy_geometry = copy.deepcopy(legacy_geometry)
    atoms = legacy_geometry["response"]["atoms"]
    deuterium = next(a for a in atoms if a["atom_index"] == 2)
    assert deuterium["element"].strip() == "H"
    deuterium["isotope_mass_number"] = 2
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    exported = export_calculation(client, 2)

    assert exported["molecule"]["mass_numbers"] == [16, 2, 1]


def test_export_refuses_isotopes_unavailable_on_401():
    """A hosted deployment with no API key: the legacy geometry read is
    rejected (surfaced here as any exception from ``get_json``, matching
    how a real ``TCKDBClient.get_json`` call with no api_key configured
    raises client-side before any request is even sent).
    """
    calculation, geometries, hessian, _legacy_geometry = _load("energy_v1")
    client = _Client(
        calculation,
        geometries,
        hessian,
        legacy_geometry_error=_FakeHTTPError(401),
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_ISOTOPES_UNAVAILABLE
    assert client.get_json_calls, "get_json was never called"


def test_export_refuses_isotopes_unavailable_on_empty_list():
    """A ``geom_hash`` query that comes back with no rows (200, empty
    ``items``) is refused the same as a hard failure -- never guessed.
    """
    calculation, geometries, hessian, legacy_geometry = _load("energy_v1")
    legacy_geometry = copy.deepcopy(legacy_geometry)
    legacy_geometry["response"] = {"items": [], "total": 0, "skip": 0, "limit": 50}
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_ISOTOPES_UNAVAILABLE


def test_export_refuses_geometry_mismatch_on_element_disagreement():
    """The legacy isotope read and the scientific geometry read naming
    different elements at the same ``atom_index`` is refused rather than
    exported with mismatched coordinates and isotopes.
    """
    calculation, geometries, hessian, legacy_geometry = _load("energy_v1")
    legacy_geometry = copy.deepcopy(legacy_geometry)
    legacy_geometry["response"]["items"][0]["atoms"][1]["element"] = "N "
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_GEOMETRY_MISMATCH


def test_export_refuses_geometry_mismatch_on_atom_count_disagreement():
    calculation, geometries, hessian, legacy_geometry = _load("energy_v1")
    legacy_geometry = copy.deepcopy(legacy_geometry)
    del legacy_geometry["response"]["items"][0]["atoms"][-1]
    client = _Client(
        calculation, geometries, hessian, legacy_geometry=legacy_geometry
    )

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_calculation(client, 1)
    assert excinfo.value.code == E_EXPORT_GEOMETRY_MISMATCH
