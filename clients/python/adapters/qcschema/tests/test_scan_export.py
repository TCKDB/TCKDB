"""Scan export and the scan round trip.

original TorsionDrive -> import mapping -> stored by a real backend ->
pinned reads -> :func:`tckdb_qcschema.scan_export.export_scan` -> compare.

No live server: :class:`ScanStubClient` serves the JSON bodies a real
backend returned for these exact deposits, captured by
``scripts/capture_scan_read_fixtures.py`` into ``tests/fixtures/reads_scan/``.
The route cases there are the backend scan corpus
(``backend/tests/fixtures/qcschema_scan/``) -- the importer's own output
for ``torsiondrive_v1``, ``torsiondrive_v2`` and ``torsiondrive_2d_v2`` --
so what is exported here is what the importer stored.

What the round trip requires (the brief's bar, not a looser one):

* the same grid points, exactly (an exact count and the same angles);
* each grid point's energy **exactly** equal (``==``) to the document's;
* each coordinate value **exactly** equal to the grid angle;
* each geometry within the adapter's existing import bound, half a unit in
  the tenth decimal place of an Angstrom (derived in ``test_round_trip.py``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import qcelemental.models.v2 as qcel_v2

from tckdb_qcschema.errors import (
    E_EXPORT_SCAN_COORDINATE_NONCONFORMING,
    E_EXPORT_SCAN_GRID_NOT_INTEGRAL,
    E_EXPORT_SCAN_NOT_RELAXED,
    E_EXPORT_SCAN_NOT_TORSION_DRIVE,
    E_EXPORT_SCAN_POINT_INCOMPLETE,
    E_TCKDB_EXPORT_REIMPORT_REFUSED,
    QCSchemaAdapterError,
)
from tckdb_qcschema.exporter import export_calculation
from tckdb_qcschema.molecule import BOHR_TO_ANGSTROM
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.scan import parse_grid_key
from tckdb_qcschema.scan_export import export_scan, grid_angle

FIXTURES = Path(__file__).parent / "fixtures"
READS = FIXTURES / "reads_scan"

#: See ``test_round_trip.py``'s "geometry bound, derived, not guessed".
GEOMETRY_BOUND_BOHR = 0.5e-10 / BOHR_TO_ANGSTROM

ROUND_TRIP_CASES = ("torsiondrive_v1", "torsiondrive_v2", "torsiondrive_2d_v2")


class ScanStubClient:
    """Serves pinned ``reads_scan/<case>/`` bodies; records every call."""

    def __init__(self, case: str, *, edit=None) -> None:
        case_dir = READS / case
        self.calculation = json.loads((case_dir / "calculation.json").read_text())
        self.scan = json.loads((case_dir / "scan.json").read_text())
        self.geometries = json.loads((case_dir / "geometries.json").read_text())
        self.legacy = json.loads((case_dir / "legacy_geometries.json").read_text())
        if edit is not None:
            edit(self)
        self.calls: list[tuple] = []

    def get_calculation(self, handle, *, include=None, profile=None):
        self.calls.append(("get_calculation", handle, tuple(include or ())))
        return copy.deepcopy(self.calculation)

    def get_calculation_scan(self, handle, *, include_geometries=None, offset=None, limit=None, **_):
        self.calls.append(("get_calculation_scan", handle, include_geometries, offset, limit))
        assert include_geometries is True, "the exporter needs geometry_link.geom_hash per point"
        page = copy.deepcopy(self.scan)
        points = page["points"]
        page["points"] = points[offset : offset + limit]
        page["pagination"] = dict(
            page["pagination"], offset=offset, limit=limit, returned=len(page["points"]), total=len(points)
        )
        return page

    def get_geometry(self, handle, *, include=None, profile=None):
        self.calls.append(("get_geometry", handle))
        return copy.deepcopy(self.geometries[handle])

    def get_json(self, path):
        self.calls.append(("get_json", path))
        return copy.deepcopy(self.legacy[path])

    def status(self):
        return {"status": "ok"}


def _original(case: str) -> dict:
    """Grid angles -> (energy, final geometry bohr) from the original document."""
    raw = (FIXTURES / case / "document.json").read_bytes()
    record = read_document(raw)
    r = record.result
    if record.family == "v1":
        n = len(r.keywords.dihedrals)
        energies = dict(r.final_energies)
        initial = r.initial_molecule
    else:
        n = len(r.input_data.specification.keywords.dihedrals)
        energies = {k: p.return_energy for k, p in r.scan_properties.items()}
        initial = r.input_data.initial_molecule
    return {
        "n": n,
        "points": {
            parse_grid_key(k, n): (energies[k], np.asarray(m.geometry, dtype=float))
            for k, m in r.final_molecules.items()
        },
        "initial": [np.asarray(m.geometry, dtype=float) for m in initial],
        "record": record,
    }


def _exported(document: dict) -> dict:
    n = len(document["input_data"]["specification"]["keywords"]["dihedrals"])
    return {
        parse_grid_key(k, n): (
            document["scan_properties"][k]["return_energy"],
            np.asarray(m["geometry"], dtype=float).reshape(-1, 3),
        )
        for k, m in document["final_molecules"].items()
    }


@pytest.mark.parametrize("case", ROUND_TRIP_CASES)
def test_scan_round_trip(case: str) -> None:
    original = _original(case)
    client = ScanStubClient(case)
    document, report = export_scan(client, "calc_x")

    qcel_v2.TorsionDriveResult.model_validate(document)
    exported = _exported(document)

    assert len(exported) == len(original["points"]), (
        f"{case}: exported {len(exported)} grid points, the document has {len(original['points'])}"
    )
    assert set(exported) == set(original["points"]), f"{case}: grid angles differ"
    for angles, (energy, geometry) in original["points"].items():
        exported_energy, exported_geometry = exported[angles]
        assert exported_energy == energy, f"{case} {angles}: energy {exported_energy!r} != {energy!r}"
        assert np.max(np.abs(exported_geometry - geometry)) <= GEOMETRY_BOUND_BOHR, (
            f"{case} {angles}: geometry outside the import bound"
        )
    initial = [
        np.asarray(m["geometry"], dtype=float).reshape(-1, 3)
        for m in document["input_data"]["initial_molecule"]
    ]
    assert len(initial) == len(original["initial"])
    for got, want in zip(initial, original["initial"], strict=True):
        assert np.max(np.abs(got - want)) <= GEOMETRY_BOUND_BOHR

    r = original["record"].result
    keywords = (
        r.keywords if original["record"].family == "v1" else r.input_data.specification.keywords
    )
    exported_keywords = document["input_data"]["specification"]["keywords"]
    assert [tuple(d) for d in exported_keywords["dihedrals"]] == [tuple(d) for d in keywords.dihedrals]
    assert exported_keywords["grid_spacing"] == list(keywords.grid_spacing)

    # Stated, not silent: what TCKDB never stored is named in the report.
    assert document["input_data"]["specification"]["protocols"]["scan_results"] == "none"
    assert document["scan_results"] == {}
    assert any(entry.startswith("scan_results") for entry in report["not_carried"])
    assert document["extras"]["tckdb"]["export_report"] == report


@pytest.mark.parametrize("case", ROUND_TRIP_CASES)
def test_scan_coordinate_values_round_trip_exactly(case: str) -> None:
    """The stored values (the capture) are the exported grid angles, exactly."""
    scan = json.loads((READS / case / "scan.json").read_text())
    document, _ = export_scan(ScanStubClient(case), "calc_x")
    n = len(scan["coordinates"])
    stored = sorted(
        tuple(v["coordinate_value"] for v in sorted(p["coordinate_values"], key=lambda v: v["coordinate_index"]))
        for p in scan["points"]
    )
    exported = sorted(parse_grid_key(k, n) for k in document["final_molecules"])
    assert exported == [tuple(float(grid_angle(v)) for v in point) for point in stored]
    assert exported == sorted(_original(case)["points"])


def test_export_calculation_dispatches_scan() -> None:
    document = export_calculation(ScanStubClient("torsiondrive_v2"), "calc_x")
    assert document["schema_name"] == "qcschema_torsion_drive_result"


def test_exported_scan_is_refused_on_reimport() -> None:
    document, _ = export_scan(ScanStubClient("torsiondrive_v2"), "calc_x")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(json.dumps(document).encode())
    assert excinfo.value.code == E_TCKDB_EXPORT_REIMPORT_REFUSED


def test_every_point_geometry_is_fetched_once_with_isotopes() -> None:
    client = ScanStubClient("torsiondrive_2d_v2")
    export_scan(client, "calc_x")
    point_refs = [p["geometry_ref"] for p in client.scan["points"]]
    fetched = [c[1] for c in client.calls if c[0] == "get_geometry"]
    assert len(point_refs) == 16
    assert sorted(fetched) == sorted(point_refs + [g["geometry_ref"] for g in client.calculation["record"]["input_geometries"]])
    assert len([c for c in client.calls if c[0] == "get_json"]) == len(fetched)


# ---------------------------------------------------------------------------
# Refusals: what has no TorsionDrive representation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("bond_scan_hand_built", E_EXPORT_SCAN_NOT_TORSION_DRIVE),
        ("relative_sweep_hand_built", E_EXPORT_SCAN_COORDINATE_NONCONFORMING),
        ("rigid_scan_hand_built", E_EXPORT_SCAN_NOT_RELAXED),
    ],
)
def test_hand_built_scans_are_refused(case: str, code: str) -> None:
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient(case), "calc_x")
    assert excinfo.value.code == code, excinfo.value.message


def test_bond_scan_is_refused_before_any_point_is_read() -> None:
    client = ScanStubClient("bond_scan_hand_built")
    with pytest.raises(QCSchemaAdapterError):
        export_scan(client, "calc_x")
    assert not [c for c in client.calls if c[0] == "get_geometry"]


def _edit_coordinate_kind(kind: str):
    def edit(stub: ScanStubClient) -> None:
        stub.scan["coordinates"][0]["coordinate_kind"] = kind

    return edit


def test_improper_is_not_a_torsion_drive() -> None:
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient("torsiondrive_v2", edit=_edit_coordinate_kind("improper")), "c")
    assert excinfo.value.code == E_EXPORT_SCAN_NOT_TORSION_DRIVE


def test_unstated_relaxation_is_refused() -> None:
    def edit(stub):
        stub.scan["scan"]["is_relaxed"] = None

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient("torsiondrive_v2", edit=edit), "c")
    assert excinfo.value.code == E_EXPORT_SCAN_NOT_RELAXED


def test_point_without_energy_is_refused() -> None:
    def edit(stub):
        stub.scan["points"][1]["electronic_energy_hartree"] = None

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient("torsiondrive_v2", edit=edit), "c")
    assert excinfo.value.code == E_EXPORT_SCAN_POINT_INCOMPLETE


def test_two_points_on_one_grid_point_are_refused() -> None:
    def edit(stub):
        stub.scan["points"].append(dict(stub.scan["points"][0], point_index=99))

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient("torsiondrive_v2", edit=edit), "c")
    assert excinfo.value.code == E_EXPORT_SCAN_POINT_INCOMPLETE


def test_fractional_grid_spacing_is_refused_not_rounded() -> None:
    def edit(stub):
        stub.scan["coordinates"][0]["step_size"] = 89.5

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        export_scan(ScanStubClient("torsiondrive_v2", edit=edit), "c")
    assert excinfo.value.code == E_EXPORT_SCAN_GRID_NOT_INTEGRAL


def test_value_past_180_is_wrapped_and_reported() -> None:
    """-90 stored as 270 is the same physical angle (ADR 0020 allows it)."""

    def edit(stub):
        for point in stub.scan["points"]:
            value = point["coordinate_values"][0]
            if value["coordinate_value"] == -90.0:
                value["coordinate_value"] = 270.0

    document, report = export_scan(ScanStubClient("torsiondrive_v2", edit=edit), "c")
    assert sorted(document["final_molecules"]) == ["-90", "0", "180", "90"]
    assert any("wrapped" in entry for entry in report["not_carried"])


def test_points_are_read_across_pages() -> None:
    """With the page size cut to 4, the 16-point scan takes four pages and
    every point arrives -- the loop follows ``pagination.total``."""
    import tckdb_qcschema.scan_export as scan_export

    client = ScanStubClient("torsiondrive_2d_v2")
    original_limit = scan_export._PAGE_LIMIT
    scan_export._PAGE_LIMIT = 4
    try:
        document, _ = export_scan(client, "c")
    finally:
        scan_export._PAGE_LIMIT = original_limit
    offsets = [c[3] for c in client.calls if c[0] == "get_calculation_scan"]
    assert offsets == [0, 4, 8, 12]
    assert len(document["final_molecules"]) == 16
