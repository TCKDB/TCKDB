"""What a cursor guarantees, and what it does not.

A cursor is opaque and tamper-proof, a forged one is a 422, the snapshot bounds insertions, and an empty cursor page
still says it is a cursor page. It does NOT freeze review state: ``review_rank`` leads the sort key, so a review change
can move a record across the reader's position and the traversal skips or repeats it. Those two behaviours are pinned
here as documented behaviour (``docs/specs/browse_order.md``), not as bugs.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.api.config import settings
from app.db.models.common import RecordReviewStatus
from app.db.models.species import SpeciesEntry
from tests.api.test_browse_order_echo import CASES
from tests.api.test_cursor_paging import (
    API,
    DEPRECATION_KEEPS_THE_ROW,
    MAX_PAGES,
    PAGED_CASES,
    ROUTE_OF_CASE,
    UNREVIEWED_CASES,
    _curator,  # noqa: F401  (autouse fixture of the sibling module)
    _first_cursor,
    code_of,
    decode,
    encode,
    full_list,
    get_page,
    refs_of,
    seed,
    set_review,
    walk,
    with_params,
)

REVIEWED = [c for c in PAGED_CASES if c not in UNREVIEWED_CASES]
#: Rankings that do not lead with the review rank (newest first, oldest first, lowest energy first): a review change
#: does not move a record across the reader there.
REVIEW_DOES_NOT_LEAD = {"species-calculations-latest", "species-calculations-earliest", "species-calculations-lowest-energy"}
REVIEW_LEADS = [c for c in REVIEWED if c not in DEPRECATION_KEEPS_THE_ROW and c not in REVIEW_DOES_NOT_LEAD]


def _walk_from(client, seeded, body, limit):
    """Continue a traversal from a page already fetched; returns the refs after it."""
    refs = []
    for _guard in range(MAX_PAGES + 1):
        assert _guard < MAX_PAGES, "the traversal does not end"
        if body["next_cursor"] is None:
            return refs
        body = get_page(client, seeded, limit=limit, cursor=body["next_cursor"])
        refs.extend(refs_of(seeded, body))
    return refs


# ---------------------------------------------------------------------------
# the token is opaque, and a key rotation only restarts a traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_decoded_without_the_key_reveals_no_integer(case, client, db_session):
    """Kills: a token that is plain base64 JSON (it hands out record ids and the snapshot bound, which the
    internal-id policy keeps out of every response)."""
    seeded = seed(case, db_session, client)
    token = _first_cursor(client, seeded)
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))

    with pytest.raises(ValueError):
        json.loads(raw)  # not JSON, so no integer in it can be read
    for word in (b"max_id", b"last_values", b"watermark", b"query_signature"):
        assert word not in raw
    payload = decode(token)  # with the key it is the cursor the engine issued
    mark = payload["watermark"]
    assert mark["max_id"] > 0 or any(mark["groups"].values()), "the check cannot see an id in a cursor it opens"


def test_flipping_any_single_byte_of_a_cursor_is_a_422(client, db_session):
    """Over HTTP: every one-byte edit of a real token is refused (nonce, ciphertext and tag all authenticate)."""
    seeded = seed("thermo-entry", db_session, client)
    token = _first_cursor(client, seeded)
    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    for index in range(len(raw)):
        forged = bytearray(raw)
        forged[index] ^= 0x80
        bad = base64.urlsafe_b64encode(bytes(forged)).decode().rstrip("=")
        assert code_of(client.get(with_params(seeded.url, cursor=bad))) == "invalid_cursor", index


@pytest.mark.parametrize("case", PAGED_CASES)
def test_rotating_the_key_makes_cursors_in_flight_a_422(case, client, db_session, monkeypatch):
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)
    assert client.get(with_params(seeded.url, cursor=cursor)).status_code == 200

    monkeypatch.setattr(settings, "paging_cursor_key", "rotated-" + "k" * 40)

    assert code_of(client.get(with_params(seeded.url, cursor=cursor))) == "invalid_cursor"
    # And a fresh traversal under the new key works.
    assert get_page(client, seeded, limit=2)["next_cursor"] is not None


# ---------------------------------------------------------------------------
# a forged cursor is a 422, never a 500
# ---------------------------------------------------------------------------

_BAD_VALUES = ["x", None, [1], {"a": 1}, True, float("nan"), {"__dt__": "not a time"}]


@pytest.mark.parametrize("bad", _BAD_VALUES, ids=repr)
@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_forged_with_values_of_the_wrong_type_is_a_422(case, bad, client, db_session):
    """The token is sealed with the real key and the right signature, so only its contents are wrong. Across the list
    flavour, the ranked-SQL flavour, the grouped species query and the raw structure SQL.

    Kills: a bad value reaching ``>`` in Python (TypeError) or a bound parameter in SQL (a database error): both a 500.
    """
    seeded = seed(case, db_session, client)
    payload = decode(_first_cursor(client, seeded))
    payload["last_values"] = [bad for _ in payload["last_values"]]
    response = client.get(with_params(seeded.url, cursor=encode(payload)))
    assert code_of(response) == "invalid_cursor", (case, bad)


@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_forged_with_the_wrong_number_of_values_or_a_bad_bound_is_a_422(case, client, db_session):
    seeded = seed(case, db_session, client)
    good = decode(_first_cursor(client, seeded))
    forged = [
        {**good, "last_values": good["last_values"] + [1]},
        {**good, "last_values": good["last_values"][:-1] or [1, 2, 3, 4, 5]},
        {**good, "watermark": {**good["watermark"], "max_id": "9"}},
        {**good, "watermark": {**good["watermark"], "max_id": True}},
        {**good, "watermark": {**good["watermark"], "max_id": -1}},
        {**good, "watermark": {**good["watermark"], "max_id": 1.5}},
        {**good, "watermark": {**good["watermark"], "groups": {"a": "b"}}},
    ]
    for payload in forged:
        response = client.get(with_params(seeded.url, cursor=encode(payload)))
        assert code_of(response) == "invalid_cursor", payload["watermark"]


# ---------------------------------------------------------------------------
# an empty cursor page is still a cursor page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [c for c in REVIEWED if c not in DEPRECATION_KEEPS_THE_ROW])
def test_an_empty_cursor_page_still_reports_the_cursor_contract(case, client, db_session):
    """Everything leaves the visible set mid-walk: the next page is empty, and it says it was a cursor page.

    Kills: an ``_empty_response`` path that reports ``mode: offset`` and no snapshot for a request that sent a cursor.
    """
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)
    for record_type, record_id in seeded.review_targets:
        set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.deprecated)

    body = get_page(client, seeded, limit=2, cursor=cursor)

    assert refs_of(seeded, body) == []
    assert body["next_cursor"] is None
    assert body["pagination"]["mode"] == "cursor"
    assert body["watermark"] is not None
    assert body["watermark"]["taken_at"]


# ---------------------------------------------------------------------------
# the snapshot bounds insertions, even while the reader is still in the approved group
# ---------------------------------------------------------------------------


def _insert_thermo(db_session, seeded):
    from tests.services.scientific_read._factories import make_thermo_scalar

    entry = db_session.get(SpeciesEntry, seeded.rows[0].species_entry_id)
    return make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-123.0)


def _insert_calculation(db_session, seeded):
    from app.db.models.common import CalculationType
    from tests.services.scientific_read._factories import make_calculation

    return make_calculation(db_session, type=CalculationType.sp, species_entry_id=seeded.rows[0].species_entry_id)


INSERTERS = {
    "thermo-entry": _insert_thermo,
    "thermo-search": _insert_thermo,
    "analytics-thermo": _insert_thermo,
    "calculations-search": _insert_calculation,
    "analytics-calculations": _insert_calculation,
}


@pytest.mark.parametrize("case", sorted(INSERTERS))
def test_a_record_inserted_after_the_reader_is_outside_the_snapshot(case, client, db_session):
    """The reader has only passed the approved record, so the new not-reviewed one sorts AFTER the reader and a plain
    key cut would deliver it. Only the snapshot bound keeps it out. Python-list flavour (thermo entry and search) and
    ranked-SQL flavour (calculations and the analytics reads).

    Kills: a traversal without its watermark.
    """
    seeded = seed(case, db_session, client)
    before = full_list(client, seeded)
    first = get_page(client, seeded, limit=1)
    assert refs_of(seeded, first) == before[:1]

    new_row = INSERTERS[case](db_session, seeded)
    db_session.flush()

    assert new_row.public_ref in full_list(client, seeded), "the inserted record is not listed: the test is vacuous"
    walked = before[:1] + _walk_from(client, seeded, first, 1)
    assert walked == before
    assert new_row.public_ref not in walked


# ---------------------------------------------------------------------------
# documented limits: review state is not frozen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", REVIEW_LEADS)
def test_approving_a_record_the_reader_has_not_reached_skips_it(case, client, db_session):
    """DOCUMENTED BEHAVIOUR, not a guarantee. ``review_rank`` leads the key, so approving a not-yet-reached record moves
    it ahead of the reader: the traversal does not deliver it, although it is listed. The snapshot bounds insertions,
    not curation. For a set that cannot move, read a published release's frozen artifact.
    """
    seeded = seed(case, db_session, client)
    before = full_list(client, seeded)
    first = get_page(client, seeded, limit=2)
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    victim = before[-1]
    record_type, record_id = seeded.review_targets[row_refs.index(victim)]
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved)

    walked = refs_of(seeded, first) + _walk_from(client, seeded, first, 2)

    assert victim in full_list(client, seeded)
    assert victim not in walked, "the record was delivered: its position did not cross the reader"
    assert len(walked) == len(before) - 1


@pytest.mark.parametrize("case", REVIEW_LEADS)
def test_demoting_a_delivered_record_repeats_it(case, client, db_session):
    """DOCUMENTED BEHAVIOUR. A delivered approved record that goes back to under review sorts after the reader, so the
    traversal delivers it a second time."""
    seeded = seed(case, db_session, client)
    first = get_page(client, seeded, limit=2)
    delivered = refs_of(seeded, first)
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    record_type, record_id = seeded.review_targets[row_refs.index(delivered[0])]
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.under_review)

    walked = delivered + _walk_from(client, seeded, first, 2)

    assert walked.count(delivered[0]) == 2


def test_species_search_redelivers_a_species_whose_entry_is_deprecated(client, db_session):
    """DOCUMENTED BEHAVIOUR. ``species/search`` keeps a species whose entries are all hidden and ranks it by its visible
    entries, so deprecating an entry moves the species to the end of the order instead of removing it. A reader who was
    already given the species is given it again."""
    assert DEPRECATION_KEEPS_THE_ROW == {"species-search"}
    seeded = seed("species-search", db_session, client)
    before = full_list(client, seeded)
    first = get_page(client, seeded, limit=2)
    delivered = refs_of(seeded, first)
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    record_type, record_id = seeded.review_targets[row_refs.index(delivered[0])]
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.deprecated)

    assert sorted(full_list(client, seeded)) == sorted(before), "the species left the listing: the case no longer applies"
    walked = delivered + _walk_from(client, seeded, first, 2)

    assert walked.count(delivered[0]) == 2
    assert sorted(set(walked)) == sorted(before)


def test_a_new_entry_under_an_old_species_moves_that_species_across_the_reader(client, db_session):
    """DOCUMENTED BEHAVIOUR. ``species/browse`` bounds species by the species id, so an entry added to an old species
    is not held back by the snapshot: it changes the species' rank, and the traversal can skip the species."""
    from tests.services.scientific_read._factories import make_species_entry

    seeded = seed("species-browse", db_session, client)
    before = full_list(client, seeded)
    first = get_page(client, seeded, limit=2)
    victim = before[-1]
    species = seeded.rows[(seeded.row_refs or [row.public_ref for row in seeded.rows]).index(victim)]
    entry = make_species_entry(db_session, species, stereo_label="late")
    set_review(db_session, record_type=seeded.review_targets[0][0], record_id=entry.id, status=RecordReviewStatus.approved)

    walked = refs_of(seeded, first) + _walk_from(client, seeded, first, 2)

    assert victim in full_list(client, seeded)
    assert victim not in walked


