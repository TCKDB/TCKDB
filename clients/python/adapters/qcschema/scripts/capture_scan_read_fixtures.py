"""Capture pinned backend reads for the scan exporter's tests.

The scan counterpart of ``capture_read_fixtures.py``, run the same way
(copied into ``backend/tests/api/`` so it can use the backend test
fixtures, then removed):

    cp clients/python/adapters/qcschema/scripts/capture_scan_read_fixtures.py \\
        backend/tests/api/test_zzz_tmp_capture_qcschema_scan_reads.py

    DB_TEST_NAME=tckdb_test_scan_capture \\
    DB_USER=tckdb DB_PASSWORD=tckdb DB_HOST=127.0.0.1 DB_PORT=5432 \\
    PYTHONPATH="$(pwd)/schemas/python/tckdb-schemas:${PYTHONPATH:-}" \\
        conda run -n tckdb_env pytest \\
        backend/tests/api/test_zzz_tmp_capture_qcschema_scan_reads.py -q

    rm backend/tests/api/test_zzz_tmp_capture_qcschema_scan_reads.py

For each case it posts a ``ComputedSpeciesUploadRequest`` through the real
``POST /uploads/computed-species`` route, then records the exact reads
:func:`tckdb_qcschema.scan_export.export_scan` makes:

* ``GET /scientific/calculations/{id}?include=results,input_geometries,parameters``
  -> ``calculation.json``
* ``GET /scientific/calculations/{id}/scan?include_geometries=true&offset=0&limit=200``
  -> ``scan.json``
* ``GET /scientific/geometries/{ref}`` for every point geometry and input
  geometry -> ``geometries.json`` (keyed by ref)
* ``GET /geometries?geom_hash=<hash>`` for each of those -> ``legacy_geometries.json``
  (keyed by the un-prefixed request path the exporter passes to ``get_json``)

into ``clients/python/adapters/qcschema/tests/fixtures/reads_scan/<case>/``.

Cases:

* the three TorsionDrive route cases of the backend scan corpus
  (``backend/tests/fixtures/qcschema_scan/<case>/payload.json``), stored as
  the importer maps them;
* three **hand-built** refusal cases, each one documented edit to the
  ``torsiondrive_v2`` (or ``_v1``) payload, posted and stored for real so
  the exporter sees exactly what the backend serves for such a scan:

  - ``bond_scan_hand_built``: the coordinate re-declared as the O-O
    ``bond`` (atoms 1, 2), each point's value the O-O distance measured
    from that point's own stored geometry (so it conforms to ADR 0020).
    A bond scan has no TorsionDrive representation;
  - ``relative_sweep_hand_built``: the ``torsiondrive_v1`` values rewritten
    as a sweep relative to the first point, with ``start_value`` holding
    the first point's angle -- ADR 0019's superseded convention, the one
    all 46 pre-ADR-0020 deposited series hold;
  - ``rigid_scan_hand_built``: ``is_relaxed`` set to ``false``.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

SCAN_CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "qcschema_scan"
OUT_DIR = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "python"
    / "adapters"
    / "qcschema"
    / "tests"
    / "fixtures"
    / "reads_scan"
)

ROUTE_CASES = ("torsiondrive_v1", "torsiondrive_v2", "torsiondrive_2d_v2")


@pytest.fixture
def stub_store_artifact(monkeypatch):
    """Same fake object-store writer as ``test_api_qcschema_fixtures.py``."""

    def _fake_store(content: bytes, sha256: str) -> str:
        return f"s3://test-bucket/{sha256[:2]}/{sha256}"

    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", _fake_store)


def _scan_calc(payload: dict) -> dict:
    return payload["conformers"][0]["additional_calculations"][0]


def _xyz(point: dict) -> list[tuple[str, float, float, float]]:
    lines = point["geometry"]["xyz_text"].strip().splitlines()[2:]
    atoms = []
    for line in lines:
        symbol, x, y, z = line.split()
        atoms.append((symbol, float(x), float(y), float(z)))
    return atoms


def _bond_scan(payload: dict) -> dict:
    derived = copy.deepcopy(payload)
    scan = _scan_calc(derived)["scan_result"]
    (coordinate,) = scan["coordinates"]
    coordinate.update(
        coordinate_kind="bond",
        atom1_index=1,
        atom2_index=2,
        atom3_index=None,
        atom4_index=None,
        step_size=None,
        resolution_degrees=None,
        value_unit="angstrom",
    )
    for point in scan["points"]:
        (_s1, *a), (_s2, *b) = _xyz(point)[0], _xyz(point)[1]
        distance = round(math.dist(a, b), 10)
        point["coordinate_values"] = [
            {"coordinate_index": 1, "coordinate_value": distance, "value_unit": "angstrom"}
        ]
    _scan_calc(derived)["key"] = "qcschema_scan_bond"
    return derived


def _relative_sweep(payload: dict) -> dict:
    derived = copy.deepcopy(payload)
    scan = _scan_calc(derived)["scan_result"]
    first = scan["points"][0]["coordinate_values"][0]["coordinate_value"]
    scan["coordinates"][0]["start_value"] = first
    for point in scan["points"]:
        value = point["coordinate_values"][0]
        value["coordinate_value"] = value["coordinate_value"] - first
    return derived


def _rigid(payload: dict) -> dict:
    derived = copy.deepcopy(payload)
    _scan_calc(derived)["scan_result"]["is_relaxed"] = False
    return derived


def _cases() -> list[tuple[str, dict]]:
    def load(case: str) -> dict:
        return json.loads((SCAN_CORPUS / case / "payload.json").read_text())

    cases = [(case, load(case)) for case in ROUTE_CASES if (SCAN_CORPUS / case).exists()]
    cases.append(("bond_scan_hand_built", _bond_scan(load("torsiondrive_v2"))))
    cases.append(("relative_sweep_hand_built", _relative_sweep(load("torsiondrive_v1"))))
    cases.append(("rigid_scan_hand_built", _rigid(load("torsiondrive_v2"))))
    return cases


def _capture(client, case: str, payload: dict) -> None:
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=payload,
        headers={"Idempotency-Key": f"qcschema-scan-read-capture-{case}"},
    )
    assert resp.status_code == 201, f"{case}: {resp.status_code}\n{resp.text[:2000]}"
    scan_id = resp.json()["conformers"][0]["additional_calculations"][0]["calculation_id"]

    detail = client.get(
        f"/api/v1/scientific/calculations/{scan_id}",
        params={"include": ["results", "input_geometries", "parameters"]},
    )
    assert detail.status_code == 200, detail.text[:2000]
    scan = client.get(
        f"/api/v1/scientific/calculations/{scan_id}/scan",
        params={"include_geometries": "true", "offset": 0, "limit": 200},
    )
    assert scan.status_code == 200, scan.text[:2000]

    links = [
        (p.get("geometry_ref"), (p.get("geometry_link") or {}).get("geom_hash"))
        for p in scan.json()["points"]
    ] + [(g["geometry_ref"], g.get("geom_hash")) for g in detail.json()["record"]["input_geometries"]]
    geometries: dict[str, object] = {}
    legacy: dict[str, object] = {}
    for ref, geom_hash in links:
        geom = client.get(f"/api/v1/scientific/geometries/{ref}")
        assert geom.status_code == 200, geom.text[:2000]
        geometries[ref] = geom.json()
        path = f"/geometries?geom_hash={geom_hash}"
        legacy_resp = client.get(f"/api/v1{path}")
        assert legacy_resp.status_code == 200, legacy_resp.text[:2000]
        legacy[path] = legacy_resp.json()

    out = OUT_DIR / case
    out.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("calculation.json", detail.json()),
        ("scan.json", scan.json()),
        ("geometries.json", geometries),
        ("legacy_geometries.json", legacy),
        ("meta.json", {"case": case, "calculation_id": scan_id, "captured_from": "backend GET routes, real DB"}),
    ):
        (out / name).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    print(f"captured {case} -> {out}")


def test_capture_scan_reads(client, db_session, stub_store_artifact) -> None:
    """Not collected here -- see the module docstring's copy-then-run recipe."""
    del db_session
    for case, payload in _cases():
        _capture(client, case, payload)
