"""Curator routes that add and retire species names, and names following a species merge."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.species_name import SpeciesName
from app.services.species_merge import merge_species
from app.services.species_name_curation import forward_species_names
from tests.id_leak import id_in_text
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key

URL = "/api/v1/curation/species-names"
SEARCH = "/api/v1/scientific/species/name-search"


def _species(db_session, tag="CN"):
    sp = make_species(db_session, inchi_key=next_inchi_key(tag))
    make_species_entry(db_session, sp)
    return sp


def _post(client, species, name="butene", kind="common", **over):
    body = {"species_ref": species.public_ref, "name": name, "kind": kind}
    body.update(over)
    return client.post(URL, json=body)


def _live(db_session, species):
    return db_session.scalars(
        select(SpeciesName).where(SpeciesName.species_id == species.id, SpeciesName.retired_at.is_(None))
    ).all()


def _found(client, q):
    return [m["species_ref"] for m in client.get(SEARCH, params={"q": q}).json()["matches"]]


@pytest.fixture
def curator(login_as, _api_curator_user):
    return login_as(_api_curator_user)


@pytest.fixture
def anon_client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


# --- role gate ------------------------------------------------------------------------------------------------------


def test_anonymous_is_refused(anon_client, db_session):
    sp = _species(db_session)
    assert _post(anon_client, sp).status_code == 401
    assert anon_client.post(f"{URL}/snm_whatever/retire").status_code == 401
    assert _live(db_session, sp) == []


def test_a_contributor_is_refused(client, db_session):
    sp = _species(db_session)
    assert _post(client, sp).status_code == 403
    assert _live(db_session, sp) == []


def test_a_curator_adds_a_name(client, db_session, curator):
    sp = _species(db_session)
    resp = _post(client, sp, name="  But-1-ene ")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["species_ref"] == sp.public_ref and body["name"] == "But-1-ene"
    assert body["kind"] == "common" and body["source_kind"] == "curated" and body["retired_at"] is None
    assert body["name_ref"].startswith("snm_")
    row = _live(db_session, sp)[0]
    assert row.created_by_user_id == curator.id and row.created_at is not None
    assert row.source_kind is SpeciesNameSourceKind.curated and row.external_source_id is None
    assert row.name_key == "but-1-ene"


def test_an_admin_adds_and_retires(client, db_session, login_as, _api_admin_user):
    login_as(_api_admin_user)
    sp = _species(db_session)
    ref = _post(client, sp).json()["name_ref"]
    assert client.post(f"{URL}/{ref}/retire").status_code == 200


# --- validation -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 129, "x" * 5000, "bad%name", "semi;colon", "tab\x00nul", "zero​width", "---", "a/b", "a_b"],
)
def test_invalid_names_are_refused(client, db_session, curator, name):
    sp = _species(db_session)
    resp = _post(client, sp, name=name)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "species_name_invalid"
    assert _live(db_session, sp) == []


def test_the_longest_name_is_accepted(client, db_session, curator):
    sp = _species(db_session)
    assert _post(client, sp, name="a" * 128).status_code == 201


def test_every_allowed_character_is_accepted(client, db_session, curator):
    sp = _species(db_session)
    assert _post(client, sp, name="N,N'-bis(2-[x]) +ab.c:d é水").status_code == 201


def test_a_bad_kind_is_refused(client, db_session, curator):
    sp = _species(db_session)
    assert _post(client, sp, kind="nickname").status_code == 422
    assert _live(db_session, sp) == []


def test_the_body_cannot_carry_an_id(client, db_session, curator):
    sp = _species(db_session)
    assert _post(client, sp, species_id=sp.id).status_code == 422


# --- duplicates -----------------------------------------------------------------------------------------------------


def test_a_live_duplicate_is_refused_by_normalised_form(client, db_session, curator):
    sp = _species(db_session)
    assert _post(client, sp, name="Water").status_code == 201
    resp = _post(client, sp, name="  WATER ", kind="abbreviation")
    assert resp.status_code == 409 and resp.json()["code"] == "species_name_duplicate"
    assert len(_live(db_session, sp)) == 1


def test_the_same_name_on_another_species_is_allowed(client, db_session, curator):
    a, b = _species(db_session, "DA"), _species(db_session, "DB")
    assert _post(client, a, name="butene").status_code == 201
    assert _post(client, b, name="butene").status_code == 201


# --- retire ---------------------------------------------------------------------------------------------------------


def test_retire_then_retire_again(client, db_session, curator):
    sp = _species(db_session)
    ref = _post(client, sp).json()["name_ref"]
    first = client.post(f"{URL}/{ref}/retire")
    assert first.status_code == 200 and first.json()["retired_at"] is not None
    stamp = first.json()["retired_at"]
    second = client.post(f"{URL}/{ref}/retire")
    assert second.status_code == 200 and second.json()["retired_at"] == stamp
    assert _live(db_session, sp) == []


def test_retire_needs_a_curator(client, db_session, login_as, _api_curator_user, _api_other_user):
    login_as(_api_curator_user)
    sp = _species(db_session)
    ref = _post(client, sp).json()["name_ref"]
    login_as(_api_other_user)
    assert client.post(f"{URL}/{ref}/retire").status_code == 403
    assert len(_live(db_session, sp)) == 1


def test_a_retired_name_stays_retired_and_the_same_name_can_be_added_again(client, db_session, curator):
    sp = _species(db_session)
    old = _post(client, sp).json()["name_ref"]
    client.post(f"{URL}/{old}/retire")
    again = _post(client, sp)
    assert again.status_code == 201 and again.json()["name_ref"] != old
    rows = db_session.scalars(select(SpeciesName).where(SpeciesName.species_id == sp.id)).all()
    assert len(rows) == 2 and sum(r.retired_at is None for r in rows) == 1


# --- unknown refs ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["spc_" + "a" * 26, "not a ref", "snm_" + "a" * 26, "1", "x" * 60])
def test_an_unknown_or_wrong_kind_of_species_ref_is_a_plain_404(client, db_session, curator, ref):
    resp = client.post(URL, json={"species_ref": ref, "name": "x name", "kind": "common"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "resource_not_found"


@pytest.mark.parametrize("ref", ["snm_" + "a" * 26, "spc_" + "a" * 26, "junk"])
def test_an_unknown_name_ref_is_a_plain_404(client, db_session, curator, ref):
    resp = client.post(f"{URL}/{ref}/retire")
    assert resp.status_code == 404 and resp.json()["code"] == "resource_not_found"


def test_a_species_ref_given_as_a_name_ref_is_the_same_404_as_an_unknown_one(client, db_session, curator):
    sp = _species(db_session)
    known = client.post(f"{URL}/{sp.public_ref}/retire")
    unknown = client.post(f"{URL}/snm_{'b' * 26}/retire")
    assert known.status_code == unknown.status_code == 404
    assert known.json()["code"] == unknown.json()["code"]


# --- no ids ---------------------------------------------------------------------------------------------------------


def test_no_response_carries_an_internal_id(client, db_session, curator):
    sp = _species(db_session)
    responses = [
        _post(client, sp),
        _post(client, sp),  # duplicate
        _post(client, sp, name=""),  # invalid
        client.post(URL, json={"species_ref": "spc_" + "a" * 26, "name": "abc", "kind": "common"}),
        client.post(f"{URL}/snm_{'a' * 26}/retire"),
    ]
    ref = responses[0].json()["name_ref"]
    responses.append(client.post(f"{URL}/{ref}/retire"))
    row = db_session.scalar(select(SpeciesName).where(SpeciesName.public_ref == ref))
    for resp in responses:
        for value in (sp.id, row.id, curator.id):
            assert not id_in_text(value, resp.text)
    body = responses[0].json()
    assert set(body) == {"name_ref", "species_ref", "name", "kind", "source_kind", "created_at", "retired_at"}


# --- the search read ------------------------------------------------------------------------------------------------


def test_a_curated_name_is_found_and_a_retired_one_is_not(client, db_session, curator):
    sp = _species(db_session)
    assert _found(client, "isobutylene") == []
    ref = _post(client, sp, name="Isobutylene").json()["name_ref"]
    assert _found(client, "isobutylene") == [sp.public_ref]
    client.post(f"{URL}/{ref}/retire")
    assert _found(client, "isobutylene") == []


# --- merge forwarding -----------------------------------------------------------------------------------------------


def test_merge_moves_live_names_to_the_survivor(client, db_session, curator):
    loser, survivor = _species(db_session, "ML"), _species(db_session, "MS")
    _post(client, loser, name="moving name")
    retired = _post(client, loser, name="old name").json()["name_ref"]
    client.post(f"{URL}/{retired}/retire")
    merge_species(db_session, loser=loser, survivor=survivor)
    assert [r.name for r in _live(db_session, survivor)] == ["moving name"]
    assert _live(db_session, loser) == []
    kept = db_session.scalar(select(SpeciesName).where(SpeciesName.public_ref == retired))
    assert kept.species_id == loser.id and kept.retired_at is not None
    assert loser.merged_into_species_id == survivor.id
    assert _found(client, "moving name") == [survivor.public_ref]


def test_merge_retires_a_losing_name_the_survivor_already_holds(client, db_session, curator):
    loser, survivor = _species(db_session, "CL"), _species(db_session, "CS")
    _post(client, survivor, name="Butene")
    clash = _post(client, loser, name="butene ").json()["name_ref"]
    _post(client, loser, name="only on loser")
    merge_species(db_session, loser=loser, survivor=survivor)
    assert sorted(r.name_key for r in _live(db_session, survivor)) == ["butene", "only on loser"]
    row = db_session.scalar(select(SpeciesName).where(SpeciesName.public_ref == clash))
    assert row.retired_at is not None and row.species_id == loser.id
    live_count = db_session.scalar(
        select(func.count()).select_from(SpeciesName).where(SpeciesName.name_key == "butene", SpeciesName.retired_at.is_(None))
    )
    assert live_count == 1
    assert _found(client, "butene") == [survivor.public_ref]


def test_forwarding_alone_leaves_the_species_live(db_session):
    loser, survivor = _species(db_session, "FL"), _species(db_session, "FS")
    db_session.add(
        SpeciesName(
            species_id=loser.id, name="abc", name_key="abc", kind=SpeciesNameKind.common,
            source_kind=SpeciesNameSourceKind.curated,
        )
    )
    db_session.flush()
    forward_species_names(db_session, loser=loser, survivor=survivor)
    assert loser.merged_into_species_id is None and len(_live(db_session, survivor)) == 1


def test_merge_refuses_a_species_into_itself_or_a_merged_one(db_session):
    a, b, c = _species(db_session, "RA"), _species(db_session, "RB"), _species(db_session, "RC")
    with pytest.raises(ValueError):
        merge_species(db_session, loser=a, survivor=a)
    merge_species(db_session, loser=a, survivor=b)
    with pytest.raises(ValueError):
        merge_species(db_session, loser=c, survivor=a)


def test_a_name_added_to_a_merged_species_lands_on_the_survivor(client, db_session, curator):
    loser, survivor = _species(db_session, "AL"), _species(db_session, "AS")
    merge_species(db_session, loser=loser, survivor=survivor)
    resp = _post(client, loser, name="after merge")
    assert resp.status_code == 201 and resp.json()["species_ref"] == survivor.public_ref
