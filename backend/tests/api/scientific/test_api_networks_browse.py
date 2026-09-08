"""API tests for GET /api/v1/scientific/networks/browse.

Companion to ``test_api_scientific_networks.py``'s search-surface tests.
Reuses that module's fixture helper (``_make_simple_network``) directly
rather than duplicating it, since the two surfaces share the exact same
candidate query and record shape (see
``app/services/scientific_read/networks_search.py::_run_network_query``)
-- only "no filter required" is new.
"""

from __future__ import annotations

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.api.scientific.test_api_scientific_networks import (
    _detail_url,
    _make_simple_network,
)
from tests.services.scientific_read._factories import (
    make_network,
    set_review,
)


def _browse_url(**params) -> str:
    base = "/api/v1/scientific/networks/browse"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# The headline defect this route exists to fix
# ---------------------------------------------------------------------------


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The exact call that 422s on /search today must 200 here."""
    _make_simple_network(db_session)

    search_resp = client.get("/api/v1/scientific/networks/search")
    assert search_resp.status_code == 422
    assert "missing_filter" in search_resp.text

    browse_resp = client.get(_browse_url())
    assert browse_resp.status_code == 200


def test_unfiltered_browse_returns_a_network_with_no_children(client, db_session):
    """Mutation-adjacent: unlike ``level_of_theory``, a ``Network`` row is
    not usage-derived -- an empty network with no attached species or
    reactions must still appear in an unfiltered browse, because
    ``search_networks`` never gates candidacy on child-row existence
    either (only optional ``has_*`` filters do, and browse leaves them
    unset by default).
    """
    empty = make_network(db_session, name="browse-empty-network")
    body = client.get(_browse_url()).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert empty.public_ref in refs


def test_browse_returns_the_same_record_shape_as_search(client, db_session):
    fx = _make_simple_network(db_session)

    search_body = client.get(
        f"/api/v1/scientific/networks/search?network_ref={fx['network'].public_ref}"
    ).json()
    browse_body = client.get(_browse_url()).json()

    search_record = search_body["records"][0]
    browse_record = next(
        r
        for r in browse_body["records"]
        if r["network"]["network_ref"] == fx["network"].public_ref
    )
    assert set(search_record.keys()) == set(browse_record.keys())
    assert search_record == browse_record


# ---------------------------------------------------------------------------
# Filters -- every one search accepts, network_ref included
# ---------------------------------------------------------------------------


def test_browse_by_network_ref_narrows(client, db_session):
    a = _make_simple_network(db_session)
    _b = _make_simple_network(db_session)
    body = client.get(_browse_url(network_ref=a["network"].public_ref)).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_browse_by_species_entry_ref(client, db_session):
    a = _make_simple_network(db_session)
    _b = _make_simple_network(db_session)
    body = client.get(
        _browse_url(species_entry_ref=a["species_entry"].public_ref)
    ).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_browse_by_reaction_entry_ref(client, db_session):
    a = _make_simple_network(db_session, with_reaction=True)
    _b = _make_simple_network(db_session, with_reaction=True)
    body = client.get(
        _browse_url(reaction_entry_ref=a["reaction_entry"].public_ref)
    ).json()
    refs = {r["network"]["network_ref"] for r in body["records"]}
    assert refs == {a["network"].public_ref}


def test_browse_has_species_true_and_false(client, db_session):
    a = _make_simple_network(db_session, with_species=True)
    b = _make_simple_network(db_session, with_species=False)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_browse_url(has_species="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_browse_url(has_species="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


def test_browse_has_channels_true_and_false(client, db_session):
    a = _make_simple_network(db_session, with_channel=True)
    b = _make_simple_network(db_session)
    true_refs = {
        r["network"]["network_ref"]
        for r in client.get(_browse_url(has_channels="true")).json()["records"]
    }
    false_refs = {
        r["network"]["network_ref"]
        for r in client.get(_browse_url(has_channels="false")).json()["records"]
    }
    assert a["network"].public_ref in true_refs
    assert b["network"].public_ref in false_refs


# ---------------------------------------------------------------------------
# Review visibility -- same convention search uses
# ---------------------------------------------------------------------------


def test_browse_default_hides_rejected(client, db_session):
    a = _make_simple_network(db_session)
    b = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=b["network"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = {
        r["network"]["network_ref"] for r in client.get(_browse_url()).json()["records"]
    }
    assert a["network"].public_ref in refs
    assert b["network"].public_ref not in refs


def test_browse_include_rejected_sorts_last(client, db_session):
    a = _make_simple_network(db_session)
    b = _make_simple_network(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.network,
        record_id=b["network"].id,
        status=RecordReviewStatus.rejected,
    )
    refs = [
        r["network"]["network_ref"]
        for r in client.get(_browse_url(include_rejected="true")).json()["records"]
    ]
    assert a["network"].public_ref in refs
    assert b["network"].public_ref in refs
    assert refs[-1] == b["network"].public_ref


# ---------------------------------------------------------------------------
# Sort / pagination / shape
# ---------------------------------------------------------------------------


def test_browse_client_sort_rejected(client, db_session):
    _make_simple_network(db_session)
    resp = client.get(_browse_url(sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_browse_pagination_envelope(client, db_session):
    for _ in range(4):
        make_network(db_session, name="browse-pag")
    body = client.get(_browse_url(limit=2, offset=0)).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] >= 4


def test_browse_record_shape_matches_detail(client, db_session):
    fx = _make_simple_network(db_session)
    detail = client.get(_detail_url(fx["network"].public_ref)).json()["record"]
    browse = client.get(
        _browse_url(network_ref=fx["network"].public_ref)
    ).json()["records"][0]
    assert set(detail.keys()) == set(browse.keys())
