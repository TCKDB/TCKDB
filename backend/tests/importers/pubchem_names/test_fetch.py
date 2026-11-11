"""PubChem name fetch: response parsing, skip rules, throttle, timeout and retry. No network: recorded responses only."""

from __future__ import annotations

import http.client
import io
import itertools
import json
import os
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from app.importers.pubchem_names import fetch as fetch_mod
from app.importers.pubchem_names.fetch import (
    REVIEW_FORMAT,
    LookupResult,
    PubChemClient,
    build_review,
    fetch_to_file,
    parse_property_response,
)
from app.importers.pubchem_names.species_list import fetch_species_list

FIXTURES = Path(__file__).parent / "fixtures"
METHANE = "VNWKTOKETHGBQD-UHFFFAOYSA-N"
AMMONIA = "QGZKDVFQNNGYKY-UHFFFAOYSA-N"
WATER = "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
UNKNOWN = "AAAAAAAAAAAAAA-AAAAAAAAAA-N"


def _recorded(name: str, status: int = 200) -> LookupResult:
    return parse_property_response(status, (FIXTURES / name).read_text())


def _lookup_from(table: dict[str, LookupResult]):
    return lambda key: table[key]


def _props(*rows) -> LookupResult:
    return LookupResult("ok", list(rows))


def _sp(ref: str, key: str, charge: int = 0, smiles: str | None = None) -> dict:
    item = {"species_ref": ref, "inchi_key": key, "charge": charge}
    if smiles is not None:
        item["smiles"] = smiles
    return item


def _unit(cid, title, iupac, *, charge=0, units=1) -> dict:
    return {"CID": cid, "Title": title, "IUPACName": iupac, "Charge": charge, "CovalentUnitCount": units}


def test_a_match_gives_a_common_and_a_systematic_row_with_the_cid_url_and_retrieval_time():
    review = build_review(
        [_sp("spc_a", AMMONIA)], _lookup_from({AMMONIA: _recorded("ammonia.json")}), now=lambda: "2026-11-11T00:00:00Z"
    )
    assert review["format"] == REVIEW_FORMAT
    assert review["skipped"] == []
    common = {"species_ref": "spc_a", "inchi_key": AMMONIA, "cid": 222, "retrieved_at": "2026-11-11T00:00:00Z",
              "url": "https://pubchem.ncbi.nlm.nih.gov/compound/222"}
    assert review["rows"] == [
        {**common, "name": "Ammonia", "kind": "common"},
        {**common, "name": "azane", "kind": "systematic"},
    ]


def test_a_title_equal_to_the_systematic_name_is_listed_once_as_common():
    review = build_review([_sp("spc_m", METHANE)], _lookup_from({METHANE: _recorded("methane.json")}))
    assert [(r["name"], r["kind"]) for r in review["rows"]] == [("Methane", "common")]


def test_no_match_is_skipped_with_the_reason():
    review = build_review([_sp("spc_x", UNKNOWN)], _lookup_from({UNKNOWN: _recorded("not_found.json", 404)}))
    assert review["rows"] == []
    assert review["skipped"] == [{"species_refs": ["spc_x"], "inchi_key": UNKNOWN, "reason": "no_pubchem_match"}]


def test_water_is_accepted_because_the_other_cid_has_two_covalent_units():
    review = build_review([_sp("spc_w", WATER)], _lookup_from({WATER: _recorded("water.json")}))
    assert review["skipped"] == []
    assert {(r["name"], r["cid"]) for r in review["rows"]} == {("Water", 962), ("oxidane", 962)}


def test_tautomers_with_one_unit_each_are_still_skipped_as_several_cids():
    result = _recorded("tautomers_synthetic.json")
    review = build_review([_sp("spc_p", METHANE)], _lookup_from({METHANE: result}))
    assert review["rows"] == []
    assert (review["skipped"][0]["reason"], review["skipped"][0]["cids"]) == ("several_cids", [8871, 5280522])


