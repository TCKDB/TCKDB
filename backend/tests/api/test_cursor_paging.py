"""Stable (cursor) paging on every browse and list read.

``offset`` is random access and is correct only for a static corpus. A cursor carries the sort key of the last record
delivered plus a snapshot bound, so a traversal does not skip or repeat a record when the corpus moves under it.

Coverage is derived, not listed. The routes come from the app's own OpenAPI document: every operation that takes
``offset`` must either take ``cursor`` (and be walked below) or appear in ``EXCLUDED`` with a reason. A new list route
that forgets the cursor, or a stale exclusion, fails ``test_every_offset_route_is_accounted_for``.

The behavioural tests reuse the seeds of ``test_browse_order_echo`` (five rows per endpoint whose review status,
deposition time and id disagree, two of them deposited at the same instant), so a cursor that mishandled a tie or a
mixed-direction key is caught on every endpoint, not on one.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import pytest

from app.api.app import create_app
from app.api.errors import DomainError
from app.db.models.common import RecordReviewStatus
from app.services.record_review import set_record_review_status
from app.services.scientific_read import keyset
from tests.api.test_browse_order_echo import CASES, Seeded, review

API = "/api/v1/scientific"

#: Offset-taking routes that deliberately have no cursor, and why. Each is checked against the live OpenAPI document.
EXCLUDED: dict[tuple[str, str], str] = {
    ("GET", f"{API}/export/ml/reactions.ndjson"): "a streamed export: the body is NDJSON and has no envelope to carry a cursor",
    ("GET", f"{API}/export/ml/species.ndjson"): "a streamed export: the body is NDJSON and has no envelope to carry a cursor",
    ("GET", "/api/v1/admin/machine-review/curator-tasks"): "an authenticated operations queue, not the browse surface",
    ("GET", "/api/v1/admin/users"): "an authenticated administrative listing, not the browse surface",
    ("GET", "/api/v1/submissions/for-review"): "an authenticated per-user work queue, not the browse surface",
    ("GET", "/api/v1/submissions/mine"): "an authenticated per-user work queue, not the browse surface",
    ("GET", "/api/v1/submissions/{submission_id}/audit-events"): "an authenticated per-submission audit trail",
    ("GET", "/api/v1/submissions/{submission_id}/record-links"): "an authenticated per-submission link list",
}

#: Which route each seeded case exercises (the route template). Verified against the seeded URL when the case runs.
ROUTE_OF_CASE: dict[str, str] = {
    "thermo-entry": f"{API}/species-entries/{{species_entry_id}}/thermo",
    "thermo-search": f"{API}/thermo/search",
    "transport-entry": f"{API}/species-entries/{{species_entry_id}}/transport",
    "transport-search": f"{API}/transport/search",
    "statmech-entry": f"{API}/species-entries/{{species_entry_id}}/statmech",
    "statmech-search": f"{API}/statmech/search",
    "kinetics-entry": f"{API}/reaction-entries/{{reaction_entry_id}}/kinetics",
    "kinetics-search": f"{API}/kinetics/search",
    "reactions-search": f"{API}/reactions/search",
    "reactions-browse": f"{API}/reactions/browse",
    "species-search": f"{API}/species/search",
    "species-browse": f"{API}/species/browse",
    "calculations-search": f"{API}/calculations/search",
    "species-calculations-default": f"{API}/species-calculations/search",
    "species-calculations-latest": f"{API}/species-calculations/search",
    "species-calculations-earliest": f"{API}/species-calculations/search",
    "species-calculations-lowest-energy": f"{API}/species-calculations/search",
    "conformers-search": f"{API}/conformers/search",
    "transition-states-search": f"{API}/transition-states/search",
    "transition-states-browse": f"{API}/transition-states/browse",
    "networks-search": f"{API}/networks/search",
    "networks-browse": f"{API}/networks/browse",
    "network-solves-search": f"{API}/network-solves/search",
    "network-kinetics-search": f"{API}/network-kinetics/search",
    "observations": f"{API}/species-entries/{{species_entry_id}}/observations",
    "artifacts-search": f"{API}/artifacts/search",
    "levels-of-theory-browse": f"{API}/level-of-theories/browse",
    "levels-of-theory-search": f"{API}/level-of-theories/search",
    "frequency-scale-factors-search": f"{API}/frequency-scale-factors/search",
    "energy-correction-schemes-search": f"{API}/energy-correction-schemes/search",
    "structure-search-exact": f"{API}/species/structure-search",
    "structure-search-substructure": f"{API}/species/structure-search",
    "structure-search-similarity": f"{API}/species/structure-search",
    "literature-records": f"{API}/literature/{{literature_ref_or_id}}/records",
    "analytics-thermo": f"{API}/analytics/thermo",
    "analytics-kinetics": f"{API}/analytics/kinetics",
    "analytics-statmech": f"{API}/analytics/statmech",
    "analytics-calculations": f"{API}/analytics/calculations",
}

#: Routes the seeds above do not reach; ``test_the_routes_without_a_seed_page_too`` walks each of them.
UNSEEDED_ROUTES: dict[str, str] = {
    f"{API}/artifacts/integrity": "custody record (curator)",
    f"{API}/artifacts/{{sha256}}/integrity": "custody history of one digest (curator)",
    f"{API}/releases": "release list",
    f"{API}/releases/{{release_handle}}/selections": "a release's selection ledger",
    f"{API}/calculations/{{calculation_ref_or_id}}/scan": "scan points",
    f"{API}/calculations/{{calculation_ref_or_id}}/irc": "IRC points",
    f"{API}/calculations/{{calculation_ref_or_id}}/path-search": "path-search points",
}

#: Seeded cases that are the lookup surface or otherwise not a paged list (none today).
NOT_PAGED_CASES: set[str] = {name for name in CASES if name.startswith("lookup-")}

PAGED_CASES = sorted(name for name in CASES if name not in NOT_PAGED_CASES)

#: Cases with no review state of their own (reference vocabularies, or a list ordered by what it lists).
UNREVIEWED_CASES = {
    "levels-of-theory-browse",
    "levels-of-theory-search",
    "frequency-scale-factors-search",
    "energy-correction-schemes-search",
    "literature-records",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_ACTOR: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _curator(db_session, _api_curator_user):
    """The curator who moves records between review states in these tests (the seeds' own rows are inserted directly)."""
    from app.db.models.app_user import AppUser

    _ACTOR["user"] = db_session.get(AppUser, _api_curator_user)
    yield
    _ACTOR.clear()


def set_review(db_session, *, record_type, record_id, status) -> None:
    """Move a record to ``status`` through the curation service (a review row is never rewritten in place)."""
    try:
        set_record_review_status(
            db_session, record_type=record_type, record_id=record_id, status=status, actor=_ACTOR["user"]
        )
    except DomainError:
        # under_review cannot go straight to deprecated: approve first, as a curator would.
        set_record_review_status(
            db_session,
            record_type=record_type,
            record_id=record_id,
            status=RecordReviewStatus.approved,
            actor=_ACTOR["user"],
        )
        set_record_review_status(
            db_session, record_type=record_type, record_id=record_id, status=status, actor=_ACTOR["user"]
        )
    db_session.flush()


def with_params(url: str, **params: Any) -> str:
    sep = "&" if "?" in url else "?"
    return url + sep + "&".join(f"{k}={v}" for k, v in params.items())


def decode(token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))


