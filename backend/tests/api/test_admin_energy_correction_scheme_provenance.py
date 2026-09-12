"""Tests for the admin-only energy-correction-scheme provenance-attach route.

``PATCH /api/v1/admin/energy-correction-schemes/{ref}/provenance`` is the
only path that can add a citation or software identity to a scheme
deposited before it had one (correction-scheme-provenance plan §4.3).
Append-only per field: it fills a null, it never overwrites a value
already recorded. See ``app/api/routes/admin.py``.

Follows the existing admin-route testing pattern (mirroring
``test_admin_machine_review_inspection.py``): the ``client`` fixture's
default actor is role=user (the 403 path), ``login_as`` swaps roles, and
``anon_client`` exercises the anonymous 401 path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from tests.services.scientific_read._factories import (
    make_energy_correction_scheme,
    make_literature,
    make_lot,
    make_software_release,
)


def _url(ref: str) -> str:
    return f"/api/v1/admin/energy-correction-schemes/{ref}/provenance"


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def test_attach_provenance_requires_auth(anon_client, db_session):
    scheme = make_energy_correction_scheme(db_session)

    resp = anon_client.patch(
        _url(scheme.public_ref), json={"software": {"name": "Gaussian"}}
    )

    assert resp.status_code == 401, resp.text


def test_attach_provenance_requires_admin(
    client, db_session, login_as, _api_curator_user
):
    scheme = make_energy_correction_scheme(db_session)

    # Default actor is role=user.
    assert (
        client.patch(
            _url(scheme.public_ref), json={"software": {"name": "Gaussian"}}
        ).status_code
        == 403
    )

    # Curators are also forbidden -- this is an admin-only surface.
    login_as(_api_curator_user)
    assert (
        client.patch(
            _url(scheme.public_ref), json={"software": {"name": "Gaussian"}}
        ).status_code
        == 403
    )


def test_attach_provenance_404_for_missing_scheme(client, login_as, _api_admin_user):
    login_as(_api_admin_user)

    resp = client.patch(
        _url("ecs_doesnotexist00000000000"),
        json={"software": {"name": "Gaussian"}},
    )

    assert resp.status_code == 404, resp.text


def test_attach_software_to_uncited_software_less_scheme(
    client, db_session, login_as, _api_admin_user
):
    """The exact shape of the two live rows: no citation, no software."""
    lot = make_lot(db_session)
    scheme = make_energy_correction_scheme(db_session, lot=lot)
    assert scheme.software_release_id is None
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref), json={"software": {"name": "Gaussian"}}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["energy_correction_scheme_ref"] == scheme.public_ref
    assert body["software_ref"] is not None
    assert body["source_literature_ref"] is None

    db_session.refresh(scheme)
    assert scheme.software_release_id is not None
    assert scheme.software_release.software.name == "Gaussian"


def test_attach_literature_via_manual_citation(
    client, db_session, login_as, _api_admin_user
):
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={
            "source_literature": {
                "kind": "article",
                "title": "A manually-entered citation",
            }
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_literature_ref"] is not None

    db_session.refresh(scheme)
    assert scheme.source_literature_id is not None
    assert scheme.source_literature.title == "A manually-entered citation"


def test_attach_both_software_and_literature_in_one_call(
    client, db_session, login_as, _api_admin_user
):
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={
            "software": {"name": "ORCA"},
            "source_literature": {"kind": "article", "title": "Both at once"},
        },
    )

    assert resp.status_code == 200, resp.text
    db_session.refresh(scheme)
    assert scheme.software_release_id is not None
    assert scheme.source_literature_id is not None


def test_attach_provenance_refuses_to_overwrite_existing_software(
    client, db_session, login_as, _api_admin_user
):
    """Append-only per field: a non-null column is never overwritten."""
    gaussian_release = make_software_release(db_session, name="Gaussian", version=None)
    scheme = make_energy_correction_scheme(
        db_session, software_release=gaussian_release
    )
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref), json={"software": {"name": "ORCA"}}
    )

    assert resp.status_code == 409, resp.text

    db_session.refresh(scheme)
    assert scheme.software_release.software.name == "Gaussian"


def test_attach_provenance_refuses_to_overwrite_existing_literature(
    client, db_session, login_as, _api_admin_user
):
    lit = make_literature(db_session, title="Original citation")
    scheme = make_energy_correction_scheme(db_session, source_literature=lit)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={
            "source_literature": {
                "kind": "article",
                "title": "A different citation",
            }
        },
    )

    assert resp.status_code == 409, resp.text

    db_session.refresh(scheme)
    assert scheme.source_literature.title == "Original citation"


def test_attach_provenance_fills_one_field_leaves_other_null(
    client, db_session, login_as, _api_admin_user
):
    """Per-field, not all-or-nothing: filling software leaves literature
    untouched (and still fillable by a later call)."""
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref), json={"software": {"name": "Gaussian"}}
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(scheme)
    assert scheme.software_release_id is not None
    assert scheme.source_literature_id is None

    # A second call can still fill the citation -- it was never touched.
    resp2 = client.patch(
        _url(scheme.public_ref),
        json={"source_literature": {"kind": "article", "title": "Filled later"}},
    )
    assert resp2.status_code == 200, resp2.text
    db_session.refresh(scheme)
    assert scheme.source_literature_id is not None


def test_attach_provenance_by_integer_id(client, db_session, login_as, _api_admin_user):
    """Path handle also accepts the integer PK, not only the public ref."""
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        f"/api/v1/admin/energy-correction-schemes/{scheme.id}/provenance",
        json={"software": {"name": "Gaussian"}},
    )

    assert resp.status_code == 200, resp.text
