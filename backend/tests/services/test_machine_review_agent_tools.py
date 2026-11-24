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
