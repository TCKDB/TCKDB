"""API tests for ``GET /api/v1/scientific/geometries/{geometry_handle}``."""

from __future__ import annotations

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.geometry import GeometryAtom
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.auth import create_api_key, create_session, revoke_api_key
from tests.services.scientific_read._factories import (
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed_geometry(db_session):
    geom = make_geometry(db_session, natoms=3)
    for idx, (sym, x, y, z) in enumerate(
        [("O", 0.0, 0.0, 0.0), ("H", 0.0, 0.76, 0.58), ("H", 0.0, -0.76, 0.58)],
        start=1,
    ):
        db_session.add(
            GeometryAtom(
                geometry_id=geom.id,
                atom_index=idx,
                element=sym,
                x=x,
                y=y,
                z=z,
            )
        )
    db_session.flush()
    return geom


# ---------------------------------------------------------------------------
# Happy path + path-handle inputs
# ---------------------------------------------------------------------------


def test_get_geometry_by_ref_returns_symbols_and_coords(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry_ref"] == geom.public_ref
    assert body["natoms"] == 3
    assert body["format"] == "cartesian"
    assert body["coordinate_units"] == "angstrom"
    assert body["symbols"] == ["O", "H", "H"]
    assert body["coords"][0] == [0.0, 0.0, 0.0]
    assert body["geom_hash"] == geom.geom_hash
    # Phase D default: integer geometry_id is hidden.
    assert "geometry_id" not in body


def test_get_geometry_by_integer_id_still_works(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.id}")
    assert resp.status_code == 200
    body = resp.json()
    # The integer-path input is honored; the response identifies the row by ref.
    assert body["geometry_ref"] == geom.public_ref


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_get_geometry_unknown_ref_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/geometries/geom_neverexistsabcdefxyzqr"
    )
    assert resp.status_code == 404
    assert "geometry not found" in resp.text


def test_get_geometry_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/geometries/spe_abcdef0123456789"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_get_geometry_malformed_handle_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/geometries/not-a-handle")
    assert resp.status_code == 422


