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
    make_workflow_tool_release,
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
    assert body["source_literature_ref"] is None

    db_session.refresh(scheme)
    assert scheme.software_release_id is not None
    assert scheme.software_release.software.name == "Gaussian"

    # Follow the ref to a real record rather than asserting it is merely
    # non-empty. This field used to carry a ``software`` ref and now
    # carries a ``software_release`` one (c24ce2d9c198); a non-emptiness
    # check could not tell those apart, which is how the change went
    # unnoticed until review of #458.
    assert body["software_release_ref"] == scheme.software_release.public_ref
    assert body["software_release_ref"].startswith("srel_")


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


def test_attach_software_release_sets_exact_release(
    client, db_session, login_as, _api_admin_user
):
    """A PATCH carrying ``version``/``revision`` sets exactly that release,
    not the version-less row for the program (correction-scheme-provenance
    plan §5.2, PR 3: ``software`` widens from ``SoftwareRef`` to
    ``SoftwareReleaseRef``)."""
    scheme = make_energy_correction_scheme(db_session)
    assert scheme.software_release_id is None
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={
            "software": {
                "name": "Gaussian",
                "version": "16",
                "revision": "C.02",
                "build": "EM64L",
            }
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    db_session.refresh(scheme)
    assert scheme.software_release_id is not None
    assert scheme.software_release.software.name == "Gaussian"
    # The assertion that actually distinguishes this from the name-only
    # path: the exact release, not the version-less one, was set.
    assert scheme.software_release.version == "16"
    assert scheme.software_release.revision == "C.02"
    # `build` is forwarded by resolve_software_release_ref too, and was
    # unpinned until review of #461: setting `build=None` in that helper
    # left all 13 tests green. Five fields are forwarded; assert the ones
    # an admin can actually set.
    assert scheme.software_release.build == "EM64L"

    assert body["software_release_ref"] == scheme.software_release.public_ref


def test_attach_software_name_only_still_resolves_version_less_release(
    client, db_session, login_as, _api_admin_user
):
    """Name-only stays a complete, honest deposit under the widened field:
    it resolves to the version-less release row for the program, and a
    second name-only PATCH on a different scheme reuses that same row
    rather than creating a second one."""
    scheme_a = make_energy_correction_scheme(db_session, name="scheme_a")
    scheme_b = make_energy_correction_scheme(db_session, name="scheme_b")
    login_as(_api_admin_user)

    resp_a = client.patch(
        _url(scheme_a.public_ref), json={"software": {"name": "Gaussian"}}
    )
    assert resp_a.status_code == 200, resp_a.text

    resp_b = client.patch(
        _url(scheme_b.public_ref), json={"software": {"name": "Gaussian"}}
    )
    assert resp_b.status_code == 200, resp_b.text

    db_session.refresh(scheme_a)
    db_session.refresh(scheme_b)

    assert scheme_a.software_release.version is None
    assert scheme_a.software_release_id == scheme_b.software_release_id
    assert resp_a.json()["software_release_ref"] == resp_b.json()["software_release_ref"]


def test_attach_software_release_collision_writes_nothing(
    client, db_session, login_as, _api_admin_user
):
    """Filling ``software`` such that the resulting 8-column identity tuple
    collides with another existing scheme is refused with 409 and writes
    nothing. The widened identity now includes ``units`` and
    ``software_release_id`` (correction-scheme-provenance plan §4/§5.2);
    build the fixture so the two rows differ *only* by
    ``software_release_id`` before the PATCH."""
    lot = make_lot(db_session)
    # Canonical form ("Gaussian"), matching what the resolver normalizes
    # the PATCH payload's name to -- a lowercase fixture name here would
    # dedupe to a *different* software row than the PATCH resolves, and
    # the collision this test wants would never fire.
    gaussian16 = make_software_release(db_session, name="Gaussian", version="16")

    # Same kind/name/lot/version/units/literature/workflow_tool_release as
    # ``target`` below -- differs only by already carrying the release
    # ``target``'s PATCH is about to set.
    make_energy_correction_scheme(
        db_session,
        name="shared_identity",
        lot=lot,
        software_release=gaussian16,
    )
    target = make_energy_correction_scheme(
        db_session,
        name="shared_identity",
        lot=lot,
        software_release=None,
    )
    login_as(_api_admin_user)

    resp = client.patch(
        _url(target.public_ref),
        json={"software": {"name": "Gaussian", "version": "16"}},
    )

    # Not just *a* 409: the specific identity-conflict code, which this
    # route can only reach by way of a failed ``session.flush()`` against
    # the real unique index (``admin.py``'s except-IntegrityError branch,
    # distinct from the already-set guard's 409, which never attempts a
    # write at all). Postgres never applies a statement that raises
    # ``IntegrityError``, so this response is itself the proof the write
    # did not happen -- there is no code path that both raises the
    # exception this branch catches and commits the row it was raised on.
    assert resp.status_code == 409, resp.text
    assert "energy_correction_scheme_identity_conflict" in resp.json()["detail"]

    # The failed flush leaves the shared per-test session's transaction in
    # a state that requires an explicit rollback before further use (see
    # ``conftest.py``'s ``client`` fixture: ``join_transaction_mode=
    # "create_savepoint"``). That rollback recovers the connection to the
    # pytest fixture's outer savepoint -- which also discards this test's
    # own fixtures -- so this test does not attempt any further ORM use of
    # ``db_session`` afterward; the outer fixture teardown closes it.


def test_composite_version_is_normalised_and_the_admin_is_told(
    client, db_session, login_as, _api_admin_user
):
    """Widening ``software`` to ``SoftwareReleaseRef`` brought
    ``normalize_composite_version`` onto this route -- a validator
    ``SoftwareRef`` never had, which *rewrites* what the admin sent.

    Splitting "Gaussian 16, Revision C.02" into ``version``/``revision``
    is the right thing to do. Doing it silently on the route whose whole
    purpose is *correcting* provenance is not: an admin who cannot see
    that their input was reshaped cannot tell whether it was reshaped
    correctly. The upload path has surfaced this since it gained the
    validator; this route now gives the same answer to the same input.

    *Mutation*: stop populating ``warnings`` in the response -- the
    warning assertions must fail while the split assertions still pass,
    which is precisely the silent-rewrite state this pins against.
    """
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={
            "software": {
                "name": "Gaussian",
                "version": "Gaussian 16, Revision C.02",
            }
        },
    )

    assert resp.status_code == 200, resp.text

    db_session.refresh(scheme)
    assert scheme.software_release.version == "16"
    assert scheme.software_release.revision == "C.02"

    warnings = resp.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "software_release_version_is_composite"
    assert warnings[0]["field"] == "software.version"


