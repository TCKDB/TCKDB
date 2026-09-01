"""Auth-gated ``submission_ref`` on ``GET /api/v1/scientific/calculations/{ref}``.

``record.provenance.submission_ref`` used to be served unconditionally —
the only thing hiding the linkage was the separate Phase D internal-id
policy, which only ever covered ``submission_id`` (an integer, matched
by the ``_id`` suffix rule), never ``submission_ref`` (a string, matched
by nothing). This file pins the fix: the ref is now visible only to a
caller who authenticates, via ``get_optional_current_user``
(``app/api/deps.py``) and ``apply_scientific_read_visibility``
(``app/services/scientific_read/auth_visibility.py``).

The geometry-detail endpoint gained the identical gate at the same
time; its dedicated coverage (including the identity block) lives in
``test_api_geometry_read.py``. This file exercises the same auth
dependency from the calculation side so both call sites of
``get_optional_current_user`` are pinned independently.
"""

from __future__ import annotations

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.auth import create_api_key, create_session, revoke_api_key
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed_calc_with_submission(db_session, *, created_by: int) -> tuple[Calculation, Submission]:
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("CALCSUB")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
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
            record_id=calc.id,
        )
    )
    db_session.flush()
    return calc, submission


def test_calculation_detail_submission_ref_omitted_for_anonymous_caller(
    client, db_session, _api_test_user
):
    calc, _submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 200
    provenance = resp.json()["record"]["provenance"]
    assert "submission_ref" not in provenance


def test_calculation_detail_submission_ref_present_for_api_key_caller(
    client, db_session, _api_test_user
):
    calc, submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    _, raw_key = create_api_key(db_session, user)

    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}",
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 200
    provenance = resp.json()["record"]["provenance"]
    assert provenance["submission_ref"] == submission.public_ref


def test_calculation_detail_submission_ref_present_for_session_cookie_caller(
    client, db_session, _api_test_user
):
    calc, submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    _, raw_token = create_session(db_session, user)

    client.cookies.set("tckdb_session", raw_token)
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 200
    provenance = resp.json()["record"]["provenance"]
    assert provenance["submission_ref"] == submission.public_ref


def test_calculation_detail_invalid_api_key_returns_401_not_anonymous(
    client, db_session, _api_test_user
):
    calc, _submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}",
        headers={"X-API-Key": "definitely-not-a-real-key"},
    )
    assert resp.status_code == 401


def test_calculation_detail_revoked_api_key_returns_401_not_anonymous(
    client, db_session, _api_test_user
):
    """A revoked key must 401, not silently fall through to anonymous.

    Falling through would turn a de-authorization into a downgrade: the
    caller keeps reading whatever the public already sees, under the
    cover of a key that was supposed to stop working.
    """
    calc, _submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    user = db_session.get(AppUser, _api_test_user)
    key_row, raw_key = create_api_key(db_session, user)
    revoke_api_key(db_session, key_row)

    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}",
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 401


def test_calculation_detail_invalid_session_cookie_returns_401_not_anonymous(
    client, db_session, _api_test_user
):
    calc, _submission = _seed_calc_with_submission(
        db_session, created_by=_api_test_user
    )
    client.cookies.set("tckdb_session", "not-a-real-session-token")
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 401