def encode(payload: dict, *, digest: bool = True) -> str:
    payload = dict(payload)
    payload.pop("k", None)
    if digest:
        payload["k"] = keyset._digest(payload)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def refs_of(seeded: Seeded, body: dict) -> list[str]:
    return [seeded.ref_of(rec) for rec in body[seeded.records_key]]


def get_page(client, seeded: Seeded, **params) -> dict:
    response = client.get(with_params(seeded.url, **params))
    assert response.status_code == 200, response.text
    return response.json()


def walk(client, seeded: Seeded, limit: int) -> tuple[list[str], list[dict]]:
    """Follow ``next_cursor`` from the first page to the last; returns the refs in order and every page."""
    body = get_page(client, seeded, limit=limit)
    pages = [body]
    refs = refs_of(seeded, body)
    while body["next_cursor"] is not None:
        assert len(pages) < 50, "the traversal does not end"
        body = get_page(client, seeded, limit=limit, cursor=body["next_cursor"])
        pages.append(body)
        refs.extend(refs_of(seeded, body))
    return refs, pages


def full_list(client, seeded: Seeded) -> list[str]:
    return refs_of(seeded, get_page(client, seeded, limit=200))


def seed(case: str, db_session, client) -> Seeded:
    seeded = CASES[case](db_session)
    review(db_session, seeded)
    path = seeded.url.split("?")[0]
    template = re.escape(ROUTE_OF_CASE[case])
    template = re.sub(r"\\\{[^}]+\\\}", "[^/]+", template)
    assert re.fullmatch(template, path), f"{case} seeds {path}, which is not {ROUTE_OF_CASE[case]}"
    return seeded


