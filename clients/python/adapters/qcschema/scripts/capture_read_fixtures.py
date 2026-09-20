"""Capture pinned backend read fixtures for the exporter round trip (C-Q3).

``tests/test_round_trip.py`` exercises :func:`tckdb_qcschema.exporter.export_calculation`
against a *stub* client (no live server, no database -- see the module's own
"No live server in the adapter tests" note in the C-Q3 brief) that serves
pinned JSON bodies recorded from a real backend. This module is how those
bodies were produced, and how they are reproduced if the backend response
shapes ever drift.

Not runnable in place: it depends on ``backend/tests/conftest.py``'s
``client``/``db_session`` pytest fixtures (DB bootstrap against
``DB_TEST_NAME``, Alembic migration, a per-test transaction that is rolled
back at teardown so nothing is left behind in the database) -- reusing that
well-tested machinery beats re-implementing DB/app wiring here, but it means
this file is not itself a backend test module (sovereignty: nothing under
``backend/`` changes for C-Q3). To (re)run it:

    cp clients/python/adapters/qcschema/scripts/capture_read_fixtures.py \\
        backend/tests/api/test_zzz_tmp_capture_qcschema_reads.py

    DB_TEST_NAME=tckdb_test_q3 \\
    DB_USER=tckdb DB_PASSWORD=tckdb DB_HOST=127.0.0.1 DB_PORT=5432 \\
    PYTHONPATH="$(pwd)/schemas/python/tckdb-schemas:${PYTHONPATH:-}" \\
        conda run -n tckdb_env pytest \\
        backend/tests/api/test_zzz_tmp_capture_qcschema_reads.py -q

    rm backend/tests/api/test_zzz_tmp_capture_qcschema_reads.py

Posts the ``energy_v1`` and ``hessian_v1`` backend corpus payloads (C-Q2,
``backend/tests/fixtures/qcschema/<case>/payload.json`` + ``artifact.json``)
through the real ``POST /uploads/conformers`` and
``POST /calculations/{id}/artifacts`` routes -- exactly as
``test_api_qcschema_fixtures.py`` does -- then issues the exact reads
:func:`tckdb_qcschema.exporter.export_calculation` makes against a live
server:

* ``GET /scientific/calculations/{id}?include=results,input_geometries,output_geometries,internal_ids``
* ``GET /scientific/geometries/{geometry_ref}`` for every geometry the
  calculation record links, plus (for a ``freq`` calculation with a stored
  Hessian) ``GET /scientific/geometries/{hessian.geometry_id}`` -- the exact
  geometry the matrix was computed at, which is not always the same row as
  ``input_geometries[0]`` in general even though it is for this corpus's
  water fixtures.
* ``GET /calculations/{id}/hessian`` (C-Q2) -- 404 for ``energy_v1``
  (no stored Hessian), 200 for ``hessian_v1``.
* The legacy per-atom-isotope read (review round 2, C-Q3): for
  ``energy_v1`` (an ``sp`` case), ``GET /geometries?geom_hash=<hash>``
  using the calculation's own geometry link's ``geom_hash``; for
  ``hessian_v1`` (a ``freq`` case), ``GET /geometries/{id}`` using the
  Hessian read's own ``geometry_id``. See ``exporter.py``'s module
  docstring's "Isotopes" section for why this legacy surface, rather than
  the scientific geometry read, is what ``export_calculation`` reads
  isotopes from.

and writes the raw JSON response bodies to
``clients/python/adapters/qcschema/tests/fixtures/reads/<case>/*.json``:
``calculation.json`` (the calculation detail envelope), ``geometries.json``
(a dict keyed by whatever handle -- ``geometry_ref`` or, for the Hessian's
own geometry, the bare numeric id -- was used to fetch it), ``hessian.json``
(``{"status_code": ..., "body": ...}`` so a 404 is captured as data, not an
exception), ``legacy_geometry.json`` (``{"route": "geom_hash_query" |
"by_id", "request_path": ..., "response": ...}`` -- ``request_path`` is
the exact, un-prefixed path :func:`tckdb_qcschema.exporter.export_calculation`
passes to ``TCKDBClient.get_json``, so the pinned-read stub can assert it
was called with the exact path the exporter actually builds) and
``meta.json`` (bookkeeping: which calculation id this run happened to
mint, for a human re-reading the capture -- never read by the round-trip
test itself, which only reads the response-body files).

Measured 2026-09-20 against this backend: the default deployment's
internal-id visibility policy strips ``calculation_id`` from
``GET /scientific/calculations/{id}`` and ``geometry_id`` from
``GET /scientific/geometries/{ref}`` even with ``include=internal_ids``
requested -- confirmed empirically in the captured ``calculation.json``
(the key is simply absent). ``GET /calculations/{id}/hessian`` is *not*
gated the same way: it is a plain, pre-Phase-D ``/calculations/{id}/...``
route that requires the caller to already have the integer id, and its
response body (``CalculationHessianRead``) always carries both
``calculation_id`` and ``geometry_id``. See ``exporter.py``'s module
docstring for how ``export_calculation`` copes with this asymmetry (a
caller must supply the integer id directly to export a ``freq`` record on
a deployment that hides it from the scientific read).

Measured again 2026-09-20 (review round 2): ``GET /geometries/{id}`` and
``GET /geometries?geom_hash=...`` are a *different* pair of routes from
the ones above -- plain, pre-Phase-D ``/geometries/...`` routes (not
``/scientific/geometries/...``), gated by the exact same
``require_auth_for_legacy_reads`` dependency as
``GET /calculations/{id}/hessian`` (see ``backend/app/api/router.py``).
Both serve ``GeometryAtomRead.isotope_mass_number`` per atom, which the
scientific geometry read never does. This is why the earlier revision of
this docstring and of ``exporter.py``'s module docstring, which said
closing the isotope gap needed a backend schema change, was wrong: the
field was already being served by a route this adapter already reads
from (the Hessian route) for a different purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "qcschema"
OUT_DIR = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "python"
    / "adapters"
    / "qcschema"
    / "tests"
    / "fixtures"
    / "reads"
)

#: The two round-trip cases named in the C-Q3 brief. Deliberately a fixed
#: pair, not "every route case in the C-Q2 corpus" -- a gradient or
#: optimization capture here would be fixture data nothing reads, since the
#: exporter supports only ``sp`` and ``freq``.
CASES = ("energy_v1", "hessian_v1")


@pytest.fixture
def stub_store_artifact(monkeypatch):
    """Same fake object-store writer as ``test_api_qcschema_fixtures.py``.

    Duplicated rather than imported: this file is copied standalone into
    ``backend/tests/api/`` to run (see the module docstring), so it cannot
    import a fixture from a sibling test module without also copying that
    module's own import graph.
    """
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", _fake_store)
    return written


def _capture(client, case: str) -> None:
    case_dir = CORPUS_DIR / case
    payload = json.loads((case_dir / "payload.json").read_text())
    artifact = json.loads((case_dir / "artifact.json").read_text())

    resp = client.post(
        "/api/v1/uploads/conformers",
        json=payload,
        headers={"Idempotency-Key": f"qcschema-read-capture-{case}-conformers"},
    )
    assert resp.status_code == 201, f"{case}: {resp.status_code}\n{resp.text[:2000]}"
    calc_id = resp.json()["primary_calculation"]["calculation_id"]

    artifact_resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": [artifact]},
        headers={"Idempotency-Key": f"qcschema-read-capture-{case}-artifact"},
    )
    assert artifact_resp.status_code == 201, artifact_resp.text[:2000]

    calc_detail = client.get(
        f"/api/v1/scientific/calculations/{calc_id}",
        params={
            "include": ["results", "input_geometries", "output_geometries", "internal_ids"]
        },
    )
    assert calc_detail.status_code == 200, calc_detail.text[:2000]
    calc_json = calc_detail.json()

    out_case_dir = OUT_DIR / case
    out_case_dir.mkdir(parents=True, exist_ok=True)
    (out_case_dir / "calculation.json").write_text(
        json.dumps(calc_json, indent=2, sort_keys=True) + "\n"
    )

    geometry_refs: set[str] = set()
    record = calc_json["record"]
    for block_name in ("input_geometries", "output_geometries"):
        for link in record.get(block_name) or []:
            geometry_refs.add(link["geometry_ref"])

    geometries: dict[str, object] = {}
    for geom_ref in sorted(geometry_refs):
        geom_resp = client.get(f"/api/v1/scientific/geometries/{geom_ref}")
        assert geom_resp.status_code == 200, geom_resp.text[:2000]
        geometries[geom_ref] = geom_resp.json()

    hessian_resp = client.get(f"/api/v1/calculations/{calc_id}/hessian")
    (out_case_dir / "hessian.json").write_text(
        json.dumps(
            {"status_code": hessian_resp.status_code, "body": hessian_resp.json()},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    if hessian_resp.status_code == 200:
        hessian_geom_id = hessian_resp.json()["geometry_id"]
        hgeom_resp = client.get(f"/api/v1/scientific/geometries/{hessian_geom_id}")
        assert hgeom_resp.status_code == 200, hgeom_resp.text[:2000]
        geometries[str(hessian_geom_id)] = hgeom_resp.json()

    (out_case_dir / "geometries.json").write_text(
        json.dumps(geometries, indent=2, sort_keys=True) + "\n"
    )

    # Legacy per-atom-isotope read (review round 2): the exact call
    # tckdb_qcschema.exporter._fetch_isotope_atoms makes for this record's
    # own calculation type -- see the module docstring above.
    calc_type = record["calculation"]["type"]
    if calc_type == "sp":
        first_link = None
        for block_name in ("input_geometries", "output_geometries"):
            links = record.get(block_name) or []
            if links:
                first_link = links[0]
                break
        assert first_link is not None, f"{case}: sp record has no geometry link"
        geom_hash = first_link["geom_hash"]
        request_path = f"/geometries?geom_hash={geom_hash}"
        legacy_resp = client.get(f"/api/v1{request_path}")
        assert legacy_resp.status_code == 200, legacy_resp.text[:2000]
        legacy_geometry = {
            "route": "geom_hash_query",
            "request_path": request_path,
            "response": legacy_resp.json(),
        }
    else:
        assert hessian_resp.status_code == 200, (
            f"{case}: freq case with no stored Hessian to read a "
            "geometry_id from"
        )
        hessian_geom_id = hessian_resp.json()["geometry_id"]
        request_path = f"/geometries/{hessian_geom_id}"
        legacy_resp = client.get(f"/api/v1{request_path}")
        assert legacy_resp.status_code == 200, legacy_resp.text[:2000]
        legacy_geometry = {
            "route": "by_id",
            "request_path": request_path,
            "response": legacy_resp.json(),
        }

    (out_case_dir / "legacy_geometry.json").write_text(
        json.dumps(legacy_geometry, indent=2, sort_keys=True) + "\n"
    )

    (out_case_dir / "meta.json").write_text(
        json.dumps(
            {
                "case": case,
                "calculation_id": calc_id,
                "captured_from": (
                    "backend GET routes, real DB, C-Q3 pinned-read capture"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"captured {case} -> {out_case_dir}")


def test_capture_energy_v1_and_hessian_v1(client, db_session, stub_store_artifact) -> None:
    """Not collected here -- see the module docstring's copy-then-run recipe."""
    del db_session  # only needed to bind to the same per-test transaction as `client`
    for case in CASES:
        _capture(client, case)
