"""The default cloud transport: any OpenAI-compatible chat-completions endpoint.

One implementation of
:class:`~app.services.machine_review.providers.cloud.MachineReviewModelClient`.
It is the *default* transport, not a required one -- the provider takes whatever
satisfies the protocol, which is how CI runs the real prompt and the real parse
against a committed fixture with no network and no key.

**Why the OpenAI shape rather than one vendor's own API.** The archive should
not be tied to a single model supplier. ``/chat/completions`` is the de-facto
interchange format: OpenAI, Together, Groq, Fireworks, OpenRouter, DeepSeek, a
self-hosted vLLM or Ollama, and Anthropic's own compatibility endpoint all
accept it. Pointing ``LLM_PRECHECK_BASE_URL`` at any of them changes the
supplier with no code change. An earlier draft of this module spoke Anthropic's
Messages API, which only Anthropic speaks; the portability lives in the wire
format, not in the package.

**Why the vendor package here, when the rest of the backend avoids them.** The
usual objection is build weight: the backend ships as an arm64 image built
under QEMU, and every wheel that needs compiling costs real minutes. ``openai``
is pure Python, so it costs none of that, and it is declared under the optional
``llm`` extra and imported **inside** the constructor -- an install without it
is fine until somebody actually sets ``AI_REVIEW_ASSISTANT_MODE=cloud``, and
then gets a configuration error naming the extra rather than an ``ImportError``
from deep inside a request. What it buys is typed errors, connection reuse, and
a request shape that tracks the API rather than this file tracking it.

``response_format`` is ``json_object`` and not ``json_schema`` on purpose.
Strict schema enforcement is supported by only some of the endpoints above,
and this transport's whole point is that it does not care which one it is
talking to. The model's output is untrusted either way: the real boundary is
:func:`~app.services.machine_review.providers.interface.parse_machine_review_payload`,
whose ``extra="forbid"`` refuses mutation fields and over-cap text. A review
that cites an unoffered record pair or an unreturned retrieval source is failed
by ``offer.py``. ``json_object`` makes a well-formed answer likelier; it is
not what makes an answer safe.

The key is read from the environment variable named by
``LLM_PRECHECK_API_KEY_ENV`` and is never logged, never stored, and never put
in an exception message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.machine_review.providers.interface import (
    MachineReviewProviderAuthError,
    MachineReviewProviderConfigurationError,
    MachineReviewProviderTimeoutError,
    MachineReviewProviderUnavailableError,
)

#: OpenAI's own endpoint. Overridable per instance, which is the whole point:
#: any compatible gateway, proxy, or self-hosted server goes here instead.
DEFAULT_API_BASE_URL = "https://api.openai.com/v1"


class ModelOutputTruncatedError(ValueError):
    """The model hit its output ceiling mid-answer.

    A distinct type because the fault it names is distinct and actionable: the
    answer was well-formed and did not fit, so the lever is
    ``LLM_PRECHECK_MAX_OUTPUT_TOKENS``, not the prompt and not the model.
    Without it a truncated reply is simply invalid JSON and gets reported as
    "malformed output", which sends a reader looking for a model that cannot
    follow a schema when the real answer is "it ran out of room".

    Reachable in ordinary use, not only at the extremes: the contract allows 50
    findings with messages up to 1000 characters each, which is on the order of
    14,000 tokens of reply -- past both the 1200-token configured default and
    any modest ceiling an operator is likely to pick.
    """


class ModelRefusedError(ValueError):
    """The model declined to answer, and said so in the documented field.

    Separate from an empty reply: a refusal is a deliberate response carrying a
    reason, and reporting it as "no content" would discard the one thing that
    explains the run. The reason is surfaced because it is the model's own
    words about this submission's context, not a secret.
    """


class ToolsUnsupportedError(Exception):
    """The server rejected a request because it carried ``tools``; a request without them may still work."""


class ModelContextExceededError(Exception):
    """The request did not fit the server's context window."""


def _error_text(exc: Any) -> str:
    """The server's error body as lower-case text, for matching only; never shown or stored."""
    parts = [str(getattr(exc, "message", "") or "")]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    return " ".join(parts).lower()


