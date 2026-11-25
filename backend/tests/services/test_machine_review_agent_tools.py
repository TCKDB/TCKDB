"""The get_record tool: offered refs only, exactly the pack's summary, bounded and free of depositor text."""

from __future__ import annotations

import json

import pytest

from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMRecordRef, LLMRecordSummary
from app.services.machine_review.agent.tools import TOOL_RESULT_MAX_BYTES, ToolRegistry
from app.services.machine_review.providers.interface import MachineReviewContext
from app.services.machine_review.providers.prompt import compact as _compact

TITLE = "TITLE-SENTINEL"
SUMMARY = "SUMMARY-SENTINEL"


def _summary(ref: str = "111", n_values: int = 3) -> LLMRecordSummary:
    return LLMRecordSummary(
        record_type="calculation",
        record_ref=ref,
        subject={"species": "CH4"},
        values={f"value_{i}": float(i) for i in range(n_values)},
        levels_of_theory={"level": "b3lyp/def2"},
    )


def _registry(summaries=None, offered=(("calculation", "111"), ("calculation", "222"))):
    pack = {("calculation", "111"): _summary("111")}
    return ToolRegistry(offered=frozenset(offered), summaries=pack if summaries is None else summaries)


def call(registry, record_type="calculation", record_ref="111"):
    return registry.execute("get_record", json.dumps({"record_type": record_type, "record_ref": record_ref}))


def test_an_offered_ref_returns_the_compact_summary_dump():
    outcome = call(_registry())
    assert outcome.status_code == "ok"
    assert json.loads(outcome.content) == _compact(_summary("111").model_dump(mode="json"))


def test_a_ref_outside_the_offered_set_is_refused_and_not_echoed():
    registry = _registry(offered=(("calculation", "111"),))
    assert call(registry).status_code == "ok"
    refused = call(registry, record_ref="REF-SENTINEL-999")
    assert refused.status_code == "refused"
    assert "REF-SENTINEL-999" not in refused.content
    json.loads(refused.content)


def test_an_offered_ref_without_a_summary_is_not_available():
    assert call(_registry(), record_ref="222").status_code == "not_available"


def test_a_summary_held_for_a_ref_that_is_not_offered_is_still_refused():
    registry = ToolRegistry(offered=frozenset(), summaries={("calculation", "111"): _summary("111")})
    assert call(registry).status_code == "refused"


@pytest.mark.parametrize(
    "text",
    ["not json", json.dumps({"record_type": "calculation"}), json.dumps({"record_type": "a", "record_ref": "1", "x": 1})],
)
def test_bad_arguments_are_invalid_and_the_valid_twin_is_ok(text):
    registry = _registry()
    assert registry.execute("get_record", text).status_code == "invalid_arguments"
    assert call(registry).status_code == "ok"


def test_an_unknown_tool_is_reported():
    assert _registry().execute("rm_rf", "{}").status_code == "unknown_tool"


def test_an_oversized_summary_is_truncated_to_valid_json_under_the_cap():
    big = ToolRegistry(
        offered=frozenset({("calculation", "111")}),
        summaries={("calculation", "111"): _summary("111", n_values=400)},
    )
    outcome = call(big)
    assert outcome.status_code == "truncated"
    assert len(outcome.content.encode()) <= TOOL_RESULT_MAX_BYTES
    assert json.loads(outcome.content)["truncated"] is True
    small = json.loads(call(_registry()).content)
    assert "truncated" not in small


def test_no_depositor_text_or_foreign_key_reaches_a_tool_result():
    context = MachineReviewContext.from_llm_precheck_context(
        LLMPrecheckContext(
            submission_id=5,
            title=TITLE,
            summary=SUMMARY,
            record_refs=(LLMRecordRef(record_type="calculation", record_id=111),),
            record_summaries=(_summary("111"),),
        )
    )
    from app.services.machine_review.offer import offered_refs_from_context

    registry = ToolRegistry(
        offered=offered_refs_from_context(context),
        summaries={("calculation", "111"): context.precheck_context.record_summaries[0]},
    )
    out = call(registry).content
    assert TITLE not in out and SUMMARY not in out and "submission_id" not in out
    allowed = set(LLMRecordSummary.model_fields) | {"truncated"}
    assert set(json.loads(out)) <= allowed


def test_the_schema_lists_get_record_only():
    schemas = _registry().schemas()
    assert [s["function"]["name"] for s in schemas] == ["get_record"]