def test_get_geometry_unknown_include_token_returns_422(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Phase D internal-ID visibility
# ---------------------------------------------------------------------------


def test_get_geometry_default_omits_geometry_id(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    body = resp.json()
    assert "geometry_id" not in body
    # request.include echoes empty (no opt-in) and the body keeps the ref.
    assert body["request"]["include"] == []


def test_get_geometry_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The token is silently dropped — it doesn't appear in the echo and
    # the ID stays hidden.
    assert "internal_ids" not in body["request"]["include"]
    assert "geometry_id" not in body


def test_get_geometry_include_internal_ids_restores_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "internal_ids" in body["request"]["include"]
    assert body["geometry_id"] == geom.id


def test_get_geometry_include_all_does_not_restore_ids(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=all"
    )
    body = resp.json()
    # ``all`` does not expand to ``internal_ids``.
    assert "internal_ids" not in body["request"]["include"]
    assert "geometry_id" not in body


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_get_geometry_provenance_lists_producers_and_consumers(
    client, db_session
):
    geom = _seed_geometry(db_session)
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("GP_API")
    )
    entry = make_species_entry(db_session, species)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=opt_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=sp_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.flush()

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    prov = resp.json()["provenance"]

    assert len(prov["produced_by"]) == 1
    assert prov["produced_by"][0]["calculation_ref"] == opt_calc.public_ref
    assert prov["produced_by"][0]["role"] == "final"
    assert prov["produced_by"][0]["calculation_type"] == "opt"

    assert len(prov["used_as_input_by"]) == 1
    assert (
        prov["used_as_input_by"][0]["calculation_ref"] == sp_calc.public_ref
    )
    assert prov["used_as_input_by"][0]["role"] is None
    # Phase D: integer calculation_id stripped by default in the
    # provenance sub-block too.
    assert "calculation_id" not in prov["produced_by"][0]
    assert "calculation_id" not in prov["used_as_input_by"][0]


# ---------------------------------------------------------------------------
# Molecular identity
# ---------------------------------------------------------------------------


def _attach_output(db_session, *, calculation, geometry):
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calculation.id,
            geometry_id=geometry.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()


def test_get_geometry_identity_species_owned(client, db_session):
    species = make_species(
        db_session, smiles="O=C=O", inchi_key=next_inchi_key("API_ID1")
    )
    entry = make_species_entry(db_session, species)
    geom = _seed_geometry(db_session)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_output(db_session, calculation=opt_calc, geometry=geom)

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    identity = resp.json()["identity"]
    assert identity is not None
    assert identity["kind"] == "species_entry"
    assert identity["transition_state_entry"] is None
    se = identity["species_entry"]
    assert se["species_ref"] == species.public_ref
    assert se["species_entry_ref"] == entry.public_ref
    assert se["canonical_smiles"] == "O=C=O"
    assert se["formula"] == "CO2"
    assert se["charge"] == 0
    assert se["multiplicity"] == 1
    # Phase D default: ids stripped.
    assert "species_id" not in se
    assert "species_entry_id" not in se


def test_get_geometry_identity_null_when_no_owner(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    assert resp.json()["identity"] is None


# ---------------------------------------------------------------------------
# Auth-gated submission_ref
# ---------------------------------------------------------------------------


def _seed_geometry_with_submission(db_session, *, created_by: int):
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("API_SUB")
    )
    entry = make_species_entry(db_session, species)
    geom = _seed_geometry(db_session)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_output(db_session, calculation=opt_calc, geometry=geom)
    submission = Submission(
        created_by=created_by,
        submission_kind=SubmissionKind.conformer,
        source_kind=SubmissionSourceKind.api,
        status=SubmissionStatus.pending,
    )
    db_session.add(submission)
    db_session.flush()
    db_session.add(
        SubmissionRecordLink(
            submission_id=submission.id,
            record_type=SubmissionRecordType.calculation,
            record_id=opt_calc.id,
        )
    )
    db_session.flush()
    return geom, submission


def test_get_geometry_submission_ref_omitted_for_anonymous_caller(
    client, db_session, _api_test_user
):
    """No credential on the request -> the key is absent, not null.

    ``client`` pre-authenticates ``get_current_user`` for other routes,
    but this route depends on ``get_optional_current_user`` instead,
    which is not overridden — a plain request with no ``X-API-Key``
    header and no session cookie really does resolve to an anonymous
    caller here.
    """
    geom, submission = _seed_geometry_with_submission(
        db_session, created_by=_api_test_user
    )
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    body = resp.json()
    assert "submission_ref" not in body
    assert "submission_id" not in body


def test_get_geometry_submission_ref_present_for_api_key_caller(
    client, db_session, _api_test_user
):
    geom, submission = _seed_geometry_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    _, raw_key = create_api_key(db_session, user)

    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}",
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["submission_ref"] == submission.public_ref


def test_get_geometry_submission_ref_present_for_session_cookie_caller(
    client, db_session, _api_test_user
):
    geom, submission = _seed_geometry_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    _, raw_token = create_session(db_session, user)

    client.cookies.set("tckdb_session", raw_token)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["submission_ref"] == submission.public_ref


def test_get_geometry_invalid_api_key_returns_401_not_anonymous(
    client, db_session, _api_test_user
):
    """An invalid credential must 401, never silently fall back to anonymous."""
    geom, _submission = _seed_geometry_with_submission(
        db_session, created_by=_api_test_user
    )
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}",
        headers={"X-API-Key": "not-a-real-key"},
    )
    assert resp.status_code == 401


def test_get_geometry_revoked_api_key_returns_401_not_anonymous(
    client, db_session, _api_test_user
):
    """A revoked key must 401 -- treating it as anonymous would turn a
    revocation into a silent downgrade instead of a refusal."""
    geom, _submission = _seed_geometry_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    key_row, raw_key = create_api_key(db_session, user)
    revoke_api_key(db_session, key_row)

    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}",
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 401
