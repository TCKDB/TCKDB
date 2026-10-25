"""Cursor paging on the list reads ``test_cursor_paging`` has no seed for: point series, the custody record, releases.

The same properties, checked directly: a cursor walk is complete and ends, a record added mid-traversal is outside the
snapshot, and a cursor that is not this request's is refused with its code.
"""

from __future__ import annotations

import pytest

from app.db.models.calculation import (
    CalculationIRCPoint,
    CalculationPathSearchPoint,
    CalculationScanPoint,
)
from app.db.models.common import ArtifactIntegrityDetectionContext, ArtifactIntegrityFinding
from tests.api.scientific.test_api_scientific_artifact_integrity import _broken_artifact, _record
from tests.api.scientific.test_api_scientific_calculation_paths import (
    _make_irc_calc,
    _make_path_search_calc,
    _make_scan_calc,
)
from tests.api.test_cursor_paging import (
    API,
    UNSEEDED_ROUTES,
    _curator,  # noqa: F401  (autouse fixture of the sibling module)
    _flip,
    code_of,
    decode,
    encode,
    keyset,
    with_params,
)


def walk_url(client, url: str, key: str, limit: int, **params):
    """Follow ``next_cursor`` from a URL whose records sit under ``key``; returns every item and every page."""
    body = client.get(with_params(url, limit=limit, **params)).json()
    pages = [body]
    items = list(body[key])
    while body["next_cursor"] is not None:
        assert len(pages) < 50
        response = client.get(with_params(url, limit=limit, cursor=body["next_cursor"], **params))
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append(body)
        items.extend(body[key])
    return items, pages


def _with_points(db_session, make, point_model, first=0):
    calc = make(db_session)
    for index in (3, 1, 4, 0, 2):  # inserted out of order: the order is the key's, not the insertion's
        db_session.add(point_model(calculation_id=calc.id, point_index=first + index))
    db_session.flush()
    return calc


#: (maker, point model, route suffix, first point index): a scan numbers its points from 1, the others from 0.
SERIES = [
    (_make_scan_calc, CalculationScanPoint, "scan", 1),
    (_make_irc_calc, CalculationIRCPoint, "irc", 0),
    (_make_path_search_calc, CalculationPathSearchPoint, "path-search", 0),
]


@pytest.mark.parametrize(("make", "point_model", "suffix", "first"), SERIES, ids=[s[2] for s in SERIES])
def test_the_point_series_page_by_cursor(make, point_model, suffix, first, client, db_session):
    assert f"{API}/calculations/{{calculation_ref_or_id}}/{suffix}" in UNSEEDED_ROUTES
    calc = _with_points(db_session, make, point_model, first)
    url = f"{API}/calculations/{calc.public_ref}/{suffix}"

    points, pages = walk_url(client, url, "points", limit=2)

    assert [p["point_index"] for p in points] == [first + i for i in range(5)]
    assert pages[-1]["next_cursor"] is None
    assert [p["pagination"]["mode"] for p in pages] == ["offset", "cursor", "cursor"]
    assert {p["pagination"]["total"] for p in pages} == {5}

    cursor = pages[0]["next_cursor"]
    assert code_of(client.get(with_params(url, cursor=_flip(cursor)))) == "invalid_cursor"
    assert code_of(client.get(with_params(url, cursor=cursor, offset=1))) == "cursor_offset_conflict"
    assert code_of(client.get(with_params(url, cursor=cursor, include_geometries="true"))) == "cursor_query_mismatch"
    assert code_of(client.get(with_params(url, cursor=cursor, profile="curated"))) == "cursor_query_mismatch"
    assert code_of(client.get(with_params(f"{API}/releases", cursor=cursor))) == "cursor_query_mismatch"
    old = encode({**decode(cursor), "v": keyset.CURSOR_VERSION - 1})
    assert code_of(client.get(with_params(url, cursor=old))) == "invalid_cursor"


def test_a_point_added_mid_traversal_is_outside_the_snapshot(client, db_session):
    calc = _with_points(db_session, _make_scan_calc, CalculationScanPoint, 1)
    url = f"{API}/calculations/{calc.public_ref}/scan"
    first = client.get(with_params(url, limit=2)).json()
    db_session.add(CalculationScanPoint(calculation_id=calc.id, point_index=6))
    db_session.flush()

    rest = client.get(with_params(url, limit=10, cursor=first["next_cursor"])).json()

    assert [p["point_index"] for p in rest["points"]] == [3, 4, 5]
    assert rest["pagination"]["total"] == 5
    assert client.get(with_params(url, limit=10)).json()["pagination"]["total"] == 6


def _observed_digests(db_session, n):
    shas = []
    for i in range(n):
        _calc, artifact, sha = _broken_artifact(db_session, f"cursor-{i}")
        _record(
            db_session,
            sha,
            ArtifactIntegrityFinding.digest_mismatch,
            ArtifactIntegrityDetectionContext.download,
            artifact_id=artifact.id,
            expected_bytes=artifact.bytes,
        )
        shas.append(sha)
    return shas


def _observe_again(db_session, sha):
    _record(db_session, sha, ArtifactIntegrityFinding.digest_mismatch, ArtifactIntegrityDetectionContext.download)


