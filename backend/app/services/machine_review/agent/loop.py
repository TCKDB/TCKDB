"""The review loop: a bounded tool phase, then one strict structured answer.

The model may read records through the registry's tools. Each model call and tool call is reported through
``emit`` so the job shows progress. The loop ends with one call that has no tools and carries the response format;
its text is returned for the caller to parse, so a tool-phase reply is never taken as the answer. Reasoning is kept
out of the history sent back to the model. A server that rejects ``tools`` gets one single-shot structured call.

Steps, tool calls and wall time are all bounded; running out raises :class:`ReviewBudgetExhausted`.

ASCII only: the final instruction is a runtime string under ``app/``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.db.models.common import MachineReviewJobEventKind as Kind
from app.services.machine_review.agent.tools import ToolRegistry
from app.services.machine_review.providers.interface import MachineReviewProviderTimeoutError
from app.services.machine_review.providers.openai_transport import (
    ChatTurn,
    ToolCall,
    ToolsUnsupportedError,
)
from app.services.machine_review.run import (
    MachineReviewCancelled,
    ReviewBudgetExhausted,
    _failure_code_for,
)

#: Reasoning can overshoot its budget, so ``max_tokens`` leaves this many budgets of room beside the answer.
THINKING_ALLOWANCE_FACTOR = 2

#: Longest string kept in an event's detail.
DETAIL_STRING_LIMIT = 8192

FINAL_TURN_INSTRUCTION = (
    "No more lookups are available. Give your review now as the single JSON object described above, "
    "and nothing else."
)

STATUS_OK = "ok"
STATUS_FINAL = "final"
STATUS_TOOLS_UNSUPPORTED = "tools_unsupported"


class ChatClient(Protocol):
    def chat(self, **kwargs: Any) -> ChatTurn: ...


Emit = Callable[..., None]


@dataclass(frozen=True)
class RunBudget:
    max_steps: int
    max_tool_calls: int
    wall_seconds: float
    call_timeout_seconds: float
    max_output_tokens: int
    thinking_budget_tokens: int

    @classmethod
    def from_settings(cls, settings_obj: Any) -> "RunBudget":
        return cls(
            max_steps=settings_obj.llm_precheck_max_steps,
            max_tool_calls=settings_obj.llm_precheck_max_tool_calls,
            wall_seconds=settings_obj.llm_precheck_run_wall_seconds,
            call_timeout_seconds=settings_obj.llm_precheck_timeout_seconds,
            max_output_tokens=settings_obj.llm_precheck_max_output_tokens,
            thinking_budget_tokens=settings_obj.llm_precheck_thinking_budget_tokens,
        )


@dataclass(frozen=True)
class LoopResult:
    text: str
    steps: int
    tokens_in: int
    tokens_out: int


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value[:DETAIL_STRING_LIMIT]
    if isinstance(value, dict):
        return {key: _clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip(item) for item in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _assistant_message(turn: ChatTurn, calls: tuple[ToolCall, ...]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": turn.content or "",
        "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in calls
        ],
    }


def run_review_loop(
    client: ChatClient,
    *,
    model: str,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    budget: RunBudget,
    response_format: dict[str, Any],
    emit: Emit,
    cancelled: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
) -> LoopResult:
    """Run the loop and return the final answer text; see the module docstring."""
    history = list(messages)
    deadline = clock() + budget.wall_seconds
    state = {"steps": 0, "tokens_in": 0, "tokens_out": 0, "reported": 0}
    max_tokens = budget.max_output_tokens + THINKING_ALLOWANCE_FACTOR * budget.thinking_budget_tokens

    def check_live() -> float:
        if cancelled():
            raise MachineReviewCancelled
        remaining = deadline - clock()
        if remaining <= 0:
            raise ReviewBudgetExhausted("The review ran past its time limit.")
        return remaining

    def call(*, tools: bool, final: bool, status: str, report_unsupported: bool = False) -> ChatTurn:
        remaining = check_live()
        clipped = remaining < budget.call_timeout_seconds
        request: dict[str, Any] = {
            "model": model,
            "messages": history,
            "max_output_tokens": max_tokens,
            "timeout_seconds": min(budget.call_timeout_seconds, remaining),
            "extra_body": {"thinking_budget_tokens": budget.thinking_budget_tokens},
        }
        if tools:
            request["tools"] = registry.schemas()
        if final:
            request["response_format"] = response_format
        started_at, started = _now(), clock()
        reported_before = state["reported"]
        sent = history[reported_before:]
        state["reported"] = len(history)

        def report(code: str, turn: ChatTurn | None) -> None:
            detail: dict[str, Any] = {"messages": sent}
            if turn is not None:
                detail.update(
                    content=turn.content,
                    reasoning_content=turn.reasoning_content,
                    tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in turn.tool_calls],
                    usage=turn.usage,
                    finish_reason=turn.finish_reason,
                )
            emit(
                Kind.model_turn,
                status_code=code,
                started_at=started_at,
                duration_ms=int((clock() - started) * 1000),
                detail=_clip(detail),
            )

        try:
            turn = client.chat(**request)
        except ToolsUnsupportedError:
            if report_unsupported:
                state["reported"] = reported_before
            else:
                report(_failure_code_for(ToolsUnsupportedError()).value, None)
            raise
        except MachineReviewProviderTimeoutError as exc:
            report(_failure_code_for(exc).value, None)
            if clipped:
                raise ReviewBudgetExhausted("The review ran past its time limit.") from exc
            raise
        except Exception as exc:
            report(_failure_code_for(exc).value, None)
            raise
        usage = turn.usage or {}
        state["steps"] += 1
        state["tokens_in"] += int(usage.get("prompt_tokens") or 0)
        state["tokens_out"] += int(usage.get("completion_tokens") or 0)
        report(status, turn)
        return turn

    def result(turn: ChatTurn) -> LoopResult:
        return LoopResult(turn.content, state["steps"], state["tokens_in"], state["tokens_out"])

    tool_calls_done = 0
    if budget.max_tool_calls > 0:
        while state["steps"] < budget.max_steps - 1:
            try:
                turn = call(tools=True, final=False, status=STATUS_OK, report_unsupported=state["steps"] == 0)
            except ToolsUnsupportedError:
                if state["steps"] > 0:
                    raise
                # The server takes no tools: one structured call on the original messages.
                return result(call(tools=False, final=True, status=STATUS_TOOLS_UNSUPPORTED))
            if not turn.tool_calls:
                if turn.content:
                    history.append({"role": "assistant", "content": turn.content})
                break
            taken = turn.tool_calls[: budget.max_tool_calls - tool_calls_done]
            history.append(_assistant_message(turn, taken))
            for tool_call in taken:
                check_live()
                emit(
                    Kind.tool_call,
                    tool_name=tool_call.name,
                    status_code=STATUS_OK,
                    started_at=_now(),
                    duration_ms=0,
                    detail=_clip({"arguments": tool_call.arguments}),
                )
                started_at, started = _now(), clock()
                outcome = registry.execute(tool_call.name, tool_call.arguments)
                tool_calls_done += 1
                emit(
                    Kind.tool_result,
                    tool_name=tool_call.name,
                    status_code=outcome.status_code,
                    started_at=started_at,
                    duration_ms=int((clock() - started) * 1000),
                    detail=_clip({"output": outcome.content}),
                )
                history.append({"role": "tool", "tool_call_id": tool_call.id, "content": outcome.content})
            if tool_calls_done >= budget.max_tool_calls:
                break

    history.append({"role": "user", "content": FINAL_TURN_INSTRUCTION})
    return result(call(tools=False, final=True, status=STATUS_FINAL))


__all__ = [
    "FINAL_TURN_INSTRUCTION",
    "THINKING_ALLOWANCE_FACTOR",
    "LoopResult",
    "ReviewBudgetExhausted",
    "RunBudget",
    "run_review_loop",
]