class OpenAICompatibleClient:
    """Calls an OpenAI-compatible chat-completions endpoint and returns the text."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        max_retries: int | None = None,
        connect_timeout_seconds: float | None = None,
    ) -> None:
        """``base_url=None`` means OpenAI's own endpoint.

        None rather than a default argument value so a caller reading
        configuration can pass ``settings.llm_precheck_base_url`` straight
        through: it is ``str | None``, and an unset setting must mean "the
        default", not a crash. One place decides what the default is.

        ``max_retries=None`` keeps the SDK's own retry policy; the local reviewer passes 0 so a
        down server is reported at once. ``connect_timeout_seconds`` bounds only the connection
        attempt, so an unreachable host fails fast while a long generation still gets its full
        read timeout.
        """
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - install-shaped
            raise MachineReviewProviderConfigurationError(
                "Machine review needs the 'llm' extra for its HTTP "
                "transport: install the backend with the [llm] extra, or "
                "inject a client that satisfies MachineReviewModelClient."
            ) from exc
        kwargs: dict[str, Any] = {}
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self._connect_timeout_seconds = connect_timeout_seconds
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or DEFAULT_API_BASE_URL,
            **kwargs,
        )

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
    ) -> "ChatTurn":
        """Send one chat request and return the reply turn.

        Transport faults become the provider errors the worker maps to failure codes: a refused or
        timed-out connection and a 5xx are unavailable, a read timeout is a timeout, a 401 or 403
        is an auth failure. Anything else propagates unchanged.
        """
        import openai

        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "timeout": openai.Timeout(timeout_seconds, connect=self._connect_timeout_seconds or timeout_seconds),
        }
        if response_format is not None:
            request["response_format"] = response_format
        if extra_body:
            request["extra_body"] = extra_body
        if tools:
            request["tools"] = tools
        try:
            response = self._client.chat.completions.create(**request)
        except openai.APITimeoutError as exc:
            # A timeout is a subclass of a connection error; only a read timeout means the server was reached.
            # By class name: the SDK bundles its own HTTP client, so its exception classes are not importable stably.
            if "ConnectTimeout" in {cls.__name__ for cls in type(exc.__cause__).__mro__}:
                raise MachineReviewProviderUnavailableError(
                    "The model server did not accept the connection in time."
                ) from exc
            raise MachineReviewProviderTimeoutError("The model server did not answer within the timeout.") from exc
        except openai.APIConnectionError as exc:
            raise MachineReviewProviderUnavailableError("The model server could not be reached.") from exc
        except openai.APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise MachineReviewProviderAuthError(
                    f"The model server refused the credentials (HTTP {exc.status_code})."
                ) from exc
            text = _error_text(exc)
            if tools and exc.status_code in (400, 422, 500, 501) and "tool" in text:
                raise ToolsUnsupportedError("The model server does not accept tools.") from exc
            if exc.status_code == 400 and ("context size" in text or "exceed_context_size" in text):
                raise ModelContextExceededError("The request did not fit the model's context window.") from exc
            if exc.status_code >= 500:
                raise MachineReviewProviderUnavailableError(
                    f"The model server answered HTTP {exc.status_code}."
                ) from exc
            raise
        return extract_turn(response.model_dump())

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        """Send one completion and return the reply text.

        Raises on any transport failure or unusable body. The caller converts
        that into a failed advisory review; nothing here repairs or retries
        beyond what the client does for connection-level faults, because a
        retry policy for a *model* call belongs where the cost of a second call
        is known.

        ``max_tokens`` rather than ``max_completion_tokens``: the former is what
        the compatible endpoints above accept, and the latter is OpenAI-only.
        A deployment on a model that rejects ``max_tokens`` gets the server's
        own error text back through the advisory failure path, which names the
        parameter to use -- a legible failure rather than a silent one.
        """
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_output_tokens,
            response_format={"type": "json_object"},
            timeout=timeout_seconds,
        )
        # Dumped to a plain dict so extraction is exercised by a recorded JSON
        # fixture in CI -- the same function, on the same shape, with no
        # network and no key.
        return extract_text(response.model_dump())


@dataclass(frozen=True)
class ChatTurn:
    """One assistant reply: the answer, any separate reasoning, and what the server reported."""

    content: str
    reasoning_content: str | None
    finish_reason: str | None
    usage: dict[str, Any] | None
    tool_calls: tuple["ToolCall", ...] = ()


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model asked for; ``arguments`` is the raw JSON text it sent."""

    id: str
    name: str
    arguments: str


def _tool_calls(message: dict[str, Any]) -> tuple[ToolCall, ...]:
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw, start=1):
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            continue
        arguments = function.get("arguments")
        call_id = item.get("id")
        calls.append(
            ToolCall(
                id=call_id if isinstance(call_id, str) and call_id else f"call_{index}",
                name=function["name"],
                arguments=arguments if isinstance(arguments, str) else "{}",
            )
        )
    return tuple(calls)


