"""GET /api/v1/scientific/species/name-search: secure, bounded, alphabetical lookup of species by name."""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import event, func, select, text
from starlette.requests import Request

from app.api.config import settings
from app.api.rate_limit import _classify
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesNameKind,
    SpeciesNameSourceKind,
    SubmissionRecordType,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.species_name import SpeciesName
from app.services.public_refs import PREFIXES
from app.services.species_name_normalisation import normalise_name
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

URL = "/api/v1/scientific/species/name-search"


def _species(db_session, tag="NS", *, entry=True, review=None):
    sp = make_species(db_session, inchi_key=next_inchi_key(tag))
    if entry:
        e = make_species_entry(db_session, sp)
        if review is not None:
            set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=e.id, status=review)
    return sp


def _name(db_session, species, name, *, kind=SpeciesNameKind.common, retired=False, key=None):
    row = SpeciesName(
        species_id=species.id,
        name=name,
        name_key=key if key is not None else normalise_name(name),
        kind=kind,
        source_kind=SpeciesNameSourceKind.curated,
    )
    if retired:
        row.retired_at = func.now()
    db_session.add(row)
    db_session.flush()
    return row


def _get(client, q, **params):
    return client.get(URL, params={"q": q, **params})


def _names(response):
    return [m["name"] for m in response.json()["matches"]]


# --- matching -------------------------------------------------------------------------------------------------------


def test_exact_match_normalises_the_query(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "Strasse acid")
    body = _get(client, "  STRASSE\tAcid ").json()
    assert [m["species_ref"] for m in body["matches"]] == [sp.public_ref]
    match = body["matches"][0]
    assert match["name"] == "Strasse acid" and match["kind"] == "common" and match["source_kind"] == "curated"
    assert match["smiles"] == sp.smiles and "formula" in match
    assert body["request"]["query_key"] == "strasse acid"
    assert body["truncated"] is False


def test_exact_does_not_match_a_longer_name(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "water vapour")
    assert _names(_get(client, "water")) == []
    assert _names(_get(client, "water", match="prefix")) == ["water vapour"]


def test_prefix_range_includes_the_prefix_and_excludes_the_upper_bound(client, db_session):
    a, b, c, d = (_species(db_session, t) for t in ("PA", "PB", "PC", "PD"))
    _name(db_session, a, "wat")  # equal to q: in
    _name(db_session, b, "water")  # starts with q: in
    _name(db_session, c, "wau")  # equals q_next: out
    _name(db_session, d, "wasp")  # below q: out
    assert _names(_get(client, "wat", match="prefix")) == ["wat", "water"]


def test_prefix_range_with_a_high_code_point(client, db_session):
    a, b = _species(db_session, "HA"), _species(db_session, "HB")
    _name(db_session, a, "ab\u6c34")
    _name(db_session, b, "ac")  # equals q_next of "ab"... 'ac'
    assert _names(_get(client, "ab", match="prefix")) == ["ab\u6c34"]


def test_curly_apostrophe_query_finds_straight_apostrophe_name(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "N'-methyl amine")
    assert _names(_get(client, "N\u2019-methyl amine")) == ["N'-methyl amine"]


def test_the_same_name_on_two_species_returns_both(client, db_session):
    a, b = _species(db_session, "BA"), _species(db_session, "BB")
    _name(db_session, a, "butene")
    _name(db_session, b, "butene")
    refs = [m["species_ref"] for m in _get(client, "butene").json()["matches"]]
    assert refs == sorted([a.public_ref, b.public_ref])


def test_order_is_alphabetical_by_key_then_ref_and_says_so(client, db_session):
    x, y, z = (_species(db_session, t) for t in ("OA", "OB", "OC"))
    _name(db_session, x, "zinc oxide")
    _name(db_session, y, "zinc chloride")
    _name(db_session, z, "zinc bromide")
    body = _get(client, "zinc", match="prefix").json()
    assert [m["name"] for m in body["matches"]] == ["zinc bromide", "zinc chloride", "zinc oxide"]
    assert body["order"]["kind"] == "display_order"
    assert [k["key"] for k in body["order"]["keys"]] == ["name_key", "species_ref"]
    assert "alphabetical" in body["order"]["statement"].lower() and "not a ranking" in body["order"]["statement"]


def test_two_names_of_one_species_are_two_matches(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "ethanol")
    _name(db_session, sp, "ethyl alcohol")
    assert _names(_get(client, "eth", match="prefix")) == ["ethanol", "ethyl alcohol"]


# --- bounds ---------------------------------------------------------------------------------------------------------


