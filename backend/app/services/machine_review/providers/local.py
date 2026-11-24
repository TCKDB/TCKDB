"""The local machine-review provider: one chat call to an OpenAI-compatible server (llama.cpp, Ollama, vLLM).

Thinking stays on and is bounded per request by ``thinking_budget_tokens``. The server's reasoning arrives in
``reasoning_content`` and is dropped here; only the answer is parsed, through the same strict boundary the cloud
provider uses. ``model`` and ``provider`` are stamped from configuration, never taken from the payload.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from app.services.machine_review.agent.loop import (
    THINKING_ALLOWANCE_FACTOR,
    Emit,
    LoopResult,
    RunBudget,
    run_review_loop,
)
from app.services.machine_review.agent.tools import ToolRegistry
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    parse_machine_review_payload,
)
from app.services.machine_review.providers.openai_transport import ChatTurn
from app.services.machine_review.providers.prompt import (
    MACHINE_REVIEW_SYSTEM_PROMPT,
    build_user_message,
)
from app.services.machine_review.schemas import MachineReviewProviderResult, provider_output_json_schema

logger = logging.getLogger(__name__)

#: Schema name sent with a ``json_schema`` response format.
_SCHEMA_NAME = "machine_review"


class MachineReviewChatClient(Protocol):
    """The transport a local provider talks through."""

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_output_tokens: int,
        timeout_seconds: float,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatTurn: ...


class LocalMachineReviewProvider:
    """Reviews a submission with one local-model call."""

    def __init__(
        self,
        *,
        client: MachineReviewChatClient,
        model: str,
        structured_output: Literal["json_schema", "json_object"] = "json_schema",
        max_output_tokens: int,
        thinking_budget_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._model = model
        self._structured_output = structured_output
        self._max_output_tokens = max_output_tokens
        self._thinking_budget_tokens = thinking_budget_tokens
        self._timeout_seconds = timeout_seconds

    def _response_format(self) -> dict[str, Any]:
        if self._structured_output == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": _SCHEMA_NAME, "strict": True, "schema": provider_output_json_schema()},
        }

    def run_agent(
        self,
        context: MachineReviewContext,
        *,
        registry: ToolRegistry,
        budget: RunBudget,
        emit: Emit,
        cancelled: Callable[[], bool],
    ) -> LoopResult:
        """Run the tool loop on ``context`` and return the final answer text, unparsed."""
        return run_review_loop(
            self._client,
            model=self._model,
            messages=[
                {"role": "system", "content": MACHINE_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(context)},
            ],
            registry=registry,
            budget=budget,
            response_format=self._response_format(),
            emit=emit,
            cancelled=cancelled,
        )

    def review_submission(self, context: MachineReviewContext) -> MachineReviewProviderResult:
        """Run one review and return the validated result."""
        started = time.monotonic()
        turn = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": MACHINE_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(context)},
            ],
            response_format=self._response_format(),
            max_output_tokens=self._max_output_tokens + THINKING_ALLOWANCE_FACTOR * self._thinking_budget_tokens,
            timeout_seconds=self._timeout_seconds,
            extra_body={"thinking_budget_tokens": self._thinking_budget_tokens},
        )
        usage = turn.usage or {}
        logger.info(
            "local machine review: model=%s seconds=%.1f prompt_tokens=%s completion_tokens=%s finish=%s",
            self._model,
            time.monotonic() - started,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            turn.finish_reason,
        )
        result = parse_machine_review_payload(turn.content)
        return result.model_copy(update={"model": self._model, "provider": type(self).__name__})


__all__ = ["THINKING_ALLOWANCE_FACTOR", "LocalMachineReviewProvider", "MachineReviewChatClient"]
