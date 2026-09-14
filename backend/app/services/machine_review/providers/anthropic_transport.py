"""The default cloud transport: Anthropic Messages over ``httpx``.

One implementation of
:class:`~app.services.machine_review.providers.cloud.MachineReviewModelClient`.
It is the *default* transport, not a required one -- the provider takes whatever
satisfies the protocol, which is how CI runs the real prompt and the real parse
against a committed fixture with no network.

**Why a thin client rather than the vendor SDK.** The backend ships as an arm64
image built under QEMU for a Raspberry Pi, and this repository is public; a
dependency that only one optional mode uses should not be in either the build or
the audit surface of every deployment. The Messages API call this needs is one
POST with a JSON body, so the SDK would buy retry/streaming/typing that this
provider does not use. ``httpx`` is declared under the optional ``llm`` extra
and imported **inside** the constructor, so an install without it is fine until
somebody actually sets ``AI_REVIEW_ASSISTANT_MODE=cloud`` -- and then gets a
configuration error naming the extra, not an ``ImportError`` from deep in a
request.

The key is read from the environment variable named by
``LLM_PRECHECK_API_KEY_ENV`` and is never logged, never stored, and never put in
an exception message.
"""

from __future__ import annotations

from typing import Any

from app.services.machine_review.providers.interface import (
    MachineReviewProviderConfigurationError,
)

#: Anthropic's Messages endpoint. Overridable per instance so a compatible
#: gateway or a proxy can be pointed at without a code change.
DEFAULT_API_BASE_URL = "https://api.anthropic.com"

#: The API version header the Messages API requires. Pinned rather than tracked:
#: a silent bump could change the response shape under a stored prompt version.
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicMessagesClient:
    """Calls the Anthropic Messages API and returns the reply text."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_API_BASE_URL,
    ) -> None:
        try:
            import httpx
        except ModuleNotFoundError as exc:  # pragma: no cover - install-shaped
            raise MachineReviewProviderConfigurationError(
                "Cloud machine review needs the 'llm' extra for its HTTP "
                "transport: install the backend with the [llm] extra, or "
                "inject a client that satisfies MachineReviewModelClient."
            ) from exc
        self._httpx = httpx
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        """POST one message and return the concatenated text blocks.

        Raises on any non-2xx status or unusable body. The caller converts that
        into a failed advisory review; nothing here repairs or retries, because
        a retry policy belongs where the cost of a second call is known.
        """
        response = self._httpx.post(
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_output_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        return extract_text(response.json())


def extract_text(payload: Any) -> str:
    """Concatenate the text blocks of a Messages response.

    Split out from the request so a recorded response fixture exercises exactly
    the same extraction CI cannot otherwise reach. A reply carrying no text
    block is a failure, not an empty review: an empty string would parse as
    invalid JSON and be reported as malformed output, which describes the wrong
    fault.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            "Model response was not a JSON object; cannot extract reply text."
        )
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise ValueError(
            "Model response carried no 'content' list; cannot extract reply text."
        )
    parts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    if not parts:
        raise ValueError(
            "Model response carried no text block; nothing to review with."
        )
    return "".join(parts)


__all__ = [
    "ANTHROPIC_VERSION",
    "DEFAULT_API_BASE_URL",
    "AnthropicMessagesClient",
    "extract_text",
]