def code_of(response) -> str:
    assert response.status_code == 422, response.text
    return response.json()["code"]


# ---------------------------------------------------------------------------
# coverage is derived from the app's routes
# ---------------------------------------------------------------------------


def _offset_routes() -> dict[tuple[str, str], bool]:
    """``{(METHOD, path): takes_cursor}`` for every operation in the OpenAPI document that takes ``offset``."""
    out = {}
    for path, ops in create_app().openapi()["paths"].items():
        for method, op in ops.items():
            names = {p["name"] for p in op.get("parameters", [])}
            if "offset" in names:
                out[(method.upper(), path)] = "cursor" in names
    return out


def test_every_offset_route_is_accounted_for():
    """Kills: a list route that takes ``offset`` and was left without ``cursor``, and a stale exclusion."""
    routes = _offset_routes()
    assert len(routes) > 40, f"only {len(routes)} offset routes found: the scan reached too little to prove anything"

    without_cursor = {key for key, has in routes.items() if not has}
    assert without_cursor == set(EXCLUDED), (
        f"routes with offset but no cursor and no reason: {sorted(without_cursor - set(EXCLUDED))}; "
        f"exclusions that no longer apply: {sorted(set(EXCLUDED) - without_cursor)}"
    )

    paged_templates = {path for (method, path), has in routes.items() if has and method == "GET"}
    covered = set(ROUTE_OF_CASE.values()) | set(UNSEEDED_ROUTES)
    assert paged_templates == covered, (
        f"cursor routes with no walking test: {sorted(paged_templates - covered)}; "
        f"tests for routes that do not exist: {sorted(covered - paged_templates)}"
    )
    assert set(CASES) - NOT_PAGED_CASES == set(ROUTE_OF_CASE), "a seeded case has no route recorded"


def test_every_search_request_model_that_pages_by_offset_accepts_a_cursor():
    """The POST twins carry the cursor in the body."""
    schemas = create_app().openapi()["components"]["schemas"]
    models = {name: s for name, s in schemas.items() if "offset" in s.get("properties", {}) and name.endswith("Request")}
    assert len(models) >= 15
    assert [name for name, s in models.items() if "cursor" not in s["properties"]] == []


def test_the_offset_parameter_says_it_is_random_access():
    schema = create_app().openapi()
    seen = 0
    for (method, path), has in _offset_routes().items():
        if not has:
            continue
        for param in schema["paths"][path][method.lower()]["parameters"]:
            if param["name"] == "offset":
                seen += 1
                assert "static corpus" in param.get("description", ""), path
                assert "next_cursor" in param["description"], path
    assert seen > 35


