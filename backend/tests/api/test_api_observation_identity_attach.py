"""Tests for the curator/admin observation-identity-attach route.

``POST /api/v1/admin/observations/{ref}/identity`` is the one write path
that fills in ``molecular_property_observation.species_entry_id`` after
deposit -- see ``app/services/observation_identity_attach.py``. Follows the
existing admin-route testing pattern (``test_admin_energy_correction_scheme_
provenance.py``): the ``client`` fixture's default actor is role=user (the
403 path), ``login_as`` swaps roles, and ``anon_client`` exercises the
anonymous 401 path.

Review round 2: the target rule relaxed (any ground-state minimum entry is
a legal target, not only the unique one -- F4) and the curation fact moved
from ``raw_payload_json`` to a ``SubmissionAuditEvent`` (F5), which also
means every successful-attach test now has to link its observation to a
submission first, the way a real deposit does.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    SpeciesEntryStateKind,
    StationaryPointKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.submission import SubmissionAuditEvent
from app.services.submission import create_submission, link_records
from tests.services.scientific_read._factories import (
    make_observation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _url(ref: str) -> str:
    return f"/api/v1/admin/observations/{ref}/identity"


@pytest.fixture
def anon_client(db_session: Session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def _entry(db_session, *, prefix: str = "SEATTACH"):
    species = make_species(db_session, smiles="CCO", inchi_key=next_inchi_key(prefix))
    return species, make_species_entry(db_session, species)


def _link_to_submission(db_session, obs, *, created_by: int):
    """Link ``obs`` to a fresh submission, the way a real deposit does.

    The attach service refuses (``observation_identity_attach_requires_
    submission``) an observation with no such link, since it has nowhere
    honest to record the curation fact.
    """
    submission = create_submission(
        db_session,
        created_by=created_by,
        submission_kind=SubmissionKind.other,
        title="observation-identity-attach test deposit",
    )
    link_records(
        db_session,
        submission=submission,
        records=[
            (SubmissionRecordType.molecular_property_observation, obs.id, None)
        ],
    )
    return submission


def _audit_events(db_session, submission_id: int) -> list[SubmissionAuditEvent]:
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent)
            .where(SubmissionAuditEvent.submission_id == submission_id)
            .order_by(SubmissionAuditEvent.id)
        )
    )


def _attach_events(db_session, submission_id: int) -> list[SubmissionAuditEvent]:
    """Only the ``observation_identity_attached`` events -- ``create_submission``
    itself already logs a ``submission_created`` event on the same submission.
    """
    return [
        event
        for event in _audit_events(db_session, submission_id)
        if event.event_kind == SubmissionAuditEventKind.observation_identity_attached
    ]


def test_attach_requires_auth(anon_client, db_session):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)

    resp = anon_client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 401, resp.text


def test_attach_requires_curator_or_admin(client, db_session):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)

    # Default actor is role=user.
    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 403, resp.text


def test_curator_attaches_an_unresolved_observation(
    client, db_session, login_as, _api_curator_user
):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
    submission = _link_to_submission(db_session, obs, created_by=_api_curator_user)
    raw_payload_before = obs.raw_payload_json
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref),
        json={"species_entry_ref": entry.public_ref, "note": "matched by formula"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["observation_ref"] == obs.public_ref
    assert body["species_entry_ref"] == entry.public_ref

    db_session.refresh(obs)
    assert obs.species_entry_id == entry.id
    # raw_payload_json is provenance (the archive's forensic/round-trip copy
    # of what was deposited) and must stay byte-identical across a later
    # curation act -- the curation fact is recorded elsewhere (below), not
    # by mutating this column.
    assert obs.raw_payload_json == raw_payload_before

    events = _attach_events(db_session, submission.id)
    assert len(events) == 1
    event = events[0]
    assert event.details_json["observation_ref"] == obs.public_ref
    assert event.details_json["species_entry_ref"] == entry.public_ref
    assert event.details_json["actor_username"] == "testcurator"
    assert event.details_json["note"] == "matched by formula"
    assert event.details_json["previous_species_entry_ref"] is None
    assert "id" not in event.details_json


def test_admin_can_also_attach(client, db_session, login_as, _api_admin_user):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
    _link_to_submission(db_session, obs, created_by=_api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 200, resp.text


def test_second_attach_refuses_already_set(
    client, db_session, login_as, _api_curator_user
):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=entry)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_already_set" in resp.text


def test_curator_may_choose_among_ambiguous_entries(
    client, db_session, login_as, _api_curator_user
):
    """Review round 2 (F4): the original ambiguity rule refused precisely
    the isomer-ambiguity case this tool exists for. A species with two
    ground-state minimum entries (a cis/trans pair) is now a legal target
    for either one -- the curator's choice of which entry IS the
    disambiguation a machine resolver could not make.
    """
    species, entry_a = _entry(db_session, prefix="SEAMBIG")
    entry_b = make_species_entry(
        db_session,
        species,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.ground,
        stereo_label="cis",
    )
    obs = make_observation(db_session, species_entry=None)
    submission = _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry_b.public_ref}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["species_entry_ref"] == entry_b.public_ref

    db_session.refresh(obs)
    assert obs.species_entry_id == entry_b.id

    events = _attach_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["species_entry_ref"] == entry_b.public_ref
    # entry_a (the other ground-state minimum entry) was never touched.
    assert entry_a.id != entry_b.id


def test_non_ground_state_target_refuses(
    client, db_session, login_as, _api_curator_user
):
    """Review round 3 (R5): the observation is now linked to a submission
    before the attach attempt, so the only guard standing between this
    request and a 200 is the target-validity rule -- previously, with no
    submission linked, a mutation that neutralized the target rule would
    still redden this test via the unrelated ``observation_identity_
    attach_requires_submission`` guard (which happens to run right after
    the target check), making the assertion's redness a coincidence rather
    than proof the target rule fired. With the submission linked, that
    mutation now lets the request actually succeed (200), which the
    assertions below catch directly.
    """
    species, ground_entry = _entry(db_session, prefix="SEEXCITED")
    excited_entry = make_species_entry(
        db_session,
        species,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.excited,
        electronic_state_label="A",
    )
    obs = make_observation(db_session, species_entry=None)
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": excited_entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_target_not_ground_state_minimum" in resp.text


def test_single_excited_state_entry_species_refuses(
    client, db_session, login_as, _api_curator_user
):
    """F7: a species with exactly one entry, and that entry excited-state,
    used to slip past the old (pre-F4) ambiguity guard's deleted-clause
    mutation because every other fixture species had two entries. This
    pins the target-validity check on a single-entry species directly.

    Review round 3 (R5): the observation is now linked to a submission
    first, for the same reason as ``test_non_ground_state_target_refuses``
    above -- without it, this test's redness under the
    ``electronic_state_kind == ground`` mutation came from the unrelated
    ``observation_identity_attach_requires_submission`` guard, not from the
    target rule itself.
    """
    species = make_species(
        db_session, smiles="CC=O", inchi_key=next_inchi_key("SESINGLEEXC")
    )
    excited_entry = make_species_entry(
        db_session,
        species,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.excited,
        electronic_state_label="A",
    )
    obs = make_observation(db_session, species_entry=None)
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": excited_entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_target_not_ground_state_minimum" in resp.text


def test_attach_refuses_an_observation_linked_to_no_submission(
    client, db_session, login_as, _api_curator_user
):
    """F5: no submission link means nowhere honest to record the curation
    fact, so the attach is refused rather than silently proceeding without
    an audit trail.
    """
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_attach_requires_submission" in resp.text

    db_session.refresh(obs)
    assert obs.species_entry_id is None


# ---------------------------------------------------------------------------
# Identity-hint conflict (Probe C, Phase C-E5 review round 3)
# ---------------------------------------------------------------------------


def test_hint_same_connectivity_different_stereo_accepts(
    client, db_session, login_as, _api_curator_user
):
    """A source InChIKey with no stereo resolution of its own (or a
    different stereo block than the target's) must still be attachable to
    a stereo-specific entry -- that is the isomer-disambiguation case this
    tool exists for. Only the connectivity block (before the first hyphen)
    is compared.
    """
    species = make_species(
        db_session,
        smiles="CCO",
        inchi_key="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    )
    entry = make_species_entry(db_session, species)
    obs = make_observation(
        db_session,
        species_entry=None,
        raw_payload_json={
            "identity_hint": {"inchikey": "LFQSCWFLJHTTHZ-DIFFRSTEREO-N"}
        },
    )
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(obs)
    assert obs.species_entry_id == entry.id


def test_hint_different_skeleton_refuses(
    client, db_session, login_as, _api_curator_user
):
    """The observation's own identity hint names a different molecular
    connectivity than the target species -- attaching it would silently
    associate an observation with the wrong molecule.
    """
    species = make_species(
        db_session,
        smiles="CCO",
        inchi_key="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    )
    entry = make_species_entry(db_session, species)
    obs = make_observation(
        db_session,
        species_entry=None,
        raw_payload_json={
            "identity_hint": {"inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"}
        },
    )
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_hint_conflict" in resp.text

    db_session.refresh(obs)
    assert obs.species_entry_id is None


def test_no_hint_accepts(client, db_session, login_as, _api_curator_user):
    """An observation with no usable identity_hint.inchikey is unaffected
    by the conflict check -- there is nothing to compare."""
    _, entry = _entry(db_session, prefix="SENOHINT")
    obs = make_observation(db_session, species_entry=None, raw_payload_json=None)
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 200, resp.text


def test_404_for_unknown_observation(client, db_session, login_as, _api_curator_user):
    _, entry = _entry(db_session)
    login_as(_api_curator_user)

    resp = client.post(
        _url("mpo_doesnotexist000000000"),
        json={"species_entry_ref": entry.public_ref},
    )
    assert resp.status_code == 404, resp.text


def test_404_for_unknown_species_entry(
    client, db_session, login_as, _api_curator_user
):
    obs = make_observation(db_session, species_entry=None)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref),
        json={"species_entry_ref": "spe_doesnotexist0000000000"},
    )
    assert resp.status_code == 404, resp.text


def test_attach_never_creates_a_species(client, db_session, login_as, _api_curator_user):
    from app.db.models.species import Species, SpeciesEntry

    before_species = db_session.scalar(select(func.count()).select_from(Species))
    before_entries = db_session.scalar(select(func.count()).select_from(SpeciesEntry))

    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
    _link_to_submission(db_session, obs, created_by=_api_curator_user)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry.public_ref}
    )
    assert resp.status_code == 200, resp.text

    after_species = db_session.scalar(select(func.count()).select_from(Species))
    after_entries = db_session.scalar(select(func.count()).select_from(SpeciesEntry))
    # Exactly the one species + one entry the test itself created via
    # _entry(); the attach call created none of its own.
    assert after_species == before_species + 1
    assert after_entries == before_entries + 1
