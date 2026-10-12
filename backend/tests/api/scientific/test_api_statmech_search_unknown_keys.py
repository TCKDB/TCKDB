"""Chunk 6.4: statmech search refuses a key it does not declare, on both verbs.

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.4 review follow-up). Before this a misspelled filter returned 200 and
was ignored, so it quietly broadened the result, the same defect class as the ignored TS filter fixed in 6.2.
Each test names the mutation it kills.
"""

from __future__ import annotations

from app.schemas.reads.scientific_statmech_search import StatmechSearchRequest
from tests.services.scientific_read._factories import make_species, make_species_entry, make_statmech, next_inchi_key

SEARCH = "/api/v1/scientific/statmech/search"


def _sm(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("UNK64")))
    return make_statmech(db_session, species_entry=entry)


def test_post_unknown_body_key_is_a_422(client, db_session):
    """Kills: dropping ``extra="forbid"`` from ``StatmechSearchRequest``."""
    sm = _sm(db_session)
    r = client.post(SEARCH, json={"statmech_ref": sm.public_ref, "has_torsion": False})
    assert r.status_code == 422
    assert any(d["type"] == "extra_forbidden" and d["loc"][-1] == "has_torsion" for d in r.json()["detail"])


def test_post_unknown_key_alone_is_not_taken_for_a_filter(client):
    """Kills: the unknown key being ignored and the request reaching the missing-filter path instead."""
    r = client.post(SEARCH, json={"has_torsion": False})
    assert r.status_code == 422
    assert "extra_forbidden" in r.text


def test_get_unknown_query_key_is_a_422(client, db_session):
    """Kills: removing the GET unknown-key guard."""
    sm = _sm(db_session)
    r = client.get(SEARCH, params={"statmech_ref": sm.public_ref, "has_torsion": "false"})
    assert r.status_code == 422
    assert "unknown_query_parameter" in r.text
    assert "has_torsion" in r.text


def test_every_declared_field_is_still_accepted_on_get(client, db_session):
    """Kills: a GET allow-list that drifts from the request model (a declared filter refused)."""
    sm = _sm(db_session)
    ok = client.get(SEARCH, params={"statmech_ref": sm.public_ref, "limit": 5, "offset": 0})
    assert ok.status_code == 200
    valid = {"model_kind": "rrho", "min_review_status": "approved", "limit": "5", "offset": "0"}
    for field in sorted(set(StatmechSearchRequest.model_fields) - {"include", "sort"}):
        info = StatmechSearchRequest.model_fields[field]
        value = valid.get(field, "true" if info.annotation in (bool, bool | None) else "x")
        answer = client.get(SEARCH, params={field: value})
        assert "unknown_query_parameter" not in answer.text, field


def test_valid_post_body_is_unaffected(client, db_session):
    """Kills: the forbid applied too broadly (declared keys refused)."""
    sm = _sm(db_session)
    r = client.post(SEARCH, json={"statmech_ref": sm.public_ref, "has_torsions": False, "include": ["review"], "limit": 5})
    assert r.status_code == 200
    assert [x["statmech"]["statmech_ref"] for x in r.json()["records"]] == [sm.public_ref]