def test_the_custody_list_pages_by_cursor_as_of_its_snapshot(client, db_session, login_as, _api_curator_user):
    shas = _observed_digests(db_session, 3)
    login_as(_api_curator_user)
    url = f"{API}/artifacts/integrity"
    first = client.get(with_params(url, limit=1)).json()
    delivered = [r["sha256"] for r in first["records"]]

    # Every digest, the delivered one included, is observed again mid-traversal. A digest's position is its last
    # observation, so by offset each would jump to the front; inside the snapshot none moves.
    for sha in shas:
        _observe_again(db_session, sha)

    seen = list(delivered)
    body = first
    while body["next_cursor"] is not None:
        body = client.get(with_params(url, limit=1, cursor=body["next_cursor"])).json()
        seen.extend(r["sha256"] for r in body["records"])
    assert len(seen) == len(set(seen)) == 3
    assert set(seen) == set(shas)

    cursor = first["next_cursor"]
    assert code_of(client.get(with_params(url, cursor=cursor, only_currently_broken="true"))) == "cursor_query_mismatch"
    assert code_of(client.get(with_params(url, cursor=cursor, offset=2))) == "cursor_offset_conflict"
    assert code_of(client.get(with_params(url, cursor="garbage"))) == "invalid_cursor"


def test_one_digests_history_pages_by_cursor(client, db_session, login_as, _api_curator_user):
    (sha,) = _observed_digests(db_session, 1)
    for _ in range(4):
        _observe_again(db_session, sha)
    login_as(_api_curator_user)
    url = f"{API}/artifacts/{sha}/integrity"
    first = client.get(with_params(url, limit=2)).json()
    total = first["pagination"]["total"]
    assert total >= 3
    assert first["next_cursor"] is not None

    _observe_again(db_session, sha)
    rest = client.get(with_params(url, limit=50, cursor=first["next_cursor"])).json()

    assert len(first["observations"]) + len(rest["observations"]) == total
    assert rest["next_cursor"] is None
    assert code_of(client.get(with_params(url, cursor=first["next_cursor"], offset=1))) == "cursor_offset_conflict"
    assert code_of(client.get(with_params(url, cursor="garbage"))) == "invalid_cursor"


def test_releases_page_by_cursor(client, db_session, login_as, _api_curator_user):
    from tests.api.test_api_dataset_releases import POLICY, RELEASE

    login_as(_api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    for i in range(3):
        created = client.post("/api/v1/releases", json={**RELEASE, "tag": f"2026.07.{i}"})
        assert created.status_code == 201, created.text

    url = f"{API}/releases"
    first = client.get(with_params(url, limit=1)).json()
    assert [r["tag"] for r in first["records"]] == ["2026.07.2"]

    created = client.post("/api/v1/releases", json={**RELEASE, "tag": "2026.07.9"})
    assert created.status_code == 201, created.text

    tags = [r["tag"] for r in first["records"]]
    body = first
    while body["next_cursor"] is not None:
        body = client.get(with_params(url, limit=1, cursor=body["next_cursor"])).json()
        tags.extend(r["tag"] for r in body["records"])
    assert tags == ["2026.07.2", "2026.07.1", "2026.07.0"]

    cursor = first["next_cursor"]
    assert code_of(client.get(with_params(url, cursor=cursor, status="published"))) == "cursor_query_mismatch"
    assert code_of(client.get(with_params(url, cursor=cursor, offset=1))) == "cursor_offset_conflict"
    assert code_of(client.get(with_params(url, cursor="garbage"))) == "invalid_cursor"
    assert client.get(with_params(url, limit=10)).json()["records"][0]["tag"] == "2026.07.9"


def test_a_release_ledger_pages_by_cursor(client, db_session, login_as, _api_curator_user):
    from app.db.models.app_user import AppUser
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from app.services.record_review import set_record_review_status
    from tests.api.test_api_dataset_releases import POLICY, RELEASE
    from tests.services.release._attest import attest_thermo
    from tests.services.scientific_read._factories import make_species, make_species_entry, make_thermo_scalar

    curator = db_session.get(AppUser, _api_curator_user)
    login_as(_api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases", json=RELEASE).status_code == 201

    def select_one(index: int) -> str:
        entry = make_species_entry(db_session, species=make_species(db_session, smiles="C" * (index + 1)))
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-10.0 * index, s298_j_mol_k=200.0)
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
        attest_thermo(db_session, depositor=curator, rows=[thermo])
        db_session.flush()
        created = client.post(
            f"/api/v1/releases/{RELEASE['tag']}/selections",
            json={"record_ref": thermo.public_ref, "rationale": f"Pick {index}."},
        )
        assert created.status_code == 201, created.text
        return created.json()["selection_ref"]

    refs = [select_one(i) for i in range(4)]
    url = f"{API}/releases/{RELEASE['tag']}/selections"
    first = client.get(with_params(url, limit=2)).json()
    refs.append(select_one(4))  # a selection made mid-traversal

    seen = [r["selection_ref"] for r in first["records"]]
    body = first
    while body["next_cursor"] is not None:
        body = client.get(with_params(url, limit=2, cursor=body["next_cursor"])).json()
        seen.extend(r["selection_ref"] for r in body["records"])
    assert seen == refs[:4]
    assert [r["selection_ref"] for r in client.get(with_params(url, limit=10)).json()["records"]] == refs

    cursor = first["next_cursor"]
    assert code_of(client.get(with_params(url, cursor=cursor, include_superseded="false"))) == "cursor_query_mismatch"
    assert code_of(client.get(with_params(url, cursor=cursor, offset=1))) == "cursor_offset_conflict"
    assert code_of(client.get(with_params(url, cursor="garbage"))) == "invalid_cursor"