def test_limit_caps_and_reports_truncated(client, db_session):
    for i in range(5):
        _name(db_session, _species(db_session, f"L{i}"), f"alkane {i}")
    body = _get(client, "alkane", match="prefix", limit=3).json()
    assert len(body["matches"]) == 3 and body["truncated"] is True
    body = _get(client, "alkane", match="prefix", limit=5).json()
    assert len(body["matches"]) == 5 and body["truncated"] is False


@pytest.mark.parametrize("limit", [0, 21, -1])
def test_limit_out_of_range_is_refused(client, limit):
    assert _get(client, "water", limit=limit).status_code == 422


def test_limit_20_is_allowed_and_default_is_10(client, db_session):
    for i in range(12):
        _name(db_session, _species(db_session, f"D{i}"), f"ketone {i:02d}")
    assert len(_get(client, "ketone", match="prefix").json()["matches"]) == 10
    assert len(_get(client, "ketone", match="prefix", limit=20).json()["matches"]) == 12


# --- input rules ----------------------------------------------------------------------------------------------------

_BAD = [
    "a",
    "x" * 65,
    "x" * 5000,
    "100%",
    "wa_er",
    "wa\\ter",
    "water;--",
    'wa"ter',
    "water' OR '1'='1;",
    "wa\x00ter",
    "wa\x07ter",
    "wa\x1cter",
    "wa\nter",
    "wa\u200bter",
    "wa<ter>",
    "wa*ter",
    "wa/ter",
    "wa=ter",
    "wa&ter",
    "wa|ter",
    "wa$ter",
    "wa{ter}",
]


@pytest.mark.parametrize("q", _BAD)
def test_bad_queries_are_422_with_the_catalogued_code_and_no_echo(client, q):
    response = _get(client, q)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "species_name_query_invalid"
    payload = response.text
    for fragment in (q.strip(), q[:8]):
        if len(fragment) > 3 and fragment.isprintable():
            assert fragment not in payload


def test_raw_length_is_checked_before_normalising(client):
    assert _get(client, " " * 300 + "ab").status_code == 422
    assert _get(client, " " * 20 + "ab").status_code == 200


def test_ideographic_zero_is_not_a_digit(client):
    assert _get(client, "ab" + chr(0x3007)).status_code == 422


def test_missing_query_is_422(client):
    assert client.get(URL).status_code == 422


def test_a_64_character_name_is_accepted_and_65_is_not(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "x" * 64)
    assert _names(_get(client, "x" * 64)) == ["x" * 64]
    assert _get(client, "x" * 65).status_code == 422


@pytest.mark.parametrize(
    "q", ["water", "Ethan-1,2-diol", "o'brien", "n(2)-[x]+.:", "\u00e9thane", "\u6c34", "\u0438\u0437\u043e", "ab\u00a0cd"]
)
def test_allowed_queries_are_accepted(client, q):
    if q == "\u6c34":
        q = "\u6c34\u6c34"
    assert _get(client, q).status_code == 200


def test_injection_text_stored_as_a_name_is_matched_literally(client, db_session):
    a, b = _species(db_session, "IA"), _species(db_session, "IB")
    _name(db_session, a, "it's (weird)")
    _name(db_session, b, "itsxweird")
    assert _names(_get(client, "it's (weird)")) == ["it's (weird)"]
    # '%' and '_' are refused outright, never read as patterns
    assert _get(client, "it%").status_code == 422
    assert _get(client, "it_").status_code == 422


def test_mixed_scripts_do_not_fail(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "water \u6c34")
    assert _names(_get(client, "water \u6c34")) == ["water \u6c34"]


# --- visibility, profile, retirement, merges ------------------------------------------------------------------------


def test_retired_names_are_not_found(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "old name", retired=True)
    _name(db_session, sp, "new name")
    assert _names(_get(client, "old name")) == []
    assert _names(_get(client, "name", match="prefix")) == []
    assert _names(_get(client, "new name")) == ["new name"]


def test_a_retired_name_can_be_added_again(db_session):
    sp = _species(db_session)
    _name(db_session, sp, "again", retired=True)
    _name(db_session, sp, "again")  # unique only over live rows


def test_a_live_duplicate_name_is_refused_by_the_database(db_session):
    from sqlalchemy.exc import IntegrityError

    sp = _species(db_session)
    _name(db_session, sp, "dup")
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _name(db_session, sp, "DUP")


def test_rejected_and_deprecated_species_are_hidden_by_default(client, db_session):
    ok, bad, old = _species(db_session, "VA"), _species(db_session, "VB"), _species(db_session, "VC")
    for sp, status in ((bad, RecordReviewStatus.rejected), (old, RecordReviewStatus.deprecated)):
        entry = db_session.scalar(select(SpeciesEntry).where(SpeciesEntry.species_id == sp.id))
        set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=status)
    for sp in (ok, bad, old):
        _name(db_session, sp, "shared")
    assert [m["species_ref"] for m in _get(client, "shared").json()["matches"]] == [ok.public_ref]