def _kinetics_summary(qualifier_width: int) -> LLMRecordSummary:
    return LLMRecordSummary(
        record_type="kinetics",
        record_ref="9",
        values={
            "a": 1.0e13,
            "a_units": "cm3_mol_s",
            "n": 0.5,
            "t0_k": 298.0,
            "ea_kj_mol": 12.0,
            "a_uncertainty": 2.0,
            "a_uncertainty_kind": "m" * qualifier_width,
        },
    )


def test_truncation_never_leaves_a_number_without_its_unit_or_qualifier():
    pairs = (("a", "a_units"), ("a_uncertainty", "a_uncertainty_kind"))
    cut_seen = False
    for width in range(TOOL_RESULT_MAX_BYTES - 200, TOOL_RESULT_MAX_BYTES + 60):
        registry = ToolRegistry(
            offered=frozenset({("kinetics", "9")}), summaries={("kinetics", "9"): _kinetics_summary(width)}
        )
        outcome = registry.execute("get_record", json.dumps({"record_type": "kinetics", "record_ref": "9"}))
        assert len(outcome.content.encode()) <= TOOL_RESULT_MAX_BYTES
        values = json.loads(outcome.content).get("values", {})
        for number, unit in pairs:
            assert (number in values) == (unit in values)
        cut_seen = cut_seen or outcome.status_code == "truncated"
    assert cut_seen


# --- search_guidance ---------------------------------------------------------------------------------

from app.services.machine_review.agent.guidance import GuidanceEntry, GuidanceIndex


def _guidance(n: int = 8, text_chars: int = 40, field_chars: int = 0) -> GuidanceIndex:
    return GuidanceIndex(
        [
            GuidanceEntry(
                source_key=f"warning:code_{i}",
                kind="deposit_warning",
                title=f"code {i}",
                text="spin " + "x" * text_chars,
                fields={"note": "y" * field_chars} if field_chars else {},
                codes=(f"code_{i}",),
            )
            for i in range(n)
        ]
    )


def _search(registry, **args):
    return registry.execute("search_guidance", json.dumps({"query": "spin", **args}))


def _guided(**kw):
    return ToolRegistry(offered=frozenset(), summaries={}, **kw)


def test_search_guidance_is_listed_only_when_an_index_is_given():
    assert [s["function"]["name"] for s in _registry().schemas()] == ["get_record"]
    names = [s["function"]["name"] for s in _guided(guidance=_guidance()).schemas()]
    assert names == ["get_record", "search_guidance"]


def test_a_search_returns_at_most_five_results_and_records_what_it_delivered():
    registry = _guided(guidance=_guidance())
    assert registry.returned_sources == frozenset()
    outcome = _search(registry)
    body = json.loads(outcome.content)
    assert outcome.status_code == "ok"
    assert len(body["results"]) == 5 and len(outcome.content.encode()) <= TOOL_RESULT_MAX_BYTES
    assert registry.returned_sources == {r["source_key"] for r in body["results"]}


def test_an_oversized_result_is_truncated_and_dropped_keys_are_not_returned():
    registry = _guided(guidance=_guidance(text_chars=3000, field_chars=1500))
    outcome = _search(registry)
    body = json.loads(outcome.content)
    assert outcome.status_code == "truncated"
    assert len(outcome.content.encode()) <= TOOL_RESULT_MAX_BYTES
    delivered = {r["source_key"] for r in body["results"]}
    assert 0 < len(delivered) < 5 and registry.returned_sources == delivered


@pytest.mark.parametrize(
    "args",
    [{"query": ""}, {"query": "x" * 201}, {"query": "spin", "extra": 1}, {"query": "spin", "kind": "lessons"}],
)
def test_bad_search_arguments_are_invalid_and_add_no_source(args):
    registry = _guided(guidance=_guidance())
    assert registry.execute("search_guidance", json.dumps(args)).status_code == "invalid_arguments"
    assert registry.returned_sources == frozenset()
    assert _search(registry).status_code == "ok"


def test_a_search_with_no_match_is_ok_and_empty():
    registry = _guided(guidance=_guidance())
    outcome = _search(registry, query="qqqq")
    assert outcome.status_code == "ok" and json.loads(outcome.content) == {"results": []}
    assert registry.returned_sources == frozenset()


