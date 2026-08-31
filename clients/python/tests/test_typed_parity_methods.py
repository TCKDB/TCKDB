"""Contract tests for the typed methods added for OpenAPI parity.

Every test drives ``httpx.MockTransport`` — no live server, ever. What is
asserted is the client's half of the contract: the path it builds, the
HTTP verb, where parameters land (query string vs JSON body), and that
the parsed response is handed back untouched.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client.client import JOB_ENDPOINTS, TERMINAL_JOB_STATUSES


def _envelope(records=None) -> dict:
    return {
        "request": {"filter": {}, "sort": ""},
        "review_summary": {"total": 0},
        "records": records if records is not None else [],
        "pagination": {
            "offset": 0,
            "limit": 50,
            "returned": len(records or []),
            "total": len(records or []),
            "post_collapse_total": len(records or []),
        },
    }


def _detail(record: dict) -> dict:
    return {"request": {}, "review_summary": {"total": 0}, "record": record}


def _ok(body) -> httpx.Response:
    return httpx.Response(200, json=body)


def _path_of(url: str) -> str:
    return unquote(urlsplit(url).path)


def _query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _capture(body):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(body, (bytes, str)):
            return httpx.Response(200, content=body)
        return _ok(body)

    return handler, seen


# ---------------------------------------------------------------------------
# Structure search
# ---------------------------------------------------------------------------


class TestStructureSearch:
    def test_posts_query_in_the_body_by_default(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_species_structures(
            query_smarts="[CX4H3]", mode="substructure", limit=10
        )

        assert seen[0].method == "POST"
        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/species/structure-search"
        )
        body = json.loads(seen[0].content)
        assert body == {
            "query_smarts": "[CX4H3]",
            "mode": "substructure",
            "limit": 10,
        }

    def test_get_form_serializes_to_the_query_string(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_species_structures(
            query_smiles="CCO",
            mode="similarity",
            similarity_threshold=0.7,
            method_http="GET",
        )

        assert seen[0].method == "GET"
        query = _query_of(str(seen[0].url))
        assert query["query_smiles"] == ["CCO"]
        assert query["mode"] == ["similarity"]
        assert query["similarity_threshold"] == ["0.7"]

    def test_iterator_walks_pages_and_yields_records(self):
        pages = [
            {
                **_envelope([{"species_ref": "sp_a"}]),
                "pagination": {
                    "offset": 0, "limit": 1, "returned": 1,
                    "total": 2, "post_collapse_total": 2,
                },
            },
            {
                **_envelope([{"species_ref": "sp_b"}]),
                "pagination": {
                    "offset": 1, "limit": 1, "returned": 1,
                    "total": 2, "post_collapse_total": 2,
                },
            },
        ]
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _ok(pages[len(calls) - 1])

        client, _ = make_client(handler)
        records = list(client.iter_species_structures(query_smiles="CCO", limit=1))

        assert [r["species_ref"] for r in records] == ["sp_a", "sp_b"]
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Conformers
# ---------------------------------------------------------------------------


class TestConformers:
    def test_search_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_conformers(species_entry_ref="spe_x", has_freq=True)

        assert seen[0].method == "POST"
        assert _path_of(str(seen[0].url)).endswith("/scientific/conformers/search")
        assert json.loads(seen[0].content) == {
            "species_entry_ref": "spe_x",
            "has_freq": True,
        }

    def test_search_forwards_evidence_match(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_conformers(
            species_entry_ref="spe_x",
            has_freq=True,
            evidence_match="all_observations",
        )

        assert json.loads(seen[0].content) == {
            "species_entry_ref": "spe_x",
            "has_freq": True,
            "evidence_match": "all_observations",
        }

    def test_search_omits_evidence_match_when_not_supplied(self):
        """Omitted, not defaulted client-side.

        The server owns the default (``any_observation``); a client that
        spelled it out would freeze today's default into every request.
        """
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_conformers(species_entry_ref="spe_x", has_freq=True)

        assert "evidence_match" not in json.loads(seen[0].content)

    def test_search_get_form(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_conformers(species_ref="sp_x", method_http="GET")

        assert seen[0].method == "GET"
        assert _query_of(str(seen[0].url))["species_ref"] == ["sp_x"]

    def test_iter_conformers_yields_records(self):
        handler, _ = _capture(_envelope([{"conformer_group": {"ref": "cgrp_1"}}]))
        client, _ = make_client(handler)

        records = list(client.iter_conformers(species_ref="sp_x"))

        assert records == [{"conformer_group": {"ref": "cgrp_1"}}]

    def test_group_detail_builds_ref_path_and_include(self):
        handler, seen = _capture(_detail({"conformer_group": {}}))
        client, _ = make_client(handler)

        result = client.get_conformer_group(
            "cgrp_abc", include=["observations", "selections"]
        )

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/conformer-groups/cgrp_abc"
        )
        assert _query_of(str(seen[0].url))["include"] == [
            "observations",
            "selections",
        ]
        assert result["record"] == {"conformer_group": {}}

    def test_group_detail_accepts_an_integer_id(self):
        handler, seen = _capture(_detail({"conformer_group": {}}))
        client, _ = make_client(handler)

        client.get_conformer_group(42)

        assert _path_of(str(seen[0].url)).endswith("/scientific/conformer-groups/42")

    def test_observation_detail_path(self):
        handler, seen = _capture(_detail({"conformer_observation": {}}))
        client, _ = make_client(handler)

        client.get_conformer_observation("cobs_9")

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/conformer-observations/cobs_9"
        )


# ---------------------------------------------------------------------------
# Transition states
# ---------------------------------------------------------------------------


class TestTransitionStates:
    def test_search_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_transition_states(reaction_entry_ref="rxe_1", has_irc=True)

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/transition-states/search"
        )
        assert json.loads(seen[0].content) == {
            "reaction_entry_ref": "rxe_1",
            "has_irc": True,
        }

    def test_search_get_form_drops_none_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_transition_states(multiplicity=2, method_http="GET")

        query = _query_of(str(seen[0].url))
        assert query["multiplicity"] == ["2"]
        assert "reaction_ref" not in query

    def test_iterator_yields_entry_records(self):
        handler, _ = _capture(
            _envelope([{"transition_state_entry": {"ref": "tse_1"}}])
        )
        client, _ = make_client(handler)

        records = list(client.iter_transition_states(reaction_ref="rxn_1"))

        assert records[0]["transition_state_entry"]["ref"] == "tse_1"

    def test_detail_paths(self):
        handler, seen = _capture(_detail({"transition_state": {}}))
        client, _ = make_client(handler)

        client.get_transition_state("ts_1")
        client.get_transition_state_entry("tse_1", include=["calculations"])

        assert _path_of(str(seen[0].url)).endswith("/scientific/transition-states/ts_1")
        assert _path_of(str(seen[1].url)).endswith(
            "/scientific/transition-state-entries/tse_1"
        )
        assert _query_of(str(seen[1].url))["include"] == ["calculations"]


# ---------------------------------------------------------------------------
# Reference libraries
# ---------------------------------------------------------------------------


class TestEnergyCorrectionSchemes:
    def test_search_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_energy_correction_schemes(
            scheme_kind="bac", used_by_thermo=True
        )

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/energy-correction-schemes/search"
        )
        assert json.loads(seen[0].content) == {
            "scheme_kind": "bac",
            "used_by_thermo": True,
        }

    def test_iterator_and_detail(self):
        handler, seen = _capture(_envelope([{"energy_correction_scheme": {}}]))
        client, _ = make_client(handler)

        records = list(client.iter_energy_correction_schemes(name="Petersson"))
        assert len(records) == 1

        handler2, seen2 = _capture(_detail({"energy_correction_scheme": {}}))
        client2, _ = make_client(handler2)
        client2.get_energy_correction_scheme("ecs_1", include=["corrections"])
        assert _path_of(str(seen2[0].url)).endswith(
            "/scientific/energy-correction-schemes/ecs_1"
        )


class TestFrequencyScaleFactors:
    def test_search_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_frequency_scale_factors(value_min=0.9, value_max=1.0)

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/frequency-scale-factors/search"
        )
        assert json.loads(seen[0].content) == {"value_min": 0.9, "value_max": 1.0}

    def test_get_form_and_detail(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)
        client.search_frequency_scale_factors(
            scale_kind="zpe", method_http="GET"
        )
        assert _query_of(str(seen[0].url))["scale_kind"] == ["zpe"]

        handler2, seen2 = _capture(_detail({"frequency_scale_factor": {}}))
        client2, _ = make_client(handler2)
        client2.get_frequency_scale_factor("fsf_3")
        assert _path_of(str(seen2[0].url)).endswith(
            "/scientific/frequency-scale-factors/fsf_3"
        )

    def test_iterator_yields_records(self):
        handler, _ = _capture(_envelope([{"frequency_scale_factor": {}}]))
        client, _ = make_client(handler)
        assert len(list(client.iter_frequency_scale_factors(method="b3lyp"))) == 1


# ---------------------------------------------------------------------------
# Literature
# ---------------------------------------------------------------------------


class TestLiterature:
    def test_detail_path_and_include(self):
        handler, seen = _capture(_detail({"literature": {}}))
        client, _ = make_client(handler)

        client.get_literature("lit_1", include=["authors"])

        assert _path_of(str(seen[0].url)).endswith("/scientific/literature/lit_1")
        assert _query_of(str(seen[0].url))["include"] == ["authors"]

    def test_records_path_and_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.get_literature_records("lit_1", record_type="kinetics", limit=5)

        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/literature/lit_1/records"
        )
        query = _query_of(str(seen[0].url))
        assert query["record_type"] == ["kinetics"]
        assert query["limit"] == ["5"]

    def test_iterator_keeps_the_literature_ref_fixed_across_pages(self):
        pages = [
            {
                **_envelope([{"record_ref": "kin_1"}]),
                "pagination": {
                    "offset": 0, "limit": 1, "returned": 1,
                    "total": 2, "post_collapse_total": 2,
                },
            },
            {
                **_envelope([{"record_ref": "kin_2"}]),
                "pagination": {
                    "offset": 1, "limit": 1, "returned": 1,
                    "total": 2, "post_collapse_total": 2,
                },
            },
        ]
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok(pages[len(seen) - 1])

        client, _ = make_client(handler)
        records = list(client.iter_literature_records("lit_7", limit=1))

        assert [r["record_ref"] for r in records] == ["kin_1", "kin_2"]
        assert all(
            _path_of(str(r.url)).endswith("/scientific/literature/lit_7/records")
            for r in seen
        )


# ---------------------------------------------------------------------------
# Calculation point-series reads
# ---------------------------------------------------------------------------


class TestCalculationPointSeries:
    @pytest.mark.parametrize(
        "method_name,suffix",
        [
            ("get_calculation_irc", "/irc"),
            ("get_calculation_scan", "/scan"),
            ("get_calculation_path_search", "/path-search"),
        ],
    )
    def test_path_and_parameters(self, method_name, suffix):
        handler, seen = _capture({"points": [], "pagination": {}})
        client, _ = make_client(handler)

        getattr(client, method_name)(
            "calc_1", include_geometries=True, offset=10, limit=100
        )

        url = str(seen[0].url)
        assert _path_of(url).endswith(f"/scientific/calculations/calc_1{suffix}")
        query = _query_of(url)
        assert query["include_geometries"] == ["true"]
        assert query["offset"] == ["10"]
        assert query["limit"] == ["100"]

    def test_returns_the_parsed_point_envelope(self):
        body = {
            "request": {},
            "calculation": {"calculation_ref": "calc_1"},
            "owner": {"kind": "transition_state_entry"},
            "irc": {"direction": "both"},
            "points": [{"point_index": 0, "is_ts": True}],
            "pagination": {"offset": 0, "limit": 50, "returned": 1, "total": 1},
        }
        handler, _ = _capture(body)
        client, _ = make_client(handler)

        result = client.get_calculation_irc("calc_1")

        assert result["points"][0]["is_ts"] is True
        assert result["owner"]["kind"] == "transition_state_entry"


# ---------------------------------------------------------------------------
# Artifacts, exports, vocabularies
# ---------------------------------------------------------------------------


class TestArtifactDownload:
    def test_returns_raw_bytes(self):
        digest = "a" * 64
        handler, seen = _capture(b"\x00binary-output-file\n")
        client, _ = make_client(handler)

        content = client.download_artifact(digest)

        assert content == b"\x00binary-output-file\n"
        assert _path_of(str(seen[0].url)).endswith(
            f"/scientific/artifacts/{digest}/download"
        )
        assert seen[0].headers["accept"] == "application/octet-stream"

    def test_rejects_an_empty_digest(self):
        client, _ = make_client(lambda request: _ok({}))
        with pytest.raises(ValueError, match="sha256"):
            client.download_artifact("   ")

    def test_http_errors_still_map_to_exceptions(self):
        from tckdb_client.errors import TCKDBForbiddenError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"code": "artifact_not_approved"})

        client, _ = make_client(handler)
        with pytest.raises(TCKDBForbiddenError) as excinfo:
            client.download_artifact("b" * 64)
        assert excinfo.value.code == "artifact_not_approved"


class TestArtifactSearch:
    def test_search_artifacts_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_artifacts(artifact_kind="output", calculation_ref="calc_1")

        assert _path_of(str(seen[0].url)).endswith("/scientific/artifacts/search")
        assert json.loads(seen[0].content) == {
            "artifact_kind": "output",
            "calculation_ref": "calc_1",
        }

    def test_iter_artifacts_yields_records(self):
        handler, _ = _capture(_envelope([{"artifact": {"sha256": "c" * 64}}]))
        client, _ = make_client(handler)
        assert len(list(client.iter_artifacts(artifact_kind="output"))) == 1


class TestStatmechAndTransportSearch:
    def test_search_statmech_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_statmech(species_entry_ref="spe_1", has_torsions=True)

        assert _path_of(str(seen[0].url)).endswith("/scientific/statmech/search")
        assert json.loads(seen[0].content) == {
            "species_entry_ref": "spe_1",
            "has_torsions": True,
        }

    def test_search_statmech_get_form(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)
        client.search_statmech(species_ref="sp_1", method_http="GET")
        assert _query_of(str(seen[0].url))["species_ref"] == ["sp_1"]

    def test_iter_statmech_yields_records(self):
        handler, _ = _capture(_envelope([{"statmech": {}}]))
        client, _ = make_client(handler)
        assert len(list(client.iter_statmech(species_ref="sp_1"))) == 1

    def test_search_transport_posts_filters(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)

        client.search_transport(species_entry_ref="spe_1", has_lj_parameters=True)

        assert _path_of(str(seen[0].url)).endswith("/scientific/transport/search")
        assert json.loads(seen[0].content) == {
            "species_entry_ref": "spe_1",
            "has_lj_parameters": True,
        }

    def test_search_transport_get_form(self):
        handler, seen = _capture(_envelope())
        client, _ = make_client(handler)
        client.search_transport(species_ref="sp_1", method_http="GET")
        assert _query_of(str(seen[0].url))["species_ref"] == ["sp_1"]

    def test_iter_transport_yields_records(self):
        handler, _ = _capture(_envelope([{"transport": {}}]))
        client, _ = make_client(handler)
        assert len(list(client.iter_transport(species_ref="sp_1"))) == 1


class TestExports:
    def test_export_ndjson_parses_one_object_per_line(self):
        payload = '{"a": 1}\n\n{"a": 2}\n'
        handler, seen = _capture(payload)
        client, _ = make_client(handler)

        rows = list(client.export_ndjson(reaction_ref=["rxe_1", "rxe_2"]))

        assert rows == [{"a": 1}, {"a": 2}]
        assert _path_of(str(seen[0].url)).endswith("/scientific/export/ndjson")
        assert _query_of(str(seen[0].url))["reaction_ref"] == ["rxe_1", "rxe_2"]

    def test_export_ml_species_path_and_flags(self):
        handler, seen = _capture('{"species_ref": "spe_1"}\n')
        client, _ = make_client(handler)

        rows = list(
            client.export_ml_species(
                all=True, element=["C", "H"], include_hessian=True
            )
        )

        assert rows == [{"species_ref": "spe_1"}]
        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/export/ml/species.ndjson"
        )
        query = _query_of(str(seen[0].url))
        assert query["all"] == ["true"]
        assert query["element"] == ["C", "H"]
        assert query["include_hessian"] == ["true"]

    def test_export_ml_reactions_path(self):
        handler, seen = _capture("")
        client, _ = make_client(handler)

        assert list(client.export_ml_reactions(reaction_family="H_Abstraction")) == []
        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/export/ml/reactions.ndjson"
        )

    def test_export_chemkin_posts_the_seed_and_returns_bytes(self):
        handler, seen = _capture(b"PK\x03\x04zip-bytes")
        client, _ = make_client(handler)

        content = client.export_chemkin(
            {"reaction_refs": ["rxe_1"]}, energy_units="kcal/mol"
        )

        assert content == b"PK\x03\x04zip-bytes"
        assert seen[0].method == "POST"
        assert _path_of(str(seen[0].url)).endswith("/scientific/export/chemkin")
        assert json.loads(seen[0].content) == {
            "seed": {"reaction_refs": ["rxe_1"]},
            "energy_units": "kcal/mol",
        }

    def test_export_chemkin_rejects_a_non_mapping_seed(self):
        client, _ = make_client(lambda request: _ok({}))
        with pytest.raises(TypeError, match="seed"):
            client.export_chemkin(["rxe_1"])  # type: ignore[arg-type]


class TestMetaAndProbes:
    @pytest.mark.parametrize(
        "method_name,suffix",
        [
            ("get_meta", "/meta"),
            ("get_meta_methods", "/scientific/meta/methods"),
            ("get_meta_basis_sets", "/scientific/meta/basis-sets"),
            ("get_meta_software", "/scientific/meta/software"),
            ("get_meta_workflow_tools", "/scientific/meta/workflow-tools"),
            ("get_meta_reaction_families", "/scientific/meta/reaction-families"),
            ("readyz", "/readyz"),
        ],
    )
    def test_paths(self, method_name, suffix):
        handler, seen = _capture({"results": []})
        client, _ = make_client(handler)

        getattr(client, method_name)()

        assert _path_of(str(seen[0].url)).endswith(suffix)
        assert seen[0].method == "GET"

    def test_meta_reads_work_without_an_api_key(self):
        handler, seen = _capture({"results": ["b3lyp"]})
        client, _ = make_client(handler, api_key=None)

        assert client.get_meta_methods() == {"results": ["b3lyp"]}
        assert "x-api-key" not in {k.lower() for k in seen[0].headers}

    def test_get_meta_software_versions_scopes_by_the_required_parent(self):
        handler, seen = _capture({"results": [{"value": "16", "count": 1}]})
        client, _ = make_client(handler)

        result = client.get_meta_software_versions("gaussian")

        assert result == {"results": [{"value": "16", "count": 1}]}
        assert _path_of(str(seen[0].url)).endswith("/scientific/meta/software-versions")
        query = _query_of(str(seen[0].url))
        assert query["software"] == ["gaussian"]

    def test_get_meta_workflow_tool_versions_scopes_by_the_required_parent(self):
        handler, seen = _capture({"results": [{"value": "1.2.3", "count": 1}]})
        client, _ = make_client(handler)

        result = client.get_meta_workflow_tool_versions("arc")

        assert result == {"results": [{"value": "1.2.3", "count": 1}]}
        assert _path_of(str(seen[0].url)).endswith(
            "/scientific/meta/workflow-tool-versions"
        )
        query = _query_of(str(seen[0].url))
        assert query["workflow_tool"] == ["arc"]


# ---------------------------------------------------------------------------
# Async job lifecycle
# ---------------------------------------------------------------------------


class TestJobs:
    def test_every_job_endpoint_is_reachable_by_short_name(self):
        for kind, path in JOB_ENDPOINTS.items():
            handler, seen = _capture({"job_id": "j1", "status": "queued", "kind": kind})
            client, _ = make_client(handler)

            client.enqueue_job(kind, {"payload": True})

            assert seen[0].method == "POST"
            assert _path_of(str(seen[0].url)).endswith(path)

    def test_explicit_path_is_accepted(self):
        handler, seen = _capture({"job_id": "j1", "status": "queued", "kind": "thermo"})
        client, _ = make_client(handler)

        client.enqueue_job("/jobs/thermo", {})

        assert _path_of(str(seen[0].url)).endswith("/jobs/thermo")

    def test_unknown_kind_is_rejected_before_any_request(self):
        called: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            called.append(request)
            return _ok({})

        client, _ = make_client(handler)
        with pytest.raises(ValueError, match="Unknown job kind"):
            client.enqueue_job("statmech", {})
        assert called == []

    def test_idempotency_key_is_forwarded(self):
        handler, seen = _capture({"job_id": "j1", "status": "queued", "kind": "thermo"})
        client, _ = make_client(handler)

        client.enqueue_job("thermo", {}, idempotency_key="thermo-run-000000001")

        assert seen[0].headers["Idempotency-Key"] == "thermo-run-000000001"

    def test_status_path_and_payload(self):
        body = {
            "job_id": "j1",
            "status": "complete",
            "kind": "thermo",
            "created_at": "2026-01-01T00:00:00Z",
            "attempts": 1,
            "result": {"thermo_id": 5},
        }
        handler, seen = _capture(body)
        client, _ = make_client(handler)

        assert client.get_job_status("j1") == body
        assert _path_of(str(seen[0].url)).endswith("/jobs/j1")

    def test_status_rejects_an_empty_job_id(self):
        client, _ = make_client(lambda request: _ok({}))
        with pytest.raises(ValueError, match="job_id"):
            client.get_job_status("")

    def test_wait_for_job_polls_until_terminal(self):
        states = ["queued", "processing", "complete"]
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _ok(
                {
                    "job_id": "j1",
                    "kind": "thermo",
                    "created_at": "2026-01-01T00:00:00Z",
                    "attempts": 1,
                    "status": states[len(seen) - 1],
                }
            )

        slept: list[float] = []
        client, _ = make_client(handler)

        result = client.wait_for_job(
            "j1", poll_interval=1.5, sleep=slept.append, monotonic=lambda: 0.0
        )

        assert result["status"] == "complete"
        assert len(seen) == 3
        assert slept == [1.5, 1.5]

    def test_wait_for_job_returns_a_failed_job_rather_than_raising(self):
        handler, _ = _capture(
            {
                "job_id": "j1",
                "kind": "thermo",
                "created_at": "2026-01-01T00:00:00Z",
                "attempts": 3,
                "status": "failed",
                "error": "boom",
            }
        )
        client, _ = make_client(handler)

        result = client.wait_for_job("j1", sleep=lambda _s: None)

        assert result["status"] == "failed"
        assert result["error"] == "boom"

    def test_wait_for_job_times_out(self):
        handler, _ = _capture(
            {
                "job_id": "j1",
                "kind": "thermo",
                "created_at": "2026-01-01T00:00:00Z",
                "attempts": 1,
                "status": "processing",
            }
        )
        client, _ = make_client(handler)
        clock = iter([0.0, 100.0, 200.0])

        with pytest.raises(TimeoutError, match="terminal state"):
            client.wait_for_job(
                "j1",
                timeout=10.0,
                sleep=lambda _s: None,
                monotonic=lambda: next(clock),
            )

    def test_terminal_statuses_match_the_backend_enum(self):
        assert TERMINAL_JOB_STATUSES == frozenset({"complete", "failed"})