def test_name_not_matching_version_is_recorded_verbatim_and_flagged(
    client, db_session, login_as, _api_admin_user
):
    """The worse half of the same validator: a ``name``/``version`` pair
    naming two different programs.

    Nothing is rewritten here -- the validator refuses to choose between
    them, which is correct, because guessing would manufacture a release
    that never ran. But that leaves a self-contradictory release row
    stored, and without the warning the admin has no signal at all that
    they recorded ORCA's version against Gaussian's name.
    """
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={"software": {"name": "Gaussian", "version": "ORCA 6.0.0"}},
    )

    assert resp.status_code == 200, resp.text

    db_session.refresh(scheme)
    # Left exactly as declared -- never silently "corrected" to ORCA.
    assert scheme.software_release.software.name == "Gaussian"
    assert scheme.software_release.version == "ORCA 6.0.0"

    warnings = resp.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "software_release_name_looks_wrong"


def test_clean_input_carries_no_warnings(
    client, db_session, login_as, _api_admin_user
):
    """A warnings list that is never empty is decoration.

    *Mutation*: return a constant warning -- this must fail.
    """
    scheme = make_energy_correction_scheme(db_session)
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={"software": {"name": "Gaussian", "version": "16"}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []


def test_attach_provenance_refuses_to_overwrite_existing_workflow_tool_release(
    client, db_session, login_as, _api_admin_user
):
    """The third fill-only guard, which had no test at all.

    Review of #461 removed this guard and all 13 tests stayed green:
    ``energy_correction_scheme_workflow_tool_release_already_set`` was
    registered in the code catalogue and pinned nowhere. The plan claims
    all three guards are unchanged; two of the three could prove it.
    Pre-existing gap, closed here because this PR is what makes the
    claim.

    *Mutation*: remove the ``workflow_tool_release_id is not None`` guard
    in ``admin.py`` -- this must fail.
    """
    arc_110 = make_workflow_tool_release(db_session, name="arc", version="1.1.0")
    scheme = make_energy_correction_scheme(
        db_session, workflow_tool_release=arc_110
    )
    login_as(_api_admin_user)

    resp = client.patch(
        _url(scheme.public_ref),
        json={"workflow_tool_release": {"name": "arc", "version": "9.9.9"}},
    )

    assert resp.status_code == 409, resp.text
    assert "workflow_tool_release_already_set" in resp.json()["detail"]

    db_session.refresh(scheme)
    assert scheme.workflow_tool_release_id == arc_110.id
    assert scheme.workflow_tool_release.version == "1.1.0"
