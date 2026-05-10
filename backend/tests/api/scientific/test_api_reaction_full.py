"""API tests for GET /api/v1/scientific/reaction-entries/{id}/full."""

from __future__ import annotations

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("FAPI1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("FAPI2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def test_returns_200_for_valid_reaction_entry_id(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reaction_entry"]["id"] == entry.id
    # Default: species + kinetics + transition_states present.
    assert body["species"] is not None
    assert body["kinetics"] == []
    assert body["transition_states"] == []


def test_returns_404_for_missing_reaction_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/reaction-entries/999999/full")
    assert resp.status_code == 404


def test_include_all_populates_every_top_level_section(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=all"
    )
    body = resp.json()
    # Empty arrays for empty included sections (per Phase 2.1 policy).
    for key in (
        "species",
        "kinetics",
        "transition_states",
        "calculations",
        "path_search",
        "irc",
        "scans",
        "conformers",
        "artifacts",
    ):
        assert key in body
        assert body[key] is not None  # included sections are present


def test_default_omits_non_default_sections(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    body = resp.json()
    # Default include set: species, kinetics, transition_states only.
    assert body["calculations"] is None
    assert body["path_search"] is None
    assert body["artifacts"] is None


def test_non_ts_backed_kinetics_no_fabricated_ts_links(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=kinetics,transition_states"
    )
    body = resp.json()
    assert len(body["kinetics"]) == 1
    p = body["kinetics"][0]["provenance"]
    assert p["transition_state_entry_id"] is None
    assert body["transition_states"] == []  # not fabricated


def test_include_review_full_adds_audit_array(client, db_session):
    entry = _entry(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
    )
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include_review=full"
    )
    body = resp.json()
    assert body["review_records"] is not None
    assert any(
        r["record_type"] == "reaction_entry" and r["record_id"] == entry.id
        for r in body["review_records"]
    )


def test_default_review_summary_omits_audit_array(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    assert resp.json()["review_records"] is None


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text
