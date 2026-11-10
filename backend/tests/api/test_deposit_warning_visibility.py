"""Who sees what of a deposit warning, and what a shared cache may keep.

A warning's stored text is the depositor's own words. It goes to whoever wrote the row (the submission's depositor for a
deposit's rows, the uploader for an artifact's) and to curators and admins, and to nobody else. A withheld record's
submission contributes nothing under the curated profile. These tests use two depositors on one record, so a wrong
owner, a lost filter or a cross-type id collision shows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionKind
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning
from app.db.models.network import Network
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.auth import create_api_key, create_session, revoke_session
from app.services.deposit_warnings import content_hash
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.submission import create_submission, link_record
from tests.api import test_api_artifact_charge_multiplicity_hook as hook
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_ts_contract_621 import _bundle
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

BASE = "/api/v1/scientific"
CODE = "sp_energy_payload_log_mismatch"


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _user(session, name: str, role: AppUserRole = AppUserRole.user) -> tuple[AppUser, str]:
    user = AppUser(username=name, role=role)
    session.add(user)
    session.flush()
    _, key = create_api_key(session, user)
    return user, key


def _calc(session) -> Calculation:
    species = make_species(session, inchi_key=next_inchi_key("DWVIS"))
    return make_calculation(session, species_entry_id=make_species_entry(session, species).id)


def _submission(session, user: AppUser) -> Submission:
    return create_submission(session, created_by=user.id, submission_kind=SubmissionKind.thermo)


def _warning(session, submission: Submission | None, writer: AppUser, message: str, *, record=None) -> None:
    session.add(
        DepositWarning(
            submission_id=submission.id if submission else None,
            record_type=record[0] if record else None,
            record_id=record[1] if record else None,
            written_by_user_id=writer.id,
            code=CODE,
            field="f",
            message=message,
            content_hash=content_hash(CODE, "f", message, None),
            check_version=1,
        )
    )
    session.flush()


def _items(read: dict) -> list[dict]:
    return [i for b in read["record_warnings"] for i in b["items"]] + [
        i for b in read["deposit_warnings"] for i in b["items"]
    ]


def _depositor_messages(read: dict) -> set[str]:
    return {i["depositor_message"] for i in _items(read) if "depositor_message" in i}


# ---------------------------------------------------------------------------
# two depositors, one record
# ---------------------------------------------------------------------------


def _two_depositors(session):
    alice, alice_key = _user(session, "dw-alice")
    bob, bob_key = _user(session, "dw-bob")
    _, curator_key = _user(session, "dw-vis-curator", AppUserRole.curator)
    calc = _calc(session)
    for person, tag in ((alice, "alice"), (bob, "bob")):
        submission = _submission(session, person)
        link_record(session, submission=submission, record_type=RT.calculation, record_id=calc.id)
        _warning(session, submission, person, f"{tag}-record", record=(RT.calculation, calc.id))
        _warning(session, submission, person, f"{tag}-deposit")
    return calc, alice_key, bob_key, curator_key


def _read_calc(client, calc, key=None) -> dict:
    headers = {"X-API-Key": key} if key else {}
    response = client.get(f"{BASE}/calculations/{calc.public_ref}", headers=headers)
    assert response.status_code == 200, response.text[:300]
    return response.json()


def test_each_depositor_sees_only_their_own_text_and_a_curator_sees_both(client, db_session):
    calc, alice_key, bob_key, curator_key = _two_depositors(db_session)
    anonymous = _read_calc(client, calc)
    assert len(_items(anonymous)) == 4 and _depositor_messages(anonymous) == set()
    assert _depositor_messages(_read_calc(client, calc, alice_key)) == {"alice-record", "alice-deposit"}
    assert _depositor_messages(_read_calc(client, calc, bob_key)) == {"bob-record", "bob-deposit"}
    assert _depositor_messages(_read_calc(client, calc, curator_key)) == {
        "alice-record",
        "alice-deposit",
        "bob-record",
        "bob-deposit",
    }
    # Public text is the same for everyone.
    assert {i["message"] for i in _items(_read_calc(client, calc, alice_key))} == {
        i["message"] for i in _items(anonymous)
    }


# ---------------------------------------------------------------------------
# the submission link lookup is by record type
# ---------------------------------------------------------------------------


def test_a_submission_linked_to_another_record_type_with_the_same_id_is_not_ours(client, db_session):
    alice, _ = _user(db_session, "dw-collide")
    calc = _calc(db_session)
    submission = _submission(db_session, alice)
    # The same number, a different table: this submission produced a thermo with the calculation's id, nothing else.
    link_record(db_session, submission=submission, record_type=RT.thermo, record_id=calc.id)
    _warning(db_session, submission, alice, "belongs to the thermo's submission")
    read = _read_calc(client, calc)
    assert read["deposit_warnings"] == []


# ---------------------------------------------------------------------------
# a real deposit by a second user is written by that user
# ---------------------------------------------------------------------------


def test_a_real_deposit_by_a_second_user_is_written_by_that_user_and_shown_to_them_alone(client, db_session, login_as):
    bob, bob_key = _user(db_session, "dw-real-bob")
    other, other_key = _user(db_session, "dw-real-other")
    login_as(bob.id)
    response = client.post("/api/v1/uploads/conformers", json=hook._conformer_payload(smiles="C", charge=0, multiplicity=1))
    assert response.status_code == 201, response.text[:300]
    body = response.json()
    assert body["warnings"], "the deposit must earn a warning for this to prove anything"

    rows = db_session.scalars(select(DepositWarning).where(DepositWarning.submission_id == body["submission_id"])).all()
    assert rows and {r.written_by_user_id for r in rows} == {bob.id}, "markers and warnings are written by the depositor"
    assert db_session.get(Submission, body["submission_id"]).created_by == bob.id

    calc_ref = body["primary_calculation"]["calculation_ref"]
    calc = db_session.scalars(select(Calculation).where(Calculation.public_ref == calc_ref)).one()
    # The reader is checked per request, so read as each user with their own key (the harness ignores the header for
    # deposits, not for these optional-credential reads).
    assert _depositor_messages(_read_calc(client, calc, bob_key))
    assert _depositor_messages(_read_calc(client, calc, other_key)) == set()
    assert _depositor_messages(_read_calc(client, calc)) == set()


# ---------------------------------------------------------------------------
# a withheld record contributes nothing under the curated profile
# ---------------------------------------------------------------------------


def test_a_withheld_entrys_submission_shows_no_deposit_warnings_under_curated(client, db_session):
    alice, alice_key = _user(db_session, "dw-ts-alice")
    bob, bob_key = _user(db_session, "dw-ts-bob")
    curator, _ = _user(db_session, "dw-ts-curator", AppUserRole.curator)

    reactant = make_species(db_session, inchi_key=next_inchi_key("DWTSR"))
    product = make_species(db_session, inchi_key=next_inchi_key("DWTSP"))
    reaction = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    ts = make_transition_state(db_session, reaction_entry=reaction_entry)
    shown = make_transition_state_entry(db_session, transition_state=ts)
    withheld = make_transition_state_entry(db_session, transition_state=ts)
    ensure_record_review(db_session, record_type=RT.transition_state_entry, record_id=withheld.id)
    set_record_review_status(
        db_session,
        record_type=RT.transition_state_entry,
        record_id=withheld.id,
        status=RecordReviewStatus.rejected,
        actor=curator,
        note="x",
    )
    for person, entry, tag in ((alice, shown, "shown"), (bob, withheld, "withheld")):
        submission = _submission(db_session, person)
        link_record(db_session, submission=submission, record_type=RT.transition_state_entry, record_id=entry.id)
        _warning(db_session, submission, person, f"{tag}-deposit")

    concept = db_session.get(TransitionState, ts.id)
    url = f"{BASE}/transition-states/{concept.public_ref}"
    for key in (None, alice_key):
        headers = {"X-API-Key": key} if key else {}
        curated = client.get(url, params={"profile": "curated"}, headers=headers)
        assert curated.status_code == 200, curated.text[:300]
        body = curated.json()
        (block,) = body["deposit_warnings"]
        assert len(block["items"]) == 1
        # Exploratory shows both submissions' blocks, as before: the guard acts only under curated.
    exploratory = client.get(url, headers={"X-API-Key": alice_key}).json()
    assert len(exploratory["deposit_warnings"]) == 2
    assert withheld.public_ref not in str(client.get(url, params={"profile": "curated"}).json())


# ---------------------------------------------------------------------------
# artifacts: the raw text goes to whoever wrote it
# ---------------------------------------------------------------------------


def test_a_curators_artifact_warning_text_is_the_curators_not_the_depositors(client, db_session, login_as):
    calc_id = hook._create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
    calc = db_session.get(Calculation, calc_id)
    depositor = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    _, depositor_key = _user(db_session, "dw-art-unused")  # a signed-in user who owns nothing
    # ``testuser`` deposited it; a curator now attaches an artifact to that deposit.
    curator, curator_key = _user(db_session, "dw-art-curator", AppUserRole.curator)
    owner_key = create_api_key(db_session, depositor)[1]
    login_as(curator.id)
    response = client.post(artifacts_url(client, calc_id), json={"artifacts": [hook._output_log()]})
    assert response.status_code == 201, response.text[:400]
    assert "multiplicity_mismatch" in {w["code"] for w in response.json()["warnings"]}
    row = db_session.scalars(
        select(DepositWarning).where(
            DepositWarning.record_type == RT.calculation,
            DepositWarning.record_id == calc_id,
            DepositWarning.code == "multiplicity_mismatch",
        )
    ).one()
    assert row.written_by_user_id == curator.id

    def artifact_item(read):
        return next(i for i in _items(read) if i["code"] == "multiplicity_mismatch")

    # The depositor sees the warning, in TCKDB's words, and not the curator's raw text.
    assert "depositor_message" not in artifact_item(_read_calc(client, calc, owner_key))
    assert "depositor_message" in artifact_item(_read_calc(client, calc, curator_key))
    assert "depositor_message" not in artifact_item(_read_calc(client, calc, depositor_key))
    assert "depositor_message" not in artifact_item(_read_calc(client, calc))


def test_an_artifact_warning_with_no_submission_is_the_uploaders_and_curators_alone(client, db_session):
    calc_id = hook._create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
    calc = db_session.get(Calculation, calc_id)
    uploader = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    submission = db_session.scalars(
        select(Submission)
        .join(SubmissionRecordLink, SubmissionRecordLink.submission_id == Submission.id)
        .where(SubmissionRecordLink.record_id == calc_id, SubmissionRecordLink.record_type == RT.calculation)
    ).first()
    from app.db.models.common import SubmissionStatus

    submission.status = SubmissionStatus.failed
    db_session.flush()
    uploader_key = create_api_key(db_session, uploader)[1]
    _, other_key = _user(db_session, "dw-nosub-other")
    _, curator_key = _user(db_session, "dw-nosub-curator", AppUserRole.curator)

    response = client.post(artifacts_url(client, calc_id), json={"artifacts": [hook._output_log()]})
    assert response.status_code == 201, response.text[:300]
    row = db_session.scalars(
        select(DepositWarning).where(
            DepositWarning.record_type == RT.calculation,
            DepositWarning.record_id == calc_id,
            DepositWarning.code == "multiplicity_mismatch",
        )
    ).one()
    assert row.submission_id is None and row.written_by_user_id == uploader.id

    def has_text(key):
        read = _read_calc(client, calc, key)
        return "depositor_message" in next(i for i in _items(read) if i["code"] == "multiplicity_mismatch")

    assert has_text(uploader_key) and has_text(curator_key)
    assert not has_text(other_key) and not has_text(None)


# ---------------------------------------------------------------------------
# a credential-dependent answer is private
# ---------------------------------------------------------------------------


def _deposit_calc(client, db_session):
    calc = db_session.get(Calculation, hook._create_calc(client, smiles="C", charge=0, multiplicity=1))
    return f"{BASE}/calculations/{calc.public_ref}"


def _deposit_bundle(client, db_session, which):
    body = client.post("/api/v1/uploads/computed-reaction", json=_bundle([])).json()
    tse = db_session.get(TransitionStateEntry, body["transition_state_entry_id"])
    if which == "tse":
        return f"{BASE}/transition-state-entries/{tse.public_ref}"
    if which == "concept":
        return f"{BASE}/transition-states/{db_session.get(TransitionState, tse.transition_state_id).public_ref}"
    if which == "kinetics":
        from app.db.models.reaction import ReactionEntry

        return f"{BASE}/reaction-entries/{db_session.get(ReactionEntry, body['reaction_entry_id']).public_ref}/kinetics"
    thermo = db_session.get(Thermo, body["thermo_ids"][0])
    return f"{BASE}/species-entries/{thermo.species_entry.public_ref}/thermo"


def _deposit_statmech(client, db_session):
    from tests.api.test_api_statmech_upload import _statmech_payload

    response = client.post("/api/v1/uploads/statmech", json=_statmech_payload())
    assert response.status_code == 201, response.text[:300]
    from app.db.models.statmech import Statmech

    return f"{BASE}/statmech/{db_session.get(Statmech, response.json()['id']).public_ref}"


def _deposit_transport(client, db_session):
    from app.db.models.transport import Transport
    from tests.api.test_api_transport_upload import _transport_payload

    response = client.post("/api/v1/uploads/transport", json=_transport_payload())
    assert response.status_code == 201, response.text[:300]
    return f"{BASE}/transport/{db_session.get(Transport, response.json()['id']).public_ref}"


def _deposit_network(client, db_session):
    response = client.post("/api/v1/uploads/networks", json={"name": "dw cache network"})
    assert response.status_code == 201, response.text[:300]
    return f"{BASE}/networks/{db_session.get(Network, response.json()['id']).public_ref}"


def _deposit_solve(client, db_session):
    from app.db.models.network_pdep import NetworkSolve
    from tests.workers.test_upload_worker_warnings import _pdep

    response = client.post("/api/v1/uploads/networks/pdep", json=_pdep())
    assert response.status_code == 201, response.text[:300]
    return f"{BASE}/network-solves/{db_session.get(NetworkSolve, response.json()['solve_id']).public_ref}"


def _deposit_conformer(client, db_session):
    from app.db.models.species import ConformerObservation

    response = client.post(
        "/api/v1/uploads/conformers", json=hook._conformer_payload(smiles="C", charge=0, multiplicity=1)
    )
    assert response.status_code == 201, response.text[:300]
    return f"{BASE}/conformer-observations/{db_session.get(ConformerObservation, response.json()['id']).public_ref}"


_ROUTES = {
    "calculation": _deposit_calc,
    "transition_state_entry": lambda c, s: _deposit_bundle(c, s, "tse"),
    "transition_state": lambda c, s: _deposit_bundle(c, s, "concept"),
    "kinetics_per_parent": lambda c, s: _deposit_bundle(c, s, "kinetics"),
    "thermo_per_parent": lambda c, s: _deposit_bundle(c, s, "thermo"),
    "statmech": _deposit_statmech,
    "transport": _deposit_transport,
    "network": _deposit_network,
    "network_solve": _deposit_solve,
    "conformer_observation": _deposit_conformer,
}


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_a_route_that_carries_the_warning_blocks_is_private_and_varies_by_credential(client, db_session, route):
    url = _ROUTES[route](client, db_session)
    _, key = _user(db_session, f"dw-cache-{route}")
    for headers in ({}, {"X-API-Key": key}):
        response = client.get(url, headers=headers)
        assert response.status_code == 200, (route, response.text[:200])
        assert "record_warnings" in response.json() and "deposit_warnings" in response.json()
        assert response.headers["cache-control"] == "private"
        vary = {v.strip().lower() for v in response.headers["vary"].split(",")}
        assert {"cookie", "x-api-key"} <= vary


def test_a_read_that_carries_no_warning_blocks_is_left_alone(client, db_session):
    response = client.get(f"{BASE}/calculations/search", params={"calculation_type": "freq", "limit": 1})
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "private"


# ---------------------------------------------------------------------------
# the 401 contract and a stale cookie
# ---------------------------------------------------------------------------


def test_a_revoked_session_is_refused_on_a_read_and_the_session_probe_clears_the_cookie(client, db_session):
    user, _ = _user(db_session, "dw-stale")
    _, token = create_session(db_session, user)
    calc = _calc(db_session)
    client.cookies.set("tckdb_session", token)
    assert client.get("/api/v1/auth/me").status_code == 200

    assert revoke_session(db_session, token)
    refused = client.get(f"{BASE}/calculations/{calc.public_ref}")
    assert refused.status_code == 401, "an invalid credential is refused, not read as anonymous"

    probe = client.get("/api/v1/auth/me")
    assert probe.status_code == 401
    cleared = probe.headers.get("set-cookie", "")
    assert "tckdb_session=" in cleared and ("Max-Age=0" in cleared or "expires=" in cleared.lower()), cleared


def test_the_session_probe_does_not_clear_a_cookie_for_a_bad_api_key_or_for_no_credentials(client, db_session):
    probe = client.get("/api/v1/auth/me")
    assert probe.status_code == 401 and "set-cookie" not in probe.headers
    client.cookies.clear()
    bad_key = client.get("/api/v1/auth/me", headers={"X-API-Key": "tckdb_not_a_real_key"})
    assert bad_key.status_code == 401 and "tckdb_session" not in bad_key.headers.get("set-cookie", "")