# ---------------------------------------------------------------------------
# POST twins of the reaction and kinetics searches continue a GET cursor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "path"),
    [("reactions-search", "reactions/search"), ("kinetics-search", "kinetics/search")],
)
def test_the_post_twin_continues_a_get_cursor(case, path, client, db_session):
    assert ROUTE_OF_CASE[case] == f"{API}/{path}"
    seeded = seed(case, db_session, client)
    first = get_page(client, seeded, limit=2)
    get_second = get_page(client, seeded, limit=2, cursor=first["next_cursor"])

    post = client.post(f"{API}/{path}", json={"reactants": ["CC"], "limit": 2, "cursor": first["next_cursor"]})

    assert post.status_code == 200, post.text
    assert refs_of(seeded, post.json()) == refs_of(seeded, get_second)
    assert post.json()["pagination"]["mode"] == "cursor"
    # The same cursor is refused for other filters, and with an offset, on the POST form too.
    other = client.post(f"{API}/{path}", json={"reactants": ["CCC"], "limit": 2, "cursor": first["next_cursor"]})
    assert code_of(other) == "cursor_query_mismatch"
    both = client.post(
        f"{API}/{path}", json={"reactants": ["CC"], "limit": 2, "offset": 1, "cursor": first["next_cursor"]}
    )
    assert code_of(both) == "cursor_offset_conflict"


def test_every_seeded_case_is_still_known():
    """A new seed in ``test_browse_order_echo`` must be given a route here, or ``ROUTE_OF_CASE`` fails first."""
    assert set(CASES) - {c for c in CASES if c.startswith("lookup-")} == set(ROUTE_OF_CASE)
    assert walk  # imported for the sibling module's helpers