# ---------------------------------------------------------------------------
# a traversal is complete, ordered, and ends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_walk_visits_every_record_once_in_order_and_ends(case, limit, client, db_session):
    """Kills: a cursor that skips or repeats a record (ties on created_at included), a last page that still carries
    a cursor, and a page after the first that is not served as a cursor page."""
    seeded = seed(case, db_session, client)
    expected = full_list(client, seeded)
    assert len(expected) == 5, f"the seed lists {len(expected)} of 5 rows: the walk below would prove nothing"

    refs, pages = walk(client, seeded, limit)

    assert refs == expected
    assert len(refs) == len(set(refs))
    assert pages[0]["pagination"]["mode"] == "offset"
    assert all(page["pagination"]["mode"] == "cursor" for page in pages[1:])
    assert pages[-1]["next_cursor"] is None
    assert all(page["next_cursor"] is not None for page in pages[:-1])
    assert len(pages) == -(-5 // limit)
    # The set a traversal walks does not change under it.
    assert {page["pagination"]["total"] for page in pages} == {5}


@pytest.mark.parametrize("case", PAGED_CASES)
def test_an_exact_final_page_has_no_next_cursor(case, client, db_session):
    """Kills: a cursor offered after the last record (a client would fetch an empty page)."""
    seeded = seed(case, db_session, client)
    body = get_page(client, seeded, limit=5)
    assert len(body[seeded.records_key]) == 5
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# the defect: the corpus moves under a reader
# ---------------------------------------------------------------------------


def _order_and_review_target(seeded: Seeded, refs: list[str]) -> tuple[int, Any]:
    """Index (in the seed) of the row served first, and its review target."""
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    index = row_refs.index(refs[0])
    return index, seeded.review_targets[index]


#: Cases where deprecating a record does not remove it from the listing. A species is an identity record with no review
#: status of its own: ``species/search`` keeps a species whose entries are all hidden, and ranks it by its visible entries,
#: so an entry's deprecation moves the species to the end instead of removing it. That is the documented limit of the
#: snapshot (curation is not frozen), and it is why the tests below skip this case.
DEPRECATION_KEEPS_THE_ROW = {"species-search"}


@pytest.mark.parametrize("case", [c for c in PAGED_CASES if c not in UNREVIEWED_CASES])
def test_a_record_leaving_the_visible_set_mid_traversal_does_not_make_a_cursor_skip(case, client, db_session):
    """The documented behaviour for a deprecation: a record already delivered stays delivered, a record not yet
    delivered that is deprecated drops out, and nothing else moves. Offset paging does worse: the same event shifts every
    later row up by one, and the reader silently skips a record.

    Kills: a cursor built from a row count instead of a sort key.
    """
    if case in DEPRECATION_KEEPS_THE_ROW:
        pytest.skip("deprecating a record does not remove it from this listing; see DEPRECATION_KEEPS_THE_ROW")
    seeded = seed(case, db_session, client)
    before = full_list(client, seeded)
    first_page = get_page(client, seeded, limit=2)
    delivered = refs_of(seeded, first_page)

    # Deprecate a record the reader has ALREADY been given.
    index, (record_type, record_id) = _order_and_review_target(seeded, delivered)
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.deprecated)
    after = full_list(client, seeded)

    rest = []
    body = first_page
    while body["next_cursor"] is not None:
        body = get_page(client, seeded, limit=2, cursor=body["next_cursor"])
        rest.extend(refs_of(seeded, body))
    assert delivered + rest == before, "a cursor walk lost or repeated a record when an earlier one was deprecated"

    assert after == [r for r in before if r != delivered[0]], "the seed's deprecation did not leave the listing"
    # The same event under offset paging: page 2 starts one row late.
    offset_page_two = refs_of(seeded, get_page(client, seeded, limit=2, offset=2))
    assert offset_page_two != before[2:4], "offset paging did not skip: the check above proves nothing"
    assert before[2] not in offset_page_two


@pytest.mark.parametrize("case", [c for c in PAGED_CASES if c not in UNREVIEWED_CASES])
def test_a_record_deprecated_before_it_is_reached_drops_out_without_a_gap(case, client, db_session):
    """The other half of the documented behaviour: curation is not frozen by the snapshot."""
    seeded = seed(case, db_session, client)
    before = full_list(client, seeded)
    if case in DEPRECATION_KEEPS_THE_ROW:
        pytest.skip("deprecating a record does not remove it from this listing")
    first_page = get_page(client, seeded, limit=2)
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    victim = before[-1]
    record_type, record_id = seeded.review_targets[row_refs.index(victim)]
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.deprecated)

    refs = refs_of(seeded, first_page)
    body = first_page
    while body["next_cursor"] is not None:
        body = get_page(client, seeded, limit=2, cursor=body["next_cursor"])
        refs.extend(refs_of(seeded, body))
    assert refs == before[:-1]


# ---------------------------------------------------------------------------
# a cursor is refused when it is not this request's
# ---------------------------------------------------------------------------


def _first_cursor(client, seeded: Seeded) -> str:
    cursor = get_page(client, seeded, limit=2)["next_cursor"]
    assert cursor, "the first page of five has no cursor"
    return cursor


def _flip(token: str) -> str:
    i = len(token) // 2
    return token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1 :]


@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_tampered_cursor_is_a_422(case, client, db_session):
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)
    for bad in (_flip(cursor), cursor[:-6], "not-a-cursor", encode({"v": keyset.CURSOR_VERSION}, digest=False)):
        response = client.get(with_params(seeded.url, cursor=bad))
        assert code_of(response) == "invalid_cursor", bad
    # An edited position with a stale digest is as bad as a corrupt token.
    payload = decode(cursor)
    payload["last_values"] = [0 for _ in payload["last_values"]]
    forged = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode()
    assert code_of(client.get(with_params(seeded.url, cursor=forged.rstrip("=")))) == "invalid_cursor"