def test_a_species_with_no_entry_is_not_listed(client, db_session):
    sp = _species(db_session, entry=False)
    _name(db_session, sp, "bare")
    assert _names(_get(client, "bare")) == []


def test_curated_profile_keeps_only_approved_species(client, db_session):
    approved = _species(db_session, "CA", review=RecordReviewStatus.approved)
    plain = _species(db_session, "CB")
    under = _species(db_session, "CC", review=RecordReviewStatus.under_review)
    for sp in (approved, plain, under):
        _name(db_session, sp, "curated test")
    assert len(_get(client, "curated test").json()["matches"]) == 3
    body = _get(client, "curated test", profile="curated").json()
    assert [m["species_ref"] for m in body["matches"]] == [approved.public_ref]
    assert body["request"]["profile"] == "curated"


def test_a_merged_species_forwards_to_the_survivor_once(client, db_session):
    survivor = _species(db_session, "MS")
    loser = Species(
        kind=survivor.kind,
        smiles=survivor.smiles + "(C)",
        inchi_key=next_inchi_key("ML"),
        charge=0,
        multiplicity=1,
        stereo_kind=survivor.stereo_kind,
    )
    db_session.add(loser)
    db_session.flush()
    _name(db_session, loser, "merged name")
    # The name predates the merge: a merge does not move it.
    db_session.execute(text("UPDATE species SET merged_into_species_id = :s WHERE id = :l"), {"s": survivor.id, "l": loser.id})
    body = _get(client, "merged name").json()
    assert [m["species_ref"] for m in body["matches"]] == [survivor.public_ref]
    # The request names no ref, so nothing is reported as forwarded.
    assert body["request"]["merged_refs"] == {} and body["request"]["hidden_by_default"] is None
    # the same name on both rows is one match
    _name(db_session, survivor, "merged name")
    assert [m["species_ref"] for m in _get(client, "merged name").json()["matches"]] == [survivor.public_ref]


def test_a_name_cannot_be_attached_to_a_merged_species(db_session):
    from sqlalchemy.exc import DBAPIError

    survivor = _species(db_session, "MT")
    loser = Species(
        kind=survivor.kind,
        smiles=survivor.smiles + "(C)",
        inchi_key=next_inchi_key("MU"),
        charge=0,
        multiplicity=1,
        stereo_kind=survivor.stereo_kind,
        merged_into_species_id=survivor.id,
    )
    db_session.add(loser)
    db_session.flush()
    with pytest.raises(DBAPIError, match="merged-away"), db_session.begin_nested():
        _name(db_session, loser, "late name")


def test_the_response_carries_no_internal_ids(client, db_session):
    sp = _species(db_session)
    _name(db_session, sp, "plain")
    body = _get(client, "plain").json()

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    assert not [k for k in keys(body) if k == "id" or k.endswith("_id") or k.endswith("_ids")]


def test_the_name_ref_prefix_is_registered():
    assert PREFIXES["SpeciesName"] == "snm"


def test_a_name_gets_a_public_ref(db_session):
    row = _name(db_session, _species(db_session), "reffed")
    assert row.public_ref.startswith("snm_") and len(row.public_ref) <= 40


# --- statement timeout and no network -------------------------------------------------------------------------------


def test_the_read_sets_a_local_statement_timeout(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "name_search_statement_timeout_ms", 321)
    _name(db_session, _species(db_session), "timed")
    seen = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db_session.connection().engine
    event.listen(engine, "before_cursor_execute", _before)
    try:
        assert _get(client, "timed").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    assert "SET LOCAL statement_timeout = 321" in seen
    assert seen.index("SET LOCAL statement_timeout = 321") < next(
        i for i, s in enumerate(seen) if "species_name" in s and s.lstrip().upper().startswith("SELECT")
    )


def test_the_session_limit_is_restored_after_the_read(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "name_search_statement_timeout_ms", 321)
    before = db_session.scalar(text("SELECT current_setting('statement_timeout')"))
    assert _get(client, "restored").status_code == 200
    assert db_session.scalar(text("SELECT current_setting('statement_timeout')")) == before


def test_a_timeout_is_the_catalogued_query_timeout_not_a_500(client, db_session, monkeypatch):
    from app.services.scientific_read import species_name_search as module

    _name(db_session, _species(db_session), "slow")
    monkeypatch.setattr(settings, "name_search_statement_timeout_ms", 50)
    original = module._statement

    def slow(**kwargs):
        return original(**kwargs).where(text("length(pg_sleep(1)::text) >= 0"))

    monkeypatch.setattr(module, "_statement", slow)
    response = _get(client, "slow")
    assert response.status_code == 503
    assert response.json()["code"] == "query_timeout"


