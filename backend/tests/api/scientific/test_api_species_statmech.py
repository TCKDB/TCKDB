"""API tests for GET /api/v1/scientific/species-entries/{id}/statmech.

The per-entry statmech read mirrors the per-entry thermo read: a thin
list wrapper over the statmech detail/search machinery, pinned to one
species entry, honouring the opt-in ``include=trust`` policy.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
    set_review,
)


def _entry(db_session):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("SESTAT")
    )
    return species, make_species_entry(db_session, species)


def _url(handle, **params) -> str:
    base = f"/api/v1/scientific/species-entries/{handle}/statmech"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Basics + handle resolution
# ---------------------------------------------------------------------------


def test_returns_200_for_valid_species_entry_id(client, db_session):
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)

    resp = client.get(_url(entry.id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request"]["filter"]["species_entry_ref"] == entry.public_ref
    assert body["pagination"]["total"] == 1
    assert len(body["records"]) == 1
    assert body["records"][0]["statmech"]["statmech_ref"] == sm.public_ref


def test_resolves_species_entry_ref_handle(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)

    resp = client.get(_url(entry.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["pagination"]["total"] == 1


def test_returns_404_for_missing_species_entry(client, db_session):
    resp = client.get(_url(999999))
    assert resp.status_code == 404
    assert "handle_not_found" in resp.text or "not found" in resp.text


def test_wrong_prefix_handle_returns_422(client, db_session):
    resp = client.get(_url("sm_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_empty_when_entry_has_no_statmech(client, db_session):
    _, entry = _entry(db_session)
    body = client.get(_url(entry.id)).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_rejects_invalid_pagination(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, limit=999))
    assert resp.status_code == 422


def test_rejects_client_sort(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_unknown_include_token_returns_422(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Trust include policy
# ---------------------------------------------------------------------------


def test_trust_omitted_by_default(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id)).json()
    assert "trust" not in body["records"][0]


def test_include_trust_returns_computed_statmech_v1(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)

    body = client.get(_url(entry.id, include="trust")).json()
    assert body["request"]["include"] == ["trust"]
    trust = body["records"][0]["trust"]
    assert trust["review_status"] == "not_reviewed"
    assert trust["is_certified"] is False
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    evidence = trust["evidence"]
    assert evidence["record_type"] == "statmech"
    assert evidence["rubric"] == "computed_statmech_v1"
    assert evidence["rubric_version"] == 1
    assert "record_id" not in evidence


def test_include_all_excludes_trust(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)

    body = client.get(_url(entry.id, include="all")).json()
    assert "trust" not in body["request"]["include"]
    assert "trust" not in body["records"][0]


def test_include_trust_uses_review_badge(client, db_session):
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_url(entry.id, include="trust")).json()
    assert body["records"][0]["trust"]["review_status"] == "approved"


# ---------------------------------------------------------------------------
# Internal-ID policy
# ---------------------------------------------------------------------------


def test_internal_ids_silently_dropped_when_disallowed(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id, include="internal_ids")).json()
    assert body["request"]["include"] == []
    assert "statmech_id" not in body["records"][0]["statmech"]


def test_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id, include="internal_ids")).json()
    assert body["records"][0]["statmech"]["statmech_id"] == sm.id


def test_trust_record_id_gated_by_internal_ids(
    client, db_session, allow_internal_ids
):
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id, include="trust,internal_ids")).json()
    assert body["records"][0]["trust"]["evidence"]["record_id"] == sm.id


# ---------------------------------------------------------------------------
# Ordering / filtering parity with search
# ---------------------------------------------------------------------------


def test_default_hides_rejected(client, db_session):
    _, entry = _entry(db_session)
    sm_a = make_statmech(db_session, species_entry=entry)
    sm_b = make_statmech(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_url(entry.id)).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert sm_a.public_ref in refs
    assert sm_b.public_ref not in refs


def test_include_rejected_sorts_them_last(client, db_session):
    _, entry = _entry(db_session)
    sm_a = make_statmech(db_session, species_entry=entry)
    sm_b = make_statmech(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_url(entry.id, include_rejected="true")).json()
    refs = [r["statmech"]["statmech_ref"] for r in body["records"]]
    assert sm_a.public_ref in refs and sm_b.public_ref in refs
    assert refs[-1] == sm_b.public_ref


def test_ordering_review_then_created(client, db_session):
    _, entry = _entry(db_session)
    sm_a = make_statmech(db_session, species_entry=entry)
    make_statmech(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_a.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_url(entry.id)).json()
    assert body["records"][0]["statmech"]["statmech_ref"] == sm_a.public_ref


def test_pagination_envelope(client, db_session):
    _, entry = _entry(db_session)
    for _ in range(4):
        make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id, limit=2, offset=0)).json()
    p = body["pagination"]
    assert (p["limit"], p["offset"], p["returned"], p["total"]) == (2, 0, 2, 4)


def test_wrapper_agrees_with_search_for_pinned_entry(client, db_session):
    """The per-entry read and a search pinned to the same entry return
    identical records for a non-trust include set."""
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)
    lot = make_lot(db_session)
    calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_statmech_source_calculation(
        db_session,
        statmech=sm,
        calculation=calc,
        role=StatmechCalculationRole.freq,
    )

    wrapper = client.get(_url(entry.id, include="source_calculations")).json()
    search = client.get(
        "/api/v1/scientific/statmech/search"
        f"?species_entry_ref={entry.public_ref}&include=source_calculations"
    ).json()
    assert wrapper["records"] == search["records"]


# ---------------------------------------------------------------------------
# Collapse + named selection policy (read-time selection, no persistence)
# ---------------------------------------------------------------------------


def test_collapse_all_is_default_and_returns_all_candidates(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id)).json()
    # Default is non-canonical: every candidate is returned.
    assert body["request"]["collapse"] == "all"
    assert body["request"]["selection_policy"] == "default"
    assert body["pagination"]["total"] == 2
    assert len(body["records"]) == 2


def test_collapse_first_returns_single_record_with_pre_collapse_total(
    client, db_session
):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    make_statmech(db_session, species_entry=entry)
    body = client.get(_url(entry.id, collapse="first")).json()
    assert body["request"]["collapse"] == "first"
    assert len(body["records"]) == 1
    # total stays pre-collapse so clients see how many candidates exist.
    assert body["pagination"]["total"] == 2


def test_default_policy_selects_best_reviewed(client, db_session):
    _, entry = _entry(db_session)
    sm_old_approved = make_statmech(db_session, species_entry=entry)
    make_statmech(db_session, species_entry=entry)  # newer, not reviewed
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_old_approved.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _url(entry.id, collapse="first", selection_policy="default")
    ).json()
    assert body["request"]["selection_policy"] == "default"
    assert (
        body["records"][0]["statmech"]["statmech_ref"]
        == sm_old_approved.public_ref
    )


def test_latest_policy_selects_newest_over_review_status(client, db_session):
    _, entry = _entry(db_session)
    sm_old_approved = make_statmech(db_session, species_entry=entry)
    sm_new = make_statmech(db_session, species_entry=entry)  # newer
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_old_approved.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _url(entry.id, collapse="first", selection_policy="latest")
    ).json()
    # 'latest' ignores review rank and picks the most recent candidate.
    assert body["records"][0]["statmech"]["statmech_ref"] == sm_new.public_ref


def test_invalid_selection_policy_returns_422(client, db_session):
    _, entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    resp = client.get(_url(entry.id, collapse="first", selection_policy="bogus"))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# No mutation
# ---------------------------------------------------------------------------


def test_read_does_not_mutate_statmech(client, db_session):
    _, entry = _entry(db_session)
    sm = make_statmech(db_session, species_entry=entry)
    before = (
        sm.statmech_treatment,
        sm.external_symmetry,
        sm.point_group,
        sm.is_linear,
    )
    resp = client.get(_url(entry.id, include="trust"))
    assert resp.status_code == 200, resp.text
    db_session.refresh(sm)
    after = (
        sm.statmech_treatment,
        sm.external_symmetry,
        sm.point_group,
        sm.is_linear,
    )
    assert after == before
