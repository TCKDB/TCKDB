"""Contract tests for the four ``/scientific/analytics/*`` typed methods.

Everything here drives ``httpx.MockTransport`` — no live server. What is
asserted is the client's half of the contract: the path, the verb, that every
filter lands in the query string (these endpoints publish no POST twin), and
that the ``iter_*`` companions traverse by **cursor** rather than by offset.

The keyset assertions are the load-bearing ones. Offset paging over a live
corpus can silently skip or duplicate rows; the whole reason the analytics
surface publishes ``next_cursor`` is so a dataset build does not have to
accept that. An iterator that quietly fell back to offsets would give the
caller the wrong data with no signal, so the fallback paths are pinned too.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client.errors import TCKDBPaginationError

ANALYTICS_METHODS = (
    ("search_kinetics_analytics", "/scientific/analytics/kinetics"),
    ("search_thermo_analytics", "/scientific/analytics/thermo"),
    ("search_statmech_analytics", "/scientific/analytics/statmech"),
    ("search_calculation_analytics", "/scientific/analytics/calculations"),
)

ANALYTICS_ITERATORS = (
    ("iter_kinetics_analytics", "/scientific/analytics/kinetics"),
    ("iter_thermo_analytics", "/scientific/analytics/thermo"),
    ("iter_statmech_analytics", "/scientific/analytics/statmech"),
    ("iter_calculation_analytics", "/scientific/analytics/calculations"),
)


def _page(records, *, next_cursor=None, limit=50, total=None):
    body = {
        "request": {
            "filter": {},
            "sort": "review_rank ASC, id DESC",
            "include": [],
            "pagination_mode": "cursor" if next_cursor else "offset",
            "profile": "exploratory",
            "profile_recommendation": "none",
            "profile_release_ref": None,
        },
        "review_summary": {"total": len(records)},
        "records": list(records),
        "pagination": {
            "offset": 0,
            "limit": limit,
            "returned": len(records),
            "total": len(records) if total is None else total,
        },
        "next_cursor": next_cursor,
        "watermark": {"taken_at": "2026-08-02T00:00:00Z", "release_ref": None},
    }
    return body


def _path_of(url: str) -> str:
    return unquote(urlsplit(url).path)


def _query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _capture(pages):
    """Serve ``pages`` in order, recording every request."""

    seen: list[httpx.Request] = []
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = remaining.pop(0) if remaining else _page([])
        return httpx.Response(200, json=body)

    return handler, seen


# ---------------------------------------------------------------------------
# The four typed methods
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_name,path", ANALYTICS_METHODS)
def test_each_analytics_method_issues_one_get_to_its_own_path(method_name, path):
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    getattr(client, method_name)()

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert _path_of(str(seen[0].url)).endswith(path)
    assert seen[0].content == b""


@pytest.mark.parametrize("method_name,_path", ANALYTICS_METHODS)
def test_paging_knobs_serialize_into_the_query_string(method_name, _path):
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    getattr(client, method_name)(limit=200, offset=100, min_review_status="approved")

    query = _query_of(str(seen[0].url))
    assert query["limit"] == ["200"]
    assert query["offset"] == ["100"]
    assert query["min_review_status"] == ["approved"]


@pytest.mark.parametrize("method_name,_path", ANALYTICS_METHODS)
def test_unset_filters_are_not_sent(method_name, _path):
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    getattr(client, method_name)()

    assert _query_of(str(seen[0].url)) == {}


def test_kinetics_range_and_coverage_filters_are_sent_verbatim():
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    client.search_kinetics_analytics(
        model_kind="arrhenius",
        ea_min_kj_mol=10.0,
        ea_max_kj_mol=250.0,
        temperature_min_k=300.0,
        temperature_max_k=2000.0,
        has_transition_state_provenance=True,
        include=["internal_ids"],
    )

    query = _query_of(str(seen[0].url))
    assert query["model_kind"] == ["arrhenius"]
    assert query["ea_min_kj_mol"] == ["10.0"]
    assert query["ea_max_kj_mol"] == ["250.0"]
    assert query["temperature_min_k"] == ["300.0"]
    assert query["temperature_max_k"] == ["2000.0"]
    assert query["has_transition_state_provenance"] == ["true"]
    assert query["include"] == ["internal_ids"]


def test_thermo_analytics_sends_its_own_numeric_axes():
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    client.search_thermo_analytics(
        phase="gas",
        h298_min_kj_mol=-100.0,
        s298_max_j_mol_k=400.0,
        has_uncertainty=False,
    )

    query = _query_of(str(seen[0].url))
    assert query["phase"] == ["gas"]
    assert query["h298_min_kj_mol"] == ["-100.0"]
    assert query["s298_max_j_mol_k"] == ["400.0"]
    assert query["has_uncertainty"] == ["false"]


def test_statmech_analytics_sends_exact_match_counts_not_ranges():
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    client.search_statmech_analytics(
        external_symmetry=3,
        optical_isomers=1,
        point_group="C3v",
        rotational_constant_a_min_cm1=0.5,
    )

    query = _query_of(str(seen[0].url))
    assert query["external_symmetry"] == ["3"]
    assert query["optical_isomers"] == ["1"]
    assert query["point_group"] == ["C3v"]
    assert query["rotational_constant_a_min_cm1"] == ["0.5"]
    # The backend publishes no range form for these counts, so the client
    # must not invent one.
    assert "external_symmetry_min" not in query
    assert "optical_isomers_max" not in query


def test_calculation_analytics_sends_diagnostics_and_provenance_filters():
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    client.search_calculation_analytics(
        calculation_type="sp",
        t1_max=0.02,
        converged=True,
        method="ccsd(t)",
        basis="cc-pvtz",
        lot_ref="lot_abc",
        software="molpro",
    )

    query = _query_of(str(seen[0].url))
    assert query["calculation_type"] == ["sp"]
    assert query["t1_max"] == ["0.02"]
    assert query["converged"] == ["true"]
    assert query["method"] == ["ccsd(t)"]
    assert query["basis"] == ["cc-pvtz"]
    assert query["lot_ref"] == ["lot_abc"]
    assert query["software"] == ["molpro"]


@pytest.mark.parametrize("method_name,_path", ANALYTICS_METHODS)
def test_the_parsed_envelope_is_handed_back_untouched(method_name, _path):
    page = _page([{"kinetics_ref": "kin_1"}], next_cursor="cur_1")
    handler, _seen = _capture([page])
    client, _ = make_client(handler)

    response = getattr(client, method_name)()

    assert response == page
    assert response["next_cursor"] == "cur_1"
    assert response["watermark"]["taken_at"] == "2026-08-02T00:00:00Z"


@pytest.mark.parametrize("method_name,_path", ANALYTICS_METHODS)
def test_an_explicit_cursor_is_forwarded(method_name, _path):
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    getattr(client, method_name)(cursor="opaque-token")

    assert _query_of(str(seen[0].url))["cursor"] == ["opaque-token"]


# ---------------------------------------------------------------------------
# Keyset iteration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iterator_name,path", ANALYTICS_ITERATORS)
def test_the_iterator_follows_next_cursor_and_never_sends_offset(
    iterator_name, path
):
    pages = [
        _page([{"ref": "a"}, {"ref": "b"}], next_cursor="cur_1", limit=2),
        _page([{"ref": "c"}], next_cursor=None, limit=2),
    ]
    handler, seen = _capture(pages)
    client, _ = make_client(handler)

    records = list(getattr(client, iterator_name)(limit=2))

    assert records == [{"ref": "a"}, {"ref": "b"}, {"ref": "c"}]
    assert len(seen) == 2
    first, second = (_query_of(str(r.url)) for r in seen)
    assert "cursor" not in first
    assert second["cursor"] == ["cur_1"]
    # A cursor plus a non-zero offset is a server-side 422; the client must
    # never build that request itself.
    assert "offset" not in first
    assert "offset" not in second
    assert all(_path_of(str(r.url)).endswith(path) for r in seen)


def test_the_iterator_stops_on_a_null_cursor_without_a_trailing_request():
    handler, seen = _capture([_page([{"ref": "only"}], next_cursor=None)])
    client, _ = make_client(handler)

    assert list(client.iter_kinetics_analytics()) == [{"ref": "only"}]
    assert len(seen) == 1


def test_iterator_filters_are_repeated_unchanged_on_every_page():
    pages = [
        _page([{"ref": "a"}], next_cursor="cur_1", limit=1),
        _page([{"ref": "b"}], next_cursor=None, limit=1),
    ]
    handler, seen = _capture(pages)
    client, _ = make_client(handler)

    list(client.iter_thermo_analytics(phase="gas", limit=1))

    for request in seen:
        assert _query_of(str(request.url))["phase"] == ["gas"]
        assert _query_of(str(request.url))["limit"] == ["1"]


def test_a_repeated_cursor_raises_instead_of_looping_forever():
    pages = [
        _page([{"ref": "a"}], next_cursor="same", limit=1),
        _page([{"ref": "b"}], next_cursor="same", limit=1),
    ]
    handler, _seen = _capture(pages)
    client, _ = make_client(handler)

    with pytest.raises(TCKDBPaginationError, match="repeated a cursor"):
        list(client.iter_statmech_analytics(limit=1))


def test_an_empty_page_carrying_a_cursor_raises():
    handler, _seen = _capture([_page([], next_cursor="cur_1")])
    client, _ = make_client(handler)

    with pytest.raises(TCKDBPaginationError, match="did not advance"):
        list(client.iter_calculation_analytics())


def test_a_page_claiming_more_records_than_its_limit_raises():
    handler, _seen = _capture([_page([{"ref": "a"}, {"ref": "b"}], limit=1)])
    client, _ = make_client(handler)

    with pytest.raises(TCKDBPaginationError, match="server page limit"):
        list(client.iter_kinetics_analytics(limit=1))


def test_a_non_string_cursor_is_rejected_before_any_request():
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    with pytest.raises(TCKDBPaginationError, match="expected a string"):
        list(client.iter_kinetics_analytics(cursor=123))
    assert seen == []


def test_a_caller_supplied_offset_falls_back_to_offset_paging():
    """Keyset cannot express 'start at row 100'; the offset must win."""

    pages = [
        {
            **_page([{"ref": "a"}], next_cursor="cur_1", limit=1, total=101),
            "pagination": {
                "offset": 100,
                "limit": 1,
                "returned": 1,
                "total": 101,
            },
        }
    ]
    handler, seen = _capture(pages)
    client, _ = make_client(handler)

    records = list(client.iter_thermo_analytics(offset=100, limit=1))

    assert records == [{"ref": "a"}]
    query = _query_of(str(seen[0].url))
    assert query["offset"] == ["100"]
    assert "cursor" not in query


def test_a_server_without_next_cursor_falls_back_to_offset_paging():
    """A pre-keyset server must not silently yield only its first page."""

    def offset_page(records, offset, total):
        body = _page(records)
        body.pop("next_cursor")
        body.pop("watermark")
        body["pagination"] = {
            "offset": offset,
            "limit": 1,
            "returned": len(records),
            "total": total,
        }
        return body

    pages = [
        offset_page([{"ref": "a"}], 0, 2),  # probe page, discarded
        offset_page([{"ref": "a"}], 0, 2),
        offset_page([{"ref": "b"}], 1, 2),
    ]
    handler, seen = _capture(pages)
    client, _ = make_client(handler)

    records = list(client.iter_calculation_analytics(limit=1))

    assert records == [{"ref": "a"}, {"ref": "b"}]
    assert len(seen) == 3


def test_a_cursor_page_without_next_cursor_raises_rather_than_truncating():
    first = _page([{"ref": "a"}], next_cursor="cur_1", limit=1)
    second = _page([{"ref": "b"}], limit=1)
    second.pop("next_cursor")
    handler, _seen = _capture([first, second])
    client, _ = make_client(handler)

    with pytest.raises(TCKDBPaginationError, match="no 'next_cursor'"):
        list(client.iter_statmech_analytics(limit=1))


# ---------------------------------------------------------------------------
# Read profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_name,_path", ANALYTICS_METHODS)
def test_profile_is_serialized_onto_the_analytics_request(method_name, _path):
    handler, seen = _capture([_page([])])
    client, _ = make_client(handler)

    getattr(client, method_name)(profile="curated")

    assert _query_of(str(seen[0].url))["profile"] == ["curated"]


@pytest.mark.parametrize("iterator_name,_path", ANALYTICS_ITERATORS)
def test_profile_is_repeated_on_every_keyset_page(iterator_name, _path):
    pages = [
        _page([{"ref": "a"}], next_cursor="cur_1", limit=1),
        _page([{"ref": "b"}], next_cursor=None, limit=1),
    ]
    handler, seen = _capture(pages)
    client, _ = make_client(handler)

    list(getattr(client, iterator_name)(profile="curated", limit=1))

    assert len(seen) == 2
    for request in seen:
        assert _query_of(str(request.url))["profile"] == ["curated"]


def test_the_resolved_profile_echo_is_returned_to_the_caller():
    page = _page([])
    page["request"]["profile"] = "curated"
    page["request"]["profile_recommendation"] = "approved_floor_only"
    handler, _seen = _capture([page])
    client, _ = make_client(handler)

    response = client.search_kinetics_analytics(profile="curated")

    assert response["request"]["profile"] == "curated"
    assert response["request"]["profile_recommendation"] == "approved_floor_only"


def test_release_is_not_a_parameter_on_any_analytics_method():
    """``?release=`` is a 422 on the whole scientific surface (DR-0026)."""

    import inspect

    from tckdb_client import TCKDBClient

    for method_name, _path in ANALYTICS_METHODS:
        parameters = inspect.signature(getattr(TCKDBClient, method_name)).parameters
        assert "release" not in parameters, method_name