@pytest.mark.parametrize("case", PAGED_CASES)
def test_an_old_or_mistyped_version_is_a_422(case, client, db_session):
    """The version is compared for exact equality: ``true``, ``3.0`` and ``"3"`` are not 3."""
    seeded = seed(case, db_session, client)
    payload = decode(_first_cursor(client, seeded))
    assert payload["v"] == keyset.CURSOR_VERSION
    for version in (keyset.CURSOR_VERSION - 1, 1, True, float(keyset.CURSOR_VERSION), str(keyset.CURSOR_VERSION), None, [3]):
        token = encode({**payload, "v": version})
        response = client.get(with_params(seeded.url, cursor=token))
        assert code_of(response) == "invalid_cursor", repr(version)
    # Control: the same token with the right version is accepted, so the refusals above are about the version.
    assert client.get(with_params(seeded.url, cursor=encode(payload))).status_code == 200


@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_from_another_endpoint_is_a_422(case, client, db_session):
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)
    other = f"{API}/releases" if ROUTE_OF_CASE[case] != f"{API}/releases" else f"{API}/analytics/thermo"
    response = client.get(with_params(other, cursor=cursor))
    assert code_of(response) == "cursor_query_mismatch"
    # And the other way: a cursor that really belongs to a different route's query, replayed here.
    foreign = encode({**decode(cursor), "query_signature": keyset.signature_for(other, {})})
    assert code_of(client.get(with_params(seeded.url, cursor=foreign))) == "cursor_query_mismatch"


@pytest.mark.parametrize("case", PAGED_CASES)
def test_a_cursor_replayed_with_other_filters_or_profile_is_a_422(case, client, db_session):
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)

    response = client.get(with_params(seeded.url, cursor=cursor, profile="curated"))
    assert code_of(response) == "cursor_query_mismatch"

    route = ROUTE_OF_CASE[case]
    params = {p["name"] for p in create_app().openapi()["paths"][route]["get"]["parameters"]}
    changed = [
        ("include_deprecated", "true"),
        ("include_rejected", "true"),
        ("min_review_status", "approved"),
    ]
    tried = 0
    for name, value in changed:
        if name in params:
            tried += 1
            response = client.get(with_params(seeded.url, cursor=cursor, **{name: value}))
            assert code_of(response) == "cursor_query_mismatch", name
    if case not in UNREVIEWED_CASES:
        assert tried, f"{case} takes no review filter to change: the check above covers only the profile"

    # Control: only the paging knobs may change between pages (the page size and ``include``).
    assert client.get(with_params(seeded.url, cursor=cursor, limit=1)).status_code == 200


@pytest.mark.parametrize("case", PAGED_CASES)
def test_offset_and_cursor_together_are_a_422(case, client, db_session):
    seeded = seed(case, db_session, client)
    cursor = _first_cursor(client, seeded)
    response = client.get(with_params(seeded.url, cursor=cursor, offset=1))
    assert code_of(response) == "cursor_offset_conflict"
    # offset=0 is no offset at all.
    assert client.get(with_params(seeded.url, cursor=cursor, offset=0)).status_code == 200


# ---------------------------------------------------------------------------
# hidden == unknown == absent, page by page
# ---------------------------------------------------------------------------


def _cursor_numbers(payload: dict) -> list[int]:
    """Every integer a cursor holds: its sort key and its snapshot bounds."""
    found = []

    def visit(node):
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit({k: v for k, v in payload.items() if k not in ("k", "v", "query_signature")})
    return found


#: What each case's snapshot bound is the highest id of, when that is not the seeded row's own id.
BOUND_ID_OF_ROW = {
    "artifacts-search": lambda row: row.id,
}


