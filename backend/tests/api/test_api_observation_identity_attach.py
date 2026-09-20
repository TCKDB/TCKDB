"""Tests for the curator/admin observation-identity-attach route.

``POST /api/v1/admin/observations/{ref}/identity`` is the one write path
that fills in ``molecular_property_observation.species_entry_id`` after
deposit -- see ``app/services/observation_identity_attach.py``. Follows the
existing admin-route testing pattern (``test_admin_energy_correction_scheme_
provenance.py``): the ``client`` fixture's default actor is role=user (the
403 path), ``login_as`` swaps roles, and ``anon_client`` exercises the
anonymous 401 path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import SpeciesEntryStateKind, StationaryPointKind
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
    assert obs.raw_payload_json["identity_attachment"]["actor"] == "testcurator"
    assert (
        obs.raw_payload_json["identity_attachment"]["note"] == "matched by formula"
    )
    assert "id" not in obs.raw_payload_json["identity_attachment"]


def test_admin_can_also_attach(client, db_session, login_as, _api_admin_user):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
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


def test_ambiguous_target_refuses(client, db_session, login_as, _api_curator_user):
    species, entry_a = _entry(db_session, prefix="SEAMBIG")
    entry_b = make_species_entry(
        db_session,
        species,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.ground,
        stereo_label="cis",
    )
    obs = make_observation(db_session, species_entry=None)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry_a.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_ambiguous_entry" in resp.text

    resp_b = client.post(
        _url(obs.public_ref), json={"species_entry_ref": entry_b.public_ref}
    )
    assert resp_b.status_code == 422, resp_b.text
    assert "observation_identity_ambiguous_entry" in resp_b.text


def test_non_ground_state_target_refuses(
    client, db_session, login_as, _api_curator_user
):
    species, ground_entry = _entry(db_session, prefix="SEEXCITED")
    excited_entry = make_species_entry(
        db_session,
        species,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.excited,
        electronic_state_label="A",
    )
    obs = make_observation(db_session, species_entry=None)
    login_as(_api_curator_user)

    resp = client.post(
        _url(obs.public_ref), json={"species_entry_ref": excited_entry.public_ref}
    )
    assert resp.status_code == 422, resp.text
    assert "observation_identity_ambiguous_entry" in resp.text


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
    from sqlalchemy import func, select

    from app.db.models.species import Species, SpeciesEntry

    before_species = db_session.scalar(select(func.count()).select_from(Species))
    before_entries = db_session.scalar(select(func.count()).select_from(SpeciesEntry))

    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=None)
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
