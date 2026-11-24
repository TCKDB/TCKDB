"""The agent loop against a scripted chat client: ordering, budgets, fallback and cancellation. No network, no DB."""

from __future__ import annotations

import json

import pytest

from app.services.llm_precheck.schemas import LLMRecordSummary
from app.services.machine_review.agent.loop import (
    FINAL_TURN_INSTRUCTION,
    LoopResult,
    ReviewBudgetExhausted,
    RunBudget,
    run_review_loop,
)
from app.services.machine_review.agent.tools import ToolRegistry
from app.services.machine_review.providers.interface import MachineReviewProviderTimeoutError
from app.services.machine_review.providers.openai_transport import ChatTurn, ToolCall, ToolsUnsupportedError
from app.services.machine_review.run import MachineReviewCancelled

RESPONSE_FORMAT = {"type": "json_object"}
FINAL = '{"final": true}'


def tool_turn(*refs: str, reasoning="REASONING-SENTINEL", usage=(10, 5)) -> ChatTurn:
    calls = tuple(
        ToolCall(id=f"c{i}-{ref}", name="get_record", arguments=json.dumps({"record_type": "calculation", "record_ref": ref}))
        for i, ref in enumerate(refs)
    )
    return ChatTurn("", reasoning, "tool_calls", {"prompt_tokens": usage[0], "completion_tokens": usage[1]}, calls)


def answer_turn(text=FINAL, usage=(10, 5)) -> ChatTurn:
    return ChatTurn(text, "REASONING-SENTINEL", "stop", {"prompt_tokens": usage[0], "completion_tokens": usage[1]})


class FakeClient:
    def __init__(self, script, *, clock=None, tick=0.0):
        self.script = list(script)
        self.requests: list[dict] = []
        self.clock = clock
        self.tick = tick

    def chat(self, **kwargs):
        self.requests.append({**kwargs, "messages": json.loads(json.dumps(kwargs["messages"]))})
        if self.clock is not None:
            self.clock[0] += self.tick
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


class Clock:
    def __init__(self):
        self.now = [0.0]

    def __call__(self):
        return self.now[0]


def registry():
    summary = LLMRecordSummary(record_type="calculation", record_ref="111", values={"x": 1})
    return ToolRegistry(
        offered=frozenset({("calculation", "111"), ("calculation", "222")}), summaries={("calculation", "111"): summary}
    )


def budget(**kw):
    base = {
        "max_steps": 6,
        "max_tool_calls": 10,
        "wall_seconds": 900,
        "call_timeout_seconds": 180,
        "max_output_tokens": 1200,
        "thinking_budget_tokens": 512,
        **kw,
    }
    return RunBudget(**base)


class Events:
    def __init__(self):
        self.items = []

    def __call__(self, kind, *, tool_name=None, outcome, started_at, duration_ms, detail):
        self.items.append((kind, tool_name, outcome, detail))

    @property
    def kinds(self):
        return [(k, c) for k, _, c, _ in self.items]