def test_the_session_is_usable_and_the_limit_is_back_after_a_timeout(client, db_session, monkeypatch):
    from app.services.scientific_read import species_name_search as module

    _name(db_session, _species(db_session), "slow again")
    before = db_session.scalar(text("SELECT current_setting('statement_timeout')"))
    monkeypatch.setattr(settings, "name_search_statement_timeout_ms", 50)
    original = module._statement
    monkeypatch.setattr(module, "_statement", lambda **kw: original(**kw).where(text("length(pg_sleep(1)::text) >= 0")))
    assert _get(client, "slow again").status_code == 503
    assert db_session.scalar(text("SELECT 1")) == 1
    assert db_session.scalar(text("SELECT current_setting('statement_timeout')")) == before


def test_a_tighter_session_limit_is_never_loosened(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "name_search_statement_timeout_ms", 5000)
    _name(db_session, _species(db_session), "tight")
    db_session.execute(text("SET LOCAL statement_timeout = 700"))
    seen = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db_session.connection().engine
    event.listen(engine, "before_cursor_execute", _before)
    try:
        assert _get(client, "tight").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    assert "SET LOCAL statement_timeout = 700" in seen and "SET LOCAL statement_timeout = 5000" not in seen


def test_the_read_makes_no_network_connection(client, db_session, monkeypatch):
    sp = _species(db_session)
    _name(db_session, sp, "offline")
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostbyname", refuse)
    # The test session already holds its database connection; the read must not open another socket.
    assert _names(_get(client, "offline")) == ["offline"]
    assert _names(_get(client, "off", match="prefix")) == ["offline"]
    assert attempts == []


# --- rate limit bucket ----------------------------------------------------------------------------------------------


def _classified(path, *, api_key=None, method="GET", query=b"q=water"):
    headers = [(b"x-api-key", api_key.encode())] if api_key else []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers,
        "client": ("203.0.113.9", 4000),
    }
    return _classify(Request(scope))


def test_name_search_has_its_own_bucket_before_the_generic_read_branch():
    bucket, limit, window, identity = _classified(URL)
    assert (bucket, limit, window, identity) == ("name_search", settings.rate_limit_name_search_anon_per_minute, 60, "203.0.113.9")
    bucket, limit, window, identity = _classified(URL, api_key="k")
    assert bucket == "name_search" and limit == settings.rate_limit_name_search_auth_per_minute
    assert identity.startswith("apikey:")
    assert (settings.rate_limit_name_search_anon_per_minute, settings.rate_limit_name_search_auth_per_minute) == (30, 120)
    # The IP ceiling never cuts a credentialed caller below their own limit.
    assert settings.rate_limit_name_search_ip_per_minute >= settings.rate_limit_name_search_auth_per_minute


def test_neighbouring_species_reads_keep_their_buckets():
    assert _classified("/api/v1/scientific/species/search")[0] == "anon_read"
    assert _classified("/api/v1/scientific/species/browse", api_key="k")[0] == "auth_read"
    assert _classified(URL + "/", query=b"")[0] == "anon_read"
    assert _classified(URL, method="POST")[0] != "name_search"


def test_the_bucket_throttles_without_spending_the_general_read_allowance(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_name_search_anon_per_minute", 2)
    from app.api.rate_limit import reset_rate_limit_store

    reset_rate_limit_store()
    assert [_get(client, "water").status_code for _ in range(3)] == [200, 200, 429]
    blocked = _get(client, "water")
    assert blocked.json()["bucket"] == "name_search"
    assert client.get("/api/v1/scientific/species/browse").status_code == 200


def test_rotating_api_keys_do_not_beat_the_ip_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_name_search_ip_per_minute", 3)
    monkeypatch.setattr(settings, "rate_limit_name_search_auth_per_minute", 100)
    from app.api.rate_limit import reset_rate_limit_store

    reset_rate_limit_store()
    codes = [client.get(URL, params={"q": "water"}, headers={"x-api-key": f"made-up-{i}"}).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_expired_windows_are_evicted():
    from app.api import rate_limit

    store = rate_limit._RateLimitStore()
    for i in range(50):
        store.hit(bucket="b", identity=f"ip{i}", limit=5, window_seconds=1)
    assert store.size() == 50
    store._next_sweep = 0.0
    rate_limit.time.sleep(1.1)
    store.hit(bucket="b", identity="fresh", limit=5, window_seconds=1)
    assert store.size() == 1


def test_one_credential_keeps_its_own_limit_under_the_ip_ceiling(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_name_search_auth_per_minute", 4)
    monkeypatch.setattr(settings, "rate_limit_name_search_ip_per_minute", 4)
    from app.api.rate_limit import reset_rate_limit_store

    reset_rate_limit_store()
    codes = [client.get(URL, params={"q": "water"}, headers={"x-api-key": "one-key"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 429]