@pytest.mark.parametrize(
    "case", [c for c in PAGED_CASES if c not in UNREVIEWED_CASES and c not in DEPRECATION_KEEPS_THE_ROW]
)
def test_a_curated_cursor_holds_no_key_of_a_hidden_record(case, client, db_session):
    """Under ``profile=curated`` a record below the approved floor is unknown. Nothing that names it may reach a cursor:
    not the snapshot bound (the highest id the profile shows, not the table's), and not a sort value.

    Kills: a bound taken over the unfloored table, and a key taken from a row the profile withholds.
    """
    seeded = seed(case, db_session, client)
    # Approve the four older rows; leave the newest (largest id) not reviewed: hidden under curated.
    hidden = seeded.rows[4]
    approved = seeded.rows[:4]
    for i in range(len(approved)):
        record_type, record_id = seeded.review_targets[i]
        set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved)
    # The newest row must really be hidden under curated and shown otherwise.
    exploratory = full_list(client, seeded)
    curated_body = get_page(client, seeded, limit=1, profile="curated")
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    hidden_ref = row_refs[4]
    assert hidden_ref in exploratory
    curated_all = refs_of(seeded, get_page(client, seeded, limit=200, profile="curated"))
    assert hidden_ref not in curated_all, "the newest row is shown under curated: the check below is vacuous"
    assert len(curated_all) >= 2

    exploratory_cursor = decode(get_page(client, seeded, limit=1)["next_cursor"])
    curated_cursor = decode(curated_body["next_cursor"])

    visible_ids = [row.id for row in approved]
    hidden_id = hidden.id
    assert hidden_id > max(visible_ids)
    assert hidden_id in _cursor_numbers(exploratory_cursor), "the check cannot see a row id in a cursor"
    assert hidden_id not in _cursor_numbers(curated_cursor)
    bound = curated_cursor["watermark"]["max_id"]
    assert bound == max(visible_ids)

    # Walking the whole curated traversal never mentions the hidden record anywhere.
    body = curated_body
    while body["next_cursor"] is not None:
        assert hidden_id not in _cursor_numbers(decode(body["next_cursor"]))
        body = get_page(client, seeded, limit=1, profile="curated", cursor=body["next_cursor"])
        assert hidden_ref not in refs_of(seeded, body)
    # The response never tells how many records the profile withheld.
    assert curated_body["pagination"]["total"] == len(curated_all)


@pytest.mark.parametrize("case", [c for c in PAGED_CASES if c not in UNREVIEWED_CASES])
def test_the_hidden_by_default_header_is_unchanged_on_a_cursor_page(case, client, db_session):
    """Deprecated records stay hidden by default on every page, and the header keeps saying so."""
    seeded = seed(case, db_session, client)
    if case in DEPRECATION_KEEPS_THE_ROW:
        pytest.skip("deprecating a record does not remove it from this listing")
    record_type, record_id = seeded.review_targets[4]
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.deprecated)
    first = client.get(with_params(seeded.url, limit=2))
    second = client.get(with_params(seeded.url, limit=2, cursor=first.json()["next_cursor"]))
    assert second.status_code == 200
    assert first.headers.get("X-TCKDB-Hidden-By-Default") == second.headers.get("X-TCKDB-Hidden-By-Default")


# ---------------------------------------------------------------------------
# the engine, directly
# ---------------------------------------------------------------------------


def test_a_record_inserted_above_the_readers_position_is_not_seen_and_does_not_shift_the_next_page():
    """The core claim, on the engine: a cursor cuts the page from a sort key, so an insertion above the reader changes
    neither what comes next nor what repeats. Offset paging over the same list repeats a record."""
    def paging(cursor=None, offset=0, limit=2):
        return keyset.Paging("/t", {"cursor": cursor, "f": 1}, offset=offset, limit=limit)

    def key(r):
        return (-r, )

    items = [50, 40, 30, 20, 10]  # ids; newest first
    first = paging()
    page1 = first.window(first.snapshot(items, id_of=lambda r: r), key_of=key)
    assert page1.items == [50, 40]
    assert page1.next_cursor is not None

    items = [60, *items]  # an upload that sorts above the reader
    offset_page2 = paging(offset=2).window(items, id_of=lambda r: r, key_of=key)
    assert offset_page2.items == [40, 30], "offset paging repeats 40: the defect this module exists to remove"

    second = paging(cursor=page1.next_cursor)
    page2 = second.window(second.snapshot(items, id_of=lambda r: r), key_of=key)
    assert page2.items == [30, 20]
    third = paging(cursor=page2.next_cursor)
    page3 = third.window(third.snapshot(items, id_of=lambda r: r), key_of=key)
    assert page3.items == [10]
    assert page3.next_cursor is None
    assert 60 not in page2.items + page3.items


