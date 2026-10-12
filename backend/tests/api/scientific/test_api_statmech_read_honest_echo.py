"""Chunk 6.1: the species statmech read and statmech search tell the truth.

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.1). Each test names the
mutation it kills. The actual orders are unchanged by this PR; what is new is
that the response *reports* the order, the review exclusions and whether the
selection policy applied.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    SelectionPolicy,
    simple_selection_sort_key,
)
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


def test_collapse_first_latest_echoes_the_order_that_chose_the_record(client, db_session):
    """Kills: echo hard-coded to the review order; policy silently not applied."""
    entry, older, newer = _older_approved_newer_unreviewed(db_session)
    body = client.get(_species_url(entry, collapse="first", selection_policy="latest")).json()

    assert [_ref(r) for r in body["records"]] == [newer.public_ref]
    assert body["request"]["effective_order"] == LATEST_ORDER
    assert body["request"]["selection_policy_applied"] is True
    # ``sort`` keeps its documented candidate-list meaning, unchanged.
    assert body["request"]["sort"] == "review_rank,created_at,id"


def test_collapse_first_default_echoes_the_review_order(client, db_session):
    entry, older, _ = _older_approved_newer_unreviewed(db_session)
    body = client.get(_species_url(entry, collapse="first")).json()
    assert [_ref(r) for r in body["records"]] == [older.public_ref]
    assert body["request"]["effective_order"] == REVIEW_ORDER
    assert body["request"]["selection_policy_applied"] is True


def test_collapse_all_latest_keeps_review_order_and_says_policy_not_applied(client, db_session):
    """Kills: the policy leaking into the ``collapse=all`` order."""
    entry, older, newer = _older_approved_newer_unreviewed(db_session)
    body = client.get(_species_url(entry, selection_policy="latest")).json()

    assert [_ref(r) for r in body["records"]] == [older.public_ref, newer.public_ref]
    assert body["request"]["selection_policy"] == "latest"
    assert body["request"]["effective_order"] == REVIEW_ORDER
    assert body["request"]["selection_policy_applied"] is False


def test_echoed_order_agrees_with_the_real_sort_key():
    """Kills: the echo table drifting from ``simple_selection_sort_key``.

    Sorts the same records by the real key and by a key built from the echoed
    order, for every policy, and requires the same sequence.
    """
    from app.schemas.reads.scientific_common import effective_order_echo

    now = datetime.now(timezone.utc)
    status = {
        1: RecordReviewStatus.not_reviewed,
        2: RecordReviewStatus.approved,
        3: RecordReviewStatus.approved,
        4: RecordReviewStatus.under_review,
    }
    created = {1: now, 2: now - timedelta(days=3), 3: now - timedelta(days=1), 4: now - timedelta(days=5)}
    ids = [1, 2, 3, 4]
    policies = list(SelectionPolicy)
    assert len(policies) == 3

    def echoed_key(cid, order):
        values = {"review_rank": REVIEW_RANK[status[cid]], "created_at": created[cid].timestamp(), "id": cid}
        return tuple(
            values[k.key] if k.direction == "asc" else -values[k.key] for k in order
        )

    sequences = set()
    for policy in policies:
        real = sorted(
            ids,
            key=lambda c: simple_selection_sort_key(
                c, policy=policy, review_status_by_id=status, created_at_by_id=created
            ),
        )
        order = effective_order_echo(collapse_first=True, policy=policy)
        assert real == sorted(ids, key=lambda c: echoed_key(c, order))
        sequences.add(tuple(real))
    # The policies must actually differ on this data, or the loop proves nothing.
    assert len(sequences) == 2


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
    empty_entry = client.get(_species_url(entry, collapse="first", selection_policy="latest")).json()
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
        assert req["effective_order"], req
        assert req["review_visibility"]["visible_statuses"]
        assert "rejected" in req["review_visibility"]["excluded_statuses"]
    assert empty_entry["request"]["effective_order"] == LATEST_ORDER
    assert empty_entry["request"]["selection_policy_applied"] is True
    assert search_empty["request"]["selection_policy_applied"] is None
    assert search_empty["request"]["effective_order"] == REVIEW_ORDER