def run(client, *, events=None, cancelled=lambda: False, clock=None, **budget_kw):
    events = events if events is not None else Events()
    kwargs = {"clock": clock} if clock else {}
    result = run_review_loop(
        client,
        model="m",
        messages=[{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        registry=registry(),
        budget=budget(**budget_kw),
        response_format=RESPONSE_FORMAT,
        emit=events,
        cancelled=cancelled,
        **kwargs,
    )
    return result, events


def test_tool_turn_answer_turn_final_turn_in_order():
    client = FakeClient([tool_turn("111"), answer_turn("thinking aloud"), answer_turn(FINAL)])
    result, events = run(client)
    assert isinstance(result, LoopResult) and result.text == FINAL
    assert events.kinds == [
        ("model_turn", "ok"), ("tool_call", "ok"), ("tool_result", "ok"), ("model_turn", "ok"), ("model_turn", "final")
    ]
    assert [r.get("tools") is not None for r in client.requests] == [True, True, False]
    assert [r.get("response_format") is not None for r in client.requests] == [False, False, True]
    assert FINAL_TURN_INSTRUCTION in json.dumps(client.requests[-1]["messages"])
    assert FINAL_TURN_INSTRUCTION not in json.dumps(client.requests[0]["messages"])
    assert "REASONING-SENTINEL" not in json.dumps([r["messages"] for r in client.requests])
    assert client.requests[0]["extra_body"] == {"thinking_budget_tokens": 512}
    assert client.requests[0]["max_output_tokens"] == 1200 + 2 * 512
    assert (result.steps, result.tokens_in, result.tokens_out) == (3, 30, 15)


def test_the_tool_call_cap_drops_surplus_calls_and_leaves_one_final_call():
    client = FakeClient([tool_turn("111", "222"), tool_turn("111", "222"), answer_turn()])
    result, events = run(client, max_tool_calls=3)
    assert [k for k, _ in events.kinds].count("tool_call") == 3
    assert len(client.requests) == 3
    assert "tools" not in client.requests[-1] and client.requests[-1]["response_format"] == RESPONSE_FORMAT
    for request in client.requests:
        messages = request["messages"]
        wanted = {c["id"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls", [])}
        answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
        assert wanted == answered


def test_max_steps_bounds_the_chat_calls_and_the_last_is_structured():
    client = FakeClient([tool_turn("111")] * 10 + [answer_turn()])
    run(client, max_steps=3)
    assert len(client.requests) == 3
    assert client.requests[-1]["response_format"] == RESPONSE_FORMAT and "tools" not in client.requests[-1]


def test_an_exhausted_wall_clock_raises_and_the_generous_twin_returns():
    clock = Clock()
    client = FakeClient([tool_turn("111"), answer_turn(), answer_turn()], clock=clock.now, tick=60)
    with pytest.raises(ReviewBudgetExhausted):
        run(client, clock=clock, wall_seconds=100)
    assert len(client.requests) == 2
    assert client.requests[1]["timeout_seconds"] == 40
    clock2 = Clock()
    twin = FakeClient([tool_turn("111"), answer_turn(), answer_turn()], clock=clock2.now, tick=60)
    result, _ = run(twin, clock=clock2, wall_seconds=10_000)
    assert result.text == FINAL and twin.requests[1]["timeout_seconds"] == 180


def test_a_timeout_on_a_clipped_call_is_budget_exhausted_and_otherwise_propagates():
    clock = Clock()
    client = FakeClient([MachineReviewProviderTimeoutError("t")], clock=clock.now)
    with pytest.raises(ReviewBudgetExhausted):
        run(client, clock=clock, wall_seconds=50, call_timeout_seconds=180)
    with pytest.raises(MachineReviewProviderTimeoutError):
        run(FakeClient([MachineReviewProviderTimeoutError("t")]), clock=Clock(), wall_seconds=5000)


def test_tools_unsupported_on_the_first_call_falls_back_to_one_structured_call():
    client = FakeClient([ToolsUnsupportedError("no tools"), answer_turn()])
    result, events = run(client)
    assert result.text == FINAL and len(client.requests) == 2
    assert "tools" not in client.requests[1] and client.requests[1]["response_format"] == RESPONSE_FORMAT
    assert FINAL_TURN_INSTRUCTION not in json.dumps(client.requests[1]["messages"])
    assert ("model_turn", "tools_unsupported") in events.kinds
    assert not any(k == "tool_call" for k, _ in events.kinds)


def test_tools_unsupported_on_a_later_call_propagates():
    client = FakeClient([tool_turn("111"), ToolsUnsupportedError("no tools")])
    with pytest.raises(ToolsUnsupportedError):
        run(client)


def test_cancel_after_the_first_tool_stops_before_another_model_call():
    state = {"tools": 0}

    class Counting(Events):
        def __call__(self, kind, **kw):
            super().__call__(kind, **kw)
            if kind == "tool_result":
                state["tools"] += 1

    client = FakeClient([tool_turn("111"), answer_turn()])
    with pytest.raises(MachineReviewCancelled):
        run(client, events=Counting(), cancelled=lambda: state["tools"] >= 1)
    assert len(client.requests) == 1


def test_event_detail_carries_the_turn_and_never_leaks_into_later_requests():
    client = FakeClient([tool_turn("111"), answer_turn()])
    _, events = run(client)
    first = events.items[0][3]
    assert first["reasoning_content"] == "REASONING-SENTINEL" and first["tool_calls"][0]["name"] == "get_record"
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert events.items[1][3]["arguments"]