def test_search_guidance_without_an_index_is_unknown_and_known_name_follows_the_registry():
    plain, guided = _registry(), _guided(guidance=_guidance())
    assert plain.execute("search_guidance", json.dumps({"query": "spin"})).status_code == "unknown_tool"
    assert plain.known_name("search_guidance") is None and plain.known_name("get_record") == "get_record"
    assert guided.known_name("search_guidance") == "search_guidance" and guided.known_name("rm") is None


def test_the_prefetch_message_holds_the_seeded_entries_and_counts_them_as_returned():
    registry = _guided(guidance=_guidance(), guidance_seed=("code_2", "code_1"))
    message = registry.prefetch_guidance_message()
    assert message["role"] == "user"
    assert [m for m in ("warning:code_2", "warning:code_1") if m in message["content"]] == [
        "warning:code_2",
        "warning:code_1",
    ]
    assert message["content"].index("warning:code_2") < message["content"].index("warning:code_1")
    assert registry.returned_sources == {"warning:code_2", "warning:code_1"}


def test_without_a_seed_or_an_index_there_is_no_prefetch_message():
    assert _guided(guidance=_guidance()).prefetch_guidance_message() is None
    assert _guided(guidance_seed=("code_1",)).prefetch_guidance_message() is None
    registry = _guided(guidance=_guidance())
    registry.prefetch_guidance_message()
    assert registry.returned_sources == frozenset()


# --- lessons -------------------------------------------------------------------------------------------


def _lessons(n: int = 4, category: str | None = None, prefix: str = "spin") -> GuidanceIndex:
    return GuidanceIndex(
        [
            GuidanceEntry(
                source_key=f"lesson:{i}",
                kind="lesson",
                title="Lesson written by a curator",
                text=f"{prefix} note {i}",
                fields={"author": "curator", **({"category": category} if category else {})},
            )
            for i in range(1, n + 1)
        ]
    )


def test_lesson_hits_come_first_at_most_three_and_corpus_entries_fill_to_five():
    registry = _guided(guidance=_guidance(), lessons=_lessons(4))
    body = json.loads(_search(registry).content)
    kinds = [r["kind"] for r in body["results"]]
    assert kinds == ["lesson"] * 3 + ["deposit_warning"] * 2
    assert all(r["fields"]["author"] == "curator" for r in body["results"] if r["kind"] == "lesson")
    assert registry.returned_sources == {r["source_key"] for r in body["results"]}
    assert {"lesson:1", "lesson:2", "lesson:3"} <= registry.returned_sources


def test_kind_lesson_returns_only_lessons_and_another_kind_returns_none():
    registry = _guided(guidance=_guidance(), lessons=_lessons(2))
    only = json.loads(_search(registry, kind="lesson").content)
    assert [r["kind"] for r in only["results"]] == ["lesson", "lesson"]
    other = json.loads(_search(registry, kind="deposit_warning").content)
    assert other["results"] and all(r["kind"] == "deposit_warning" for r in other["results"])
    assert "lesson" not in json.dumps(json.loads(_search(registry, kind="check").content))


def test_a_registry_without_lessons_answers_a_lesson_search_with_nothing():
    registry = _guided(guidance=_guidance())
    outcome = _search(registry, kind="lesson")
    assert outcome.status_code == "ok" and json.loads(outcome.content) == {"results": []}
    assert registry.returned_sources == frozenset()
    assert json.loads(_search(registry).content)["results"]


def test_a_category_filter_drops_a_lesson_of_another_category_and_keeps_the_matching_one():
    registry = _guided(lessons=GuidanceIndex([*_lessons(1, "thermo").entries, *_lessons(2, "kinetics").entries[1:]]))
    wrong = json.loads(_search(registry, kind="lesson", category="geometry").content)
    assert wrong["results"] == []
    right = json.loads(_search(registry, kind="lesson", category="kinetics").content)
    assert [r["source_key"] for r in right["results"]] == ["lesson:2"]
    assert registry.returned_sources == {"lesson:2"}


def test_the_search_tool_is_offered_for_lessons_alone_and_lists_the_lesson_kind():
    registry = _guided(lessons=_lessons(1))
    (tool,) = [s for s in registry.schemas() if s["function"]["name"] == "search_guidance"]
    properties = tool["function"]["parameters"]["properties"]
    assert "lesson" in properties["kind"]["enum"] and "category" in properties
    assert "written by curators" in tool["function"]["description"]
    assert registry.known_name("search_guidance") == "search_guidance"
