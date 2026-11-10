"""Review follow-ups for #826: children of a served detail, and the legacy review-status header.

* D-b: the scientific network detail with ``include=solves`` shows its unreviewed solves badged and withholds rejected and
  deprecated ones (the legacy network detail already did).
* D-c: ``X-TCKDB-Review-Status`` is set only on a response that serves ONE record, from the gated id the path ends with; it
  is declared in OpenAPI beside ``X-TCKDB-Hidden-By-Default``, and a browser can read both (CORS ``expose_headers``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.common import RecordReviewStatus
from app.db.models.network_pdep import NetworkSolve
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"
LEGACY = "/api/v1"
UNLISTED = {RecordReviewStatus.not_reviewed.value, RecordReviewStatus.under_review.value}


@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _network_of(db_session, solve):
    from app.db.models.network import Network

    return db_session.scalar(select(Network).where(Network.id == solve.network_id))


def test_a_network_detail_shows_its_unreviewed_solves_badged(client, db_session, world):
    shown = 0
    for key in ("nsolve_h", "nsolve_h3", "nsolve_hsel"):
        solve = world.hidden[key]
        network = _network_of(db_session, solve)
        response = client.get(
            f"{BASE}/networks/{network.public_ref}", params={"profile": "curated", "include": ["solves"]}
        )
        assert response.status_code == 200, (key, response.text)
        body = response.json()
        listed = [s["network_solve_ref"] for s in body["record"]["solves"]]
        assert solve.public_ref in listed, key
        assert body["review_status_by_ref"][solve.public_ref] in UNLISTED
        assert body["record"]["evidence_summary"]["solve_count"] == len(listed), key
        shown += 1
    assert shown == 3


def test_a_network_detail_withholds_rejected_and_deprecated_solves(client, db_session, world):
    keys = ("nsolve_h", "nsolve_h3", "nsolve_hsel")
    refs = {key: world.hidden[key].public_ref for key in keys}
    networks = {key: _network_of(db_session, world.hidden[key]).public_ref for key in keys}
    statuses = world.withhold_hidden(db_session)
    assert all(statuses[key] in (RecordReviewStatus.rejected, RecordReviewStatus.deprecated) for key in keys)
    for key in keys:
        response = client.get(f"{BASE}/networks/{networks[key]}", params={"profile": "curated", "include": ["solves"]})
        assert response.status_code == 200, response.text
        assert refs[key] not in response.text, key
        record = response.json()["record"]
        assert record["evidence_summary"]["solve_count"] == len(record["solves"])
    assert db_session.scalars(select(NetworkSolve.id)).first() is not None  # the solves still exist: it is the rule, not absence


# ---------------------------------------------------------------------------
# D-c: the legacy review-status header
# ---------------------------------------------------------------------------


def test_the_header_is_on_single_records_only_and_comes_from_the_gated_id_the_path_ends_with(client, db_session, world):
    solve = world.hidden["nsolve_h"]
    network = _network_of(db_session, solve)
    calc = world.hidden["calc_h"]
    curated = {"profile": "curated"}

    # A solve is served under its own status, whatever its network's.
    detail = client.get(f"{LEGACY}/networks/{network.id}/solves/{solve.id}", params=curated)
    assert detail.status_code == 200, detail.text
    assert detail.headers["X-TCKDB-Review-Status"] in UNLISTED
    # A sub-resource list states no status: it serves many records, not the gated one in its path.
    sub_list = client.get(f"{LEGACY}/networks/{network.id}/solves", params=curated)
    assert sub_list.status_code == 200 and "X-TCKDB-Review-Status" not in sub_list.headers
    dependencies = client.get(f"{LEGACY}/calculations/{calc.id}/dependencies", params=curated)
    assert dependencies.status_code == 200 and "X-TCKDB-Review-Status" not in dependencies.headers
    # The record itself does.
    record = client.get(f"{LEGACY}/calculations/{calc.id}", params=curated)
    assert record.headers["X-TCKDB-Review-Status"] in UNLISTED


def test_the_headers_are_declared_in_openapi_and_exposed_to_browsers(client, monkeypatch):
    spec = client.get("/openapi.json").json()
    thermo = spec["paths"]["/api/v1/thermo/{thermo_id}"]["get"]["responses"]["200"]
    assert "X-TCKDB-Review-Status" in thermo["headers"]
    sub_list = spec["paths"]["/api/v1/calculations/{calculation_id}/dependencies"]["get"]["responses"]["200"]
    assert "X-TCKDB-Review-Status" not in sub_list.get("headers", {})
    declared = {
        header
        for item in spec["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict)
        for header in (operation.get("responses", {}).get("200", {}).get("headers", {}) or {})
    }
    assert {"X-TCKDB-Review-Status", "X-TCKDB-Hidden-By-Default"} <= declared

    from app.api import app as app_module
    from app.api.config import settings

    monkeypatch.setattr(settings, "cors_allow_origins", ["https://example.test"])
    built = app_module.create_app()
    cors = next(m for m in built.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert set(cors.kwargs["expose_headers"]) >= {"X-TCKDB-Review-Status", "X-TCKDB-Hidden-By-Default"}
