"""API-boundary coverage for the composite ``software_release.version`` guard.

Issue #305 originally refused a ``version`` that embedded a parsed ESS
banner. Refusing broke real ARC ingestion: five real payloads carry
``name='gaussian', version='ORCA 6.0.0'`` (see
``test_api_arc_run_fixtures.py::test_arc_run_payload_uploads_cleanly``),
and the guard 422'd every one of them. The owner's call: warn and
normalise, never refuse.

The normalisation itself lives on ``tckdb_schemas.fragments.refs.
SoftwareReleaseRef`` (a request body model) and is unit-tested precisely
in ``schemas/python/tckdb-schemas/tests/test_software_release_ref_version.py``.
This file asserts the same behaviour survives a real upload route: the
request never gets refused, the response carries the warning at the
correct dot-path, and the persisted row reflects the (normalised or
untouched, depending on the case) values -- proving the wire-package
change and the route wiring (``collect_software_release_version_warnings``
in ``app/api/routes/uploads.py``) actually compose.
"""

from __future__ import annotations

from app.db.models.calculation import Calculation
from app.db.models.software import SoftwareRelease


def _hydrogen_conformer_payload(*, software_release: dict) -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": software_release,
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "conf-a",
        "note": "test upload",
    }


def _released_software(db_session, calculation_id: int) -> SoftwareRelease:
    calc = db_session.get(Calculation, calculation_id)
    assert calc is not None and calc.software_release_id is not None
    release = db_session.get(SoftwareRelease, calc.software_release_id)
    assert release is not None
    return release


def test_gaussian_banner_uploads_cleanly_and_normalises(client, db_session):
    """The live 206x ``gaussian``/``"Gaussian 09, Revision D.01"`` pair.

    Must upload cleanly (never refused) and normalise: the leading
    package name is stripped and the trailing revision label is split
    out.
    """
    payload = _hydrogen_conformer_payload(
        software_release={"name": "gaussian", "version": "Gaussian 09, Revision D.01"}
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    warnings = body["warnings"]
    matching = [
        w for w in warnings if w["field"] == "calculation.software_release.version"
    ]
    assert len(matching) == 1, warnings
    assert matching[0]["code"] == "software_release_version_is_composite"

    release = _released_software(
        db_session, body["primary_calculation"]["calculation_id"]
    )
    assert release.version == "09"
    assert release.revision == "D.01"


def test_orca_banner_uploads_cleanly_and_normalises(client, db_session):
    """The live 54x ``orca``/``"ORCA 6.0.0"`` pair: strip to a bare version."""
    payload = _hydrogen_conformer_payload(
        software_release={"name": "orca", "version": "ORCA 6.0.0"}
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    matching = [
        w
        for w in body["warnings"]
        if w["field"] == "calculation.software_release.version"
    ]
    assert len(matching) == 1, body["warnings"]
    assert matching[0]["code"] == "software_release_version_is_composite"

    release = _released_software(
        db_session, body["primary_calculation"]["calculation_id"]
    )
    assert release.version == "6.0.0"
    assert release.revision is None


def test_mismatched_name_uploads_cleanly_and_is_left_completely_untouched(
    client, db_session
):
    """The live 5x ``gaussian``/``"ORCA 6.0.0"`` pair -- a producer bug
    where a stale ``name`` rode along with a really-observed ORCA
    version. Must upload cleanly, must NOT be normalised (the assertion
    that catches an over-eager normaliser), and must warn that the name
    looks wrong under its own, distinct code.
    """
    payload = _hydrogen_conformer_payload(
        software_release={"name": "gaussian", "version": "ORCA 6.0.0"}
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    matching = [
        w
        for w in body["warnings"]
        if w["field"] == "calculation.software_release.version"
    ]
    assert len(matching) == 1, body["warnings"]
    assert matching[0]["code"] == "software_release_name_looks_wrong"
    assert matching[0]["code"] != "software_release_version_is_composite"

    release = _released_software(
        db_session, body["primary_calculation"]["calculation_id"]
    )
    # The version is untouched exactly as declared. name normalizes
    # (lower/alias) at resolution regardless of this guard -- only
    # version/revision/build are this guard's business.
    assert release.version == "ORCA 6.0.0"
    assert release.revision is None


def test_a_clean_version_uploads_with_no_software_release_warning(client):
    """The live 329x ``ARC``/``"1.1.0"`` pair. A clean deposit must stay
    completely silent -- no warning at all for this field."""
    payload = _hydrogen_conformer_payload(
        software_release={"name": "ARC", "version": "1.1.0"}
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    matching = [
        w
        for w in body["warnings"]
        if w["field"] == "calculation.software_release.version"
    ]
    assert matching == []


def test_depositor_supplied_revision_is_never_overwritten_at_the_route(
    client, db_session
):
    """A composite version that would split out its own revision must not
    clobber a revision the depositor already declared. Both fields land
    in the database exactly as sent."""
    payload = _hydrogen_conformer_payload(
        software_release={
            "name": "gaussian",
            "version": "Gaussian 09, Revision D.01",
            "revision": "E.01",
        }
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    matching = [
        w
        for w in body["warnings"]
        if w["field"] == "calculation.software_release.version"
    ]
    assert len(matching) == 1, body["warnings"]
    assert matching[0]["code"] == "software_release_version_is_composite"

    release = _released_software(
        db_session, body["primary_calculation"]["calculation_id"]
    )
    assert release.version == "Gaussian 09, Revision D.01"
    assert release.revision == "E.01"


def test_a_legitimate_version_uploads_cleanly_through_the_same_route(client):
    """A bare, correctly-declared version has always uploaded cleanly and
    still does."""
    payload = _hydrogen_conformer_payload(
        software_release={"name": "Gaussian", "version": "16"}
    )
    resp = client.post("/api/v1/uploads/conformers", json=payload)

    assert resp.status_code == 201, resp.text
