"""Chunk 6.1: the species statmech read and statmech search tell the truth.

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.1). Each test names the
mutation it kills. The actual orders are unchanged by this PR; what is new is
that the response *reports* the order, the review exclusions and whether the
selection policy applied.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
    set_review,
)
from tests.services.test_scientific_record_supersession import (
    _statmech_pair,
    _supersede_statmech,
)

REVIEW_ORDER = [
    {"key": "review_rank", "direction": "asc"},
    {"key": "created_at", "direction": "desc"},
    {"key": "id", "direction": "desc"},
]
LATEST_ORDER = [
    {"key": "created_at", "direction": "desc"},
    {"key": "id", "direction": "desc"},
]


def _keys(order):
    return [{"key": k["key"], "direction": k["direction"]} for k in order["keys"]]


def _entry(db_session):
    species = make_species(db_session, smiles="CCO", inchi_key=next_inchi_key("HECHO"))
    return make_species_entry(db_session, species)


def _species_url(entry, **params) -> str:
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/statmech"
    return base + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")


def _ref(rec) -> str:
    return rec["statmech"]["statmech_ref"]


# --- supersession on the species read ---------------------------------------


def test_species_read_carries_the_same_supersession_notice_as_detail(client, db_session):
    """Kills: dropping ``supersession=`` in the species materializer."""
    actor, (old, new) = _statmech_pair(db_session)
    _supersede_statmech(db_session, actor, old, new)
    entry = old.species_entry

    detail = client.get(f"/api/v1/scientific/statmech/{old.public_ref}").json()["record"]
    listed = client.get(_species_url(entry, include_deprecated="true")).json()
    row = next(r for r in listed["records"] if _ref(r) == old.public_ref)

    assert detail["supersession"] is not None
    assert row["supersession"] == detail["supersession"]


def test_only_the_superseded_record_on_a_page_carries_the_notice(client, db_session):
    """Kills: attaching the notice by page position instead of by id."""
    actor, (old, new) = _statmech_pair(db_session)
    _supersede_statmech(db_session, actor, old, new)
    body = client.get(_species_url(old.species_entry, include_deprecated="true")).json()

    by_ref = {_ref(r): r for r in body["records"]}
    assert set(by_ref) == {old.public_ref, new.public_ref}
    assert by_ref[old.public_ref]["supersession"] is not None
    assert by_ref[new.public_ref]["supersession"] is None
    assert sum(1 for r in body["records"] if r["supersession"] is not None) == 1


# --- effective order / policy applied ---------------------------------------


def _older_approved_newer_unreviewed(db_session):
    entry = _entry(db_session)
    older = make_statmech(db_session, species_entry=entry)
    newer = make_statmech(db_session, species_entry=entry)
    older.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.flush()
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=older.id,
        status=RecordReviewStatus.approved,
    )
    return entry, older, newer


def test_the_species_read_lists_every_record_in_the_echoed_order_and_picks_none(client, db_session):
    """Kills: a read that still picks one record, or an echo that says something the list does not do."""
    entry, older, newer = _older_approved_newer_unreviewed(db_session)
    body = client.get(_species_url(entry)).json()

    # The approved record is first although it is older; the newer unreviewed one follows. Neither is dropped.
    assert [_ref(r) for r in body["records"]] == [older.public_ref, newer.public_ref]
    assert _keys(body["request"]["sort"]) == REVIEW_ORDER
    for removed in ("collapse", "selection_policy", "selection_policy_applied", "effective_order"):
        assert removed not in body["request"]


def test_collapse_and_selection_policy_are_refused_and_point_at_the_select_endpoint(client, db_session):
    """Kills: ``?collapse=first`` quietly ignored (it used to pick one record)."""
    entry, _older, _newer = _older_approved_newer_unreviewed(db_session)
    for params in ({"collapse": "first"}, {"selection_policy": "latest"}, {"collapse": "all"}):
        resp = client.get(_species_url(entry, **params))
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "browse_parameter_removed"
        assert body["context"]["parameter"] == next(iter(params))
        assert body["context"]["select_endpoint"].endswith("/statmech/select")


# --- review visibility ------------------------------------------------------


def test_curated_floor_is_reported_and_requested_flag_still_echoed(client, db_session):
    """Kills: echoing requested flags as effective ones."""
    entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    body = client.get(
        f"/api/v1/scientific/statmech/search?species_entry_ref={entry.public_ref}"
        "&include_rejected=true&profile=curated"
    ).json()
    req = body["request"]
    assert req["filter"]["include_rejected"] is True
    vis = req["review_visibility"]
    assert "rejected" in vis["excluded_statuses"]
    assert vis["profile_floor_applied"] is True
    assert vis["visible_statuses"] == ["approved"]


def test_default_visibility_excludes_rejected_and_deprecated(client, db_session):
    entry = _entry(db_session)
    make_statmech(db_session, species_entry=entry)
    for url in (
        _species_url(entry),
        f"/api/v1/scientific/statmech/search?species_entry_ref={entry.public_ref}",
    ):
        vis = client.get(url).json()["request"]["review_visibility"]
        assert vis["excluded_statuses"] == ["deprecated", "rejected"]
        assert vis["profile_floor_applied"] is False
        assert set(vis["visible_statuses"]) == {"approved", "under_review", "not_reviewed"}


# --- empty paths ------------------------------------------------------------


def test_empty_responses_carry_the_full_echo(client, db_session):
    """Kills: ``_empty_response`` paths left on the old echo."""
    entry = _entry(db_session)
    empty_entry = client.get(_species_url(entry)).json()
    # Candidates exist but none are visible.
    hidden = _entry(db_session)
    sm = make_statmech(db_session, species_entry=hidden)
    set_review(
        db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.rejected
    )
    hidden_page = client.get(_species_url(hidden)).json()
    search_empty = client.get(f"/api/v1/scientific/statmech/search?species_entry_ref={entry.public_ref}").json()
    search_unknown_ref = client.post(
        "/api/v1/scientific/statmech/search", json={"species_entry_ref": "spe_" + "a" * 26}
    ).json()

    for body in (empty_entry, hidden_page, search_empty, search_unknown_ref):
        assert body["records"] == []
        req = body["request"]
        assert _keys(req["sort"]) == REVIEW_ORDER
        assert req["review_visibility"]["visible_statuses"]
        assert "rejected" in req["review_visibility"]["excluded_statuses"]


# --- ties: the returned order follows the echoed order ---------------------


def test_the_species_read_and_search_order_follows_the_echoed_order_on_true_ties(client, db_session):
    """Kills: flipping the id tie-break in the species read or in search.

    Three records share one status and one ``created_at`` exactly, so only the ``id`` key can order them. The
    returned order must equal a sort by the ``sort`` the same response echoed.
    """
    entry = _entry(db_session)
    tied = datetime.now(timezone.utc) - timedelta(days=1)
    rows = [make_statmech(db_session, species_entry=entry) for _ in range(3)]
    for row in rows:
        row.created_at = tied
    db_session.flush()
    id_by_ref = {row.public_ref: row.id for row in rows}
    assert len(set(id_by_ref.values())) == 3

    for body in (
        client.get(_species_url(entry)).json(),
        client.get(f"/api/v1/scientific/statmech/search?species_entry_ref={entry.public_ref}").json(),
    ):
        returned = [_ref(r) for r in body["records"]]
        assert sorted(returned) == sorted(id_by_ref)
        order = body["request"]["sort"]["keys"]
        assert order[-1]["key"] == "id"
        # Status and created_at tie, so the only live key is id.
        sign = -1 if order[-1]["direction"] == "desc" else 1
        assert returned == sorted(id_by_ref, key=lambda ref: sign * id_by_ref[ref])