_LEADING_THINK = re.compile(r"\A\s*<think>(.*?)</think>", re.DOTALL)


def _first_choice(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(choice, message)`` of a chat-completions response, or raise ``ValueError``."""
    if not isinstance(payload, dict):
        raise ValueError(
            "Model response was not a JSON object; cannot extract reply text."
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(
            "Model response carried no 'choices'; cannot extract reply text."
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Model response choice was not an object.")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Model response choice carried no 'message' object.")
    return choice, message


def _check_complete(choice: dict[str, Any], message: dict[str, Any], *, thinking_note: bool) -> None:
    """Raise the named fault for a refused or cut-off reply.

    Both are tested before the text is returned, because each still carries content (incomplete
    JSON, or an explanation) that would otherwise be reported as a generic parse failure.
    """
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        raise ModelRefusedError(f"Model declined to review: {refusal}")

    if choice.get("finish_reason") == "length":
        hint = "Raise LLM_PRECHECK_MAX_OUTPUT_TOKENS."
        if thinking_note:
            hint = (
                "The reply was spent on reasoning; lower LLM_PRECHECK_THINKING_BUDGET_TOKENS "
                "or raise LLM_PRECHECK_MAX_OUTPUT_TOKENS."
            )
        raise ModelOutputTruncatedError(
            f"Model response was cut off at the output token ceiling; the review is incomplete. {hint}"
        )


def extract_text(payload: Any) -> str:
    """Return the assistant message text of a chat-completions response.

    Split out from the request so a recorded response fixture exercises exactly
    the same extraction CI cannot otherwise reach.

    The order of the checks is deliberate. Truncation and refusal are both
    tested BEFORE the text is returned, because each produces a reply that
    still carries content -- incomplete JSON in one case, an explanation in the
    other -- and handing either on would launder a known, named fault into a
    generic parse failure one layer up.
    """
    choice, message = _first_choice(payload)
    _check_complete(choice, message, thinking_note=False)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "Model response carried no message content; nothing to review with."
        )
    return content


def extract_turn(payload: Any) -> ChatTurn:
    """Return the reply turn of a chat-completions response.

    Same checks, in the same order, as :func:`extract_text`. A server that reports its reasoning
    separately fills ``reasoning_content``; one that inlines it as a leading ``<think>`` block has
    the block stripped from ``content`` (an unclosed block is an error, because everything after it
    is unusable). The reasoning is returned, never interpreted.
    """
    choice, message = _first_choice(payload)
    reasoning = message.get("reasoning_content")
    reasoning = reasoning if isinstance(reasoning, str) and reasoning else None
    content = message.get("content")
    has_content = isinstance(content, str) and bool(content.strip())
    tool_calls = _tool_calls(message)
    _check_complete(choice, message, thinking_note=not has_content and reasoning is not None)
    if not has_content and tool_calls:
        usage = payload.get("usage")
        finish = choice.get("finish_reason")
        return ChatTurn(
            content="",
            reasoning_content=reasoning,
            finish_reason=finish if isinstance(finish, str) else None,
            usage=usage if isinstance(usage, dict) else None,
            tool_calls=tool_calls,
        )
    if not has_content:
        raise ValueError(
            "Model response carried no message content; nothing to review with."
        )
    assert isinstance(content, str)
    match = _LEADING_THINK.match(content)
    if match is not None:
        reasoning = reasoning or match.group(1).strip() or None
        content = content[match.end():].lstrip()
    elif content.lstrip().startswith("<think>"):
        raise ValueError("Model response opened a <think> block that was never closed.")
    if not content.strip() and not tool_calls:
        raise ValueError(
            "Model response carried no message content; nothing to review with."
        )
    usage = payload.get("usage")
    finish = choice.get("finish_reason")
    return ChatTurn(
        content=content,
        reasoning_content=reasoning,
        finish_reason=finish if isinstance(finish, str) else None,
        usage=usage if isinstance(usage, dict) else None,
        tool_calls=tool_calls,
    )


__all__ = [
    "DEFAULT_API_BASE_URL",
    "ChatTurn",
    "ModelContextExceededError",
    "ModelOutputTruncatedError",
    "ModelRefusedError",
    "OpenAICompatibleClient",
    "ToolCall",
    "ToolsUnsupportedError",
    "extract_text",
    "extract_turn",
]