def test_a_cid_of_another_charge_or_without_the_counts_is_not_a_candidate():
    result = _props(
        _unit(1, "Wrong charge", "wrong charge", charge=1),
        {"CID": 2, "Title": "No counts", "IUPACName": "no counts"},
        _unit(3, "Right one", "right one"),
    )
    review = build_review([_sp("spc_c", METHANE)], _lookup_from({METHANE: result}))
    assert {r["cid"] for r in review["rows"]} == {3}
    none_left = build_review([_sp("spc_c", METHANE, charge=2)], _lookup_from({METHANE: result}))
    assert none_left["skipped"][0]["reason"] == "no_matching_cid"


def test_names_come_from_the_accepted_cids_row_not_the_first_row():
    result = _props(_unit(10, "Salt form", "salt form", units=2), _unit(11, "Plain", "plain thing"))
    review = build_review([_sp("spc_n", METHANE)], _lookup_from({METHANE: result}))
    assert {(r["name"], r["cid"]) for r in review["rows"]} == {("Plain", 11), ("plain thing", 11)}


def test_a_boolean_is_not_a_cid():
    result = _props({"CID": True, "Title": "x1", "IUPACName": "x2", "Charge": 0, "CovalentUnitCount": 1})
    review = build_review([_sp("spc_b", METHANE)], _lookup_from({METHANE: result}))
    assert review["rows"] == [] and review["skipped"][0]["reason"] == "no_matching_cid"


def test_a_multi_component_smiles_is_skipped_without_a_request():
    asked: list[str] = []
    review = build_review([_sp("spc_i", METHANE, smiles="[Na+].[Cl-]")], lambda k: asked.append(k) or _props())
    assert asked == [] and review["skipped"][0]["reason"] == "smiles_multi_component"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("x" * 129, "name_length"),
        ("bad<name>", "name_characters"),
        ("ab\x00c", "name_characters"),
        ("zero​width", "name_characters"),
        ("---", "name_no_letter_or_digit"),
    ],
)
def test_a_name_failing_the_normaliser_is_skipped_but_the_other_kind_is_kept(name, reason):
    result = _props(_unit(5, name, "fine name"))
    review = build_review([_sp("spc_n", METHANE)], _lookup_from({METHANE: result}))
    assert [(r["name"], r["kind"]) for r in review["rows"]] == [("fine name", "systematic")]
    assert [(s["reason"], s["kind"]) for s in review["skipped"]] == [(reason, "common")]


def test_a_shared_inchikey_skips_every_species_and_asks_pubchem_nothing():
    asked: list[str] = []

    def lookup(key):
        asked.append(key)
        return _recorded("methane.json")

    review = build_review([_sp("spc_s", METHANE), _sp("spc_t", METHANE), _sp("spc_u", AMMONIA)], lookup)
    assert asked == [AMMONIA]
    assert review["skipped"][0]["reason"] == "inchikey_shared_by_species"
    assert review["skipped"][0]["species_refs"] == ["spc_s", "spc_t"]
    assert {r["species_ref"] for r in review["rows"]} == {"spc_u"}


def test_a_repeated_species_row_is_one_species():
    review = build_review([_sp("spc_a", AMMONIA), _sp("spc_a", AMMONIA)], _lookup_from({AMMONIA: _recorded("ammonia.json")}))
    assert len(review["rows"]) == 2 and review["skipped"] == []


def test_a_failed_lookup_and_a_bad_input_row_are_reported():
    review = build_review(
        [_sp("spc_a", AMMONIA), {"species_ref": "spc_b", "inchi_key": "not a key"}],
        _lookup_from({AMMONIA: LookupResult("error", detail="timeout")}),
    )
    assert [s["reason"] for s in review["skipped"]] == ["bad_input_row", "lookup_failed"]


# --- the client ---------------------------------------------------------------------------------------------------


