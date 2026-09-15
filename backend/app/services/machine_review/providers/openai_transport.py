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
:func:`~app.services.machine_review.providers.interface.parse_machine_review_v2_payload`,
whose ``extra="forbid"`` refuses a mutation field and whose ``Literal[False]``
refuses a RAG claim. ``json_object`` makes a well-formed answer likelier; it is
not what makes an answer safe.

The key is read from the environment variable named by
``LLM_PRECHECK_API_KEY_ENV`` and is never logged, never stored, and never put
in an exception message.
"""

from __future__ import annotations

from typing import Any

from app.services.machine_review.providers.interface import (
    MachineReviewProviderConfigurationError,
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


class OpenAICompatibleClient:
    """Calls an OpenAI-compatible chat-completions endpoint and returns the text."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        """``base_url=None`` means OpenAI's own endpoint.

        None rather than a default argument value so a caller reading
        configuration can pass ``settings.llm_precheck_base_url`` straight
        through: it is ``str | None``, and an unset setting must mean "the
        default", not a crash. One place decides what the default is.
        """
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - install-shaped
            raise MachineReviewProviderConfigurationError(
                "Cloud machine review needs the 'llm' extra for its HTTP "
                "transport: install the backend with the [llm] extra, or "
                "inject a client that satisfies MachineReviewModelClient."
            ) from exc
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or DEFAULT_API_BASE_URL,
        )

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

    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        raise ModelRefusedError(f"Model declined to review: {refusal}")

    if choice.get("finish_reason") == "length":
        raise ModelOutputTruncatedError(
            "Model response was cut off at the output token ceiling; the "
            "review is incomplete. Raise LLM_PRECHECK_MAX_OUTPUT_TOKENS."
        )

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "Model response carried no message content; nothing to review with."
        )
    return content


__all__ = [
    "DEFAULT_API_BASE_URL",
    "ModelOutputTruncatedError",
    "ModelRefusedError",
    "OpenAICompatibleClient",
    "extract_text",
]