def test_a_cursor_resumes_across_a_tie_in_every_key_but_the_last():
    def key(r):
        return (r[0], -r[1])

    items = [(1, 5), (1, 4), (1, 3), (2, 2), (2, 1)]  # (created, id): the first three tie on created
    seen = []
    token = None
    for _ in range(len(items) + 1):  # a cursor that does not advance must fail, not loop
        p = keyset.Paging("/t", {"cursor": token}, offset=0, limit=1)
        page = p.window(p.snapshot(items, id_of=lambda r: r[1]), key_of=key)
        seen.extend(page.items)
        token = page.next_cursor
        if token is None:
            break
    else:
        pytest.fail("the traversal did not end")
    assert seen == sorted(items, key=key)


def test_a_watermark_is_bound_per_table_when_a_list_mixes_tables():
    """Ids of different tables are not comparable: statmech 3 is newer than the snapshot although 3 < thermo's 9."""
    rows = [("thermo", 9), ("thermo", 8), ("statmech", 2), ("statmech", 1)]

    def key(r):
        return (r[0], r[1])

    p = keyset.Paging("/m", {}, offset=0, limit=1)
    page1 = p.window(p.snapshot(rows, id_of=lambda r: r[1], group_of=lambda r: r[0]), key_of=key)
    assert page1.items == [("statmech", 1)]
    grown = [*rows, ("statmech", 3), ("thermo", 10)]
    q = keyset.Paging("/m", {"cursor": page1.next_cursor}, offset=0, limit=10)
    page2 = q.window(q.snapshot(grown, id_of=lambda r: r[1], group_of=lambda r: r[0]), key_of=key)
    assert page2.items == [("statmech", 2), ("thermo", 8), ("thermo", 9)]


# ---------------------------------------------------------------------------
# POST twins share the cursor
# ---------------------------------------------------------------------------


def test_the_post_twin_continues_a_cursor_issued_by_the_get_form(client, db_session):
    seeded = seed("thermo-search", db_session, client)
    get_first = get_page(client, seeded, limit=2)
    get_second = get_page(client, seeded, limit=2, cursor=get_first["next_cursor"])
    post = client.post(f"{API}/thermo/search", json={"smiles": "C=C", "limit": 2, "cursor": get_first["next_cursor"]})
    assert post.status_code == 200, post.text
    assert refs_of(seeded, post.json()) == refs_of(seeded, get_second)
    bad = client.post(f"{API}/thermo/search", json={"smiles": "C=C", "limit": 2, "cursor": "nope"})
    assert code_of(bad) == "invalid_cursor"
    both = client.post(
        f"{API}/thermo/search", json={"smiles": "C=C", "limit": 2, "offset": 1, "cursor": get_first["next_cursor"]}
    )
    assert code_of(both) == "cursor_offset_conflict"


# ---------------------------------------------------------------------------
# the engine paths, with a record inserted above the reader
# ---------------------------------------------------------------------------


def _insert_thermo(db_session, seeded: Seeded):
    from app.db.models.species import SpeciesEntry
    from tests.services.scientific_read._factories import make_thermo_scalar

    entry = db_session.get(SpeciesEntry, seeded.rows[0].species_entry_id)
    return make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)


def test_an_insertion_above_the_reader_repeats_a_record_under_offset_and_not_under_a_cursor(client, db_session):
    """Reproduces the defect on a Python-list read (thermo of one entry): a new, approved, newest record sorts first."""
    from app.db.models.common import SubmissionRecordType

    seeded = seed("thermo-entry", db_session, client)
    before = full_list(client, seeded)
    first = get_page(client, seeded, limit=2)
    delivered = refs_of(seeded, first)

    new_row = _insert_thermo(db_session, seeded)
    set_review(
        db_session, record_type=SubmissionRecordType.thermo, record_id=new_row.id, status=RecordReviewStatus.approved
    )

    offset_second = refs_of(seeded, get_page(client, seeded, limit=2, offset=2))
    assert set(offset_second) & set(delivered), "offset paging did not repeat a record: the reproduction is vacuous"

    walked = list(delivered)
    body = first
    while body["next_cursor"] is not None:
        body = get_page(client, seeded, limit=2, cursor=body["next_cursor"])
        walked.extend(refs_of(seeded, body))
    assert walked == before
    assert new_row.public_ref not in walked
    assert new_row.public_ref in full_list(client, seeded)