class _Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Clock:
    """A fake clock whose sleep advances it, so throttling is measured, not waited for."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(opener, clock: _Clock, **kw) -> PubChemClient:
    return PubChemClient(opener=opener, sleep=clock.sleep, clock=clock.time, **kw)


def test_requests_carry_only_the_inchikey_and_a_plain_user_agent_and_are_throttled_to_four_a_second():
    seen = []
    clock = _Clock()

    def opener(request, timeout):
        seen.append((request, timeout, clock.now))
        return _Response((FIXTURES / "methane.json").read_bytes())

    client = _client(opener, clock)
    for _ in range(5):
        assert client.lookup(METHANE).status == "ok"
    request, timeout, _ = seen[0]
    assert request.get_method() == "GET" and request.data is None
    assert request.full_url == (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{METHANE}/property/Title,IUPACName,Charge,CovalentUnitCount/JSON"
    )
    assert dict(request.header_items()) == {"User-agent": "tckdb-name-import"}
    assert timeout == 10
    starts = [t for _, _, t in seen]
    assert all(b - a >= 0.25 - 1e-9 for a, b in itertools.pairwise(starts))
    assert starts[-1] - starts[0] == pytest.approx(1.0)  # 5 requests span at least 4 intervals


def test_a_key_that_is_not_an_inchikey_is_never_sent():
    def opener(request, timeout):  # pragma: no cover - must not run
        raise AssertionError("sent")

    assert _client(opener, _Clock()).lookup("../etc/passwd").status == "error"


def test_503_is_retried_a_bounded_number_of_times_then_reported():
    calls = []
    clock = _Clock()

    def opener(request, timeout):
        calls.append(clock.now)
        assert len(calls) < 20, "retries are not bounded"
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)

    client = _client(opener, clock, max_retries=2)
    result = client.lookup(METHANE)
    assert (result.status, result.detail) == ("error", "http_503")
    assert len(calls) == 3  # one try and two retries, no more


def test_503_then_success_returns_the_success():
    clock = _Clock()
    answers = iter([urllib.error.HTTPError("u", 503, "busy", {}, None), _Response((FIXTURES / "methane.json").read_bytes())])

    def opener(request, timeout):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert _client(opener, clock).lookup(METHANE).status == "ok"


def test_a_timeout_is_reported_and_not_retried():
    calls = []

    def opener(request, timeout):
        calls.append(timeout)
        raise socket.timeout("slow")

    result = _client(opener, _Clock()).lookup(METHANE)
    assert (result.status, result.detail, calls) == ("error", "timeout", [10])


def test_404_from_the_http_layer_is_no_match():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, None)

    assert _client(opener, _Clock()).lookup(METHANE).status == "not_found"


def test_a_malformed_response_is_an_error_not_a_crash():
    assert parse_property_response(200, "not json").status == "error"
    assert parse_property_response(200, json.dumps({"PropertyTable": {}})).status == "error"
    assert parse_property_response(500, "").detail == "http_500"


# --- the species list ---------------------------------------------------------------------------------------------


def test_the_species_list_pages_through_the_browse_with_get_only():
    pages = [
        {"records": [{"species_ref": f"spc_{i}", "inchi_key": METHANE, "formula": "CH4", "charge": 0, "multiplicity": 1}
                     for i in range(2)], "pagination": {"total": 3}},
        {"records": [{"species_ref": "spc_2", "inchi_key": AMMONIA, "formula": "H3N", "charge": 0, "multiplicity": 1}],
         "pagination": {"total": 3}},
    ]
    urls = []

    def opener(request, timeout):
        urls.append((request.get_method(), request.full_url, request.data))
        return _Response(json.dumps(pages[len(urls) - 1]).encode())

    document = fetch_species_list("https://tckdb.example.org/", opener=opener, sleep=lambda s: None)
    assert [s["species_ref"] for s in document["species"]] == ["spc_0", "spc_1", "spc_2"]
    assert all(m == "GET" and d is None for m, _, d in urls)
    assert urls[0][1].startswith("https://tckdb.example.org/api/v1/scientific/species/browse?")
    assert "offset=2" in urls[1][1]


def test_the_species_list_refuses_a_non_http_base_url():
    with pytest.raises(ValueError):
        fetch_species_list("file:///etc/passwd")


def test_a_browse_record_missing_a_key_is_a_reported_skip():
    page = {
        "records": [
            {"species_ref": "spc_ok", "inchi_key": METHANE, "canonical_smiles": "C", "charge": 0, "multiplicity": 1},
            {"species_ref": "spc_bad", "inchi_key": METHANE},
            "not a record",
        ],
        "pagination": {"total": 3},
    }
    document = fetch_species_list(
        "https://tckdb.example.org", opener=lambda request, timeout: _Response(json.dumps(page).encode()), sleep=lambda s: None
    )
    assert [s["species_ref"] for s in document["species"]] == ["spc_ok"]
    assert document["species"][0]["smiles"] == "C"
    assert document["skipped"] == [
        {"species_refs": ["spc_bad"], "reason": "bad_browse_record"},
        {"species_refs": [], "reason": "bad_browse_record"},
    ]


# --- failures that must not end the run ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), http.client.BadStatusLine("x"), RecursionError()]
)
def test_transport_failures_are_reported_as_a_failed_lookup(error):
    def opener(request, timeout):
        raise error

    result = _client(opener, _Clock()).lookup(METHANE)
    assert (result.status, result.detail) == ("error", "network")


def test_a_response_over_the_size_bound_is_a_failed_lookup_and_is_not_read_whole():
    reads = []

    class Big(_Response):
        def read(self, size=-1):
            reads.append(size)
            return b"x" * (size if size >= 0 else 10**7)

    result = _client(lambda request, timeout: Big(b""), _Clock(), max_bytes=1000).lookup(METHANE)
    assert (result.status, result.detail) == ("error", "response_too_large")
    assert reads == [1001]


def test_a_deeply_nested_response_is_an_error_not_a_crash():
    assert parse_property_response(200, "[" * 100000 + "]" * 100000).status == "error"


def test_a_properties_value_that_is_not_a_list_is_an_error_not_no_match():
    body = json.dumps({"PropertyTable": {"Properties": {"CID": 1}}})
    assert parse_property_response(200, body).status == "error"
    assert parse_property_response(200, json.dumps({"PropertyTable": {"Properties": []}})).status == "not_found"


def test_the_review_file_keeps_what_was_fetched_when_a_late_lookup_crashes(tmp_path):
    keys = [AMMONIA, METHANE, "AAAAAAAAAAAAAA-AAAAAAAAAB-N", "AAAAAAAAAAAAAA-AAAAAAAAAC-N"]
    calls = []

    class Client:
        requests_made = 0

        def lookup(self, key):
            calls.append(key)
            if len(calls) == 4:
                raise KeyboardInterrupt
            return _recorded("ammonia.json") if key == AMMONIA else LookupResult("not_found")

    out = tmp_path / "review.json"
    species = [_sp(f"spc_{i}", k) for i, k in enumerate(keys)]
    with pytest.raises(KeyboardInterrupt):
        fetch_to_file(species, out, Client(), earlier_skipped=[{"reason": "bad_browse_record"}], checkpoint_every=2)
    saved = json.loads(out.read_text())
    assert saved["format"] == REVIEW_FORMAT
    assert {r["name"] for r in saved["rows"]} == {"Ammonia", "azane"}
    assert [s["reason"] for s in saved["skipped"]] == ["bad_browse_record", "no_pubchem_match"]
    assert not (tmp_path / "review.json.part").exists()


def test_the_final_review_file_is_written_whole(tmp_path):
    class Client:
        requests_made = 0

        def lookup(self, key):
            return _recorded("methane.json")

    out = tmp_path / "review.json"
    review = fetch_to_file([_sp("spc_m", METHANE)], out, Client())
    assert json.loads(out.read_text()) == review and review["rows"][0]["name"] == "Methane"


# --- name search never reaches PubChem ----------------------------------------------------------------------------

_PROBE = """
import sys
from app.api.app import create_app
create_app()
import app
bad = sorted(m for m in sys.modules if "pubchem" in m.lower() or m.endswith("species_name_import"))
print("ROUTE:" + str("app.api.routes.scientific.species_name_search" in sys.modules))
print("ROOT:" + app.__file__)
print("MODULES:" + ",".join(bad))
"""


def test_building_the_api_loads_neither_the_pubchem_modules_nor_the_name_import():
    backend = Path(fetch_mod.__file__).resolve().parents[3]
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=backend,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(backend), *(p for p in sys.path if p)])},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    lines = dict(line.split(":", 1) for line in proc.stdout.strip().splitlines() if ":" in line)
    # The probe must have built the same code, with the name-search route loaded, or it proves nothing.
    assert Path(lines["ROOT"]).resolve().is_relative_to(backend.resolve())
    assert lines["ROUTE"] == "True"
    assert lines["MODULES"] == ""
