"""The cloud machine-review provider: one model call, strictly parsed.

Phase (a) of ``docs/plans/machine_review_llm_implementation.md``. Until this
module existed the factory validated cloud configuration and then raised
``NotImplementedError``; this is the call it was standing in for.

**The transport is injected, not imported.** :class:`MachineReviewModelClient`
is a two-method protocol, and the provider never learns whether the other side
is an HTTP call, a recorded fixture, or a stub. Three things follow, and all
three are the reason for the shape:

* CI exercises the real parse and the real prompt against a **committed
  response fixture**, so the wire shape is covered with no network and no key
  (plan §7.1: "CI never calls the API").
* No vendor SDK becomes a hard dependency of the backend. The default transport
  (:mod:`app.services.machine_review.providers.anthropic_transport`) is a thin
  ``httpx`` client behind an optional extra, imported only when cloud mode is
  actually selected.
* A local/self-hosted transport is the same protocol, which is what Phase (a)'s
  sibling needs.

**What the provider trusts and what it does not.** The model's output is
untrusted: it goes through :func:`parse_machine_review_v2_payload`, the single
strict boundary, whose ``extra="forbid"`` refuses any mutation field and whose
``Literal[False]`` refuses a RAG claim. What the provider does *not* take from
the model is its own identity -- ``model`` and ``provider`` are stamped from
configuration afterwards, overwriting whatever the payload claimed. A reviewer
reading a stored review needs to know which model produced it, and the model is
not the authority on that.

Errors are **raised, not swallowed**. A transport failure, a timeout, malformed
JSON or a contract violation all propagate to the service layer, which converts
them into an advisory ``machine_review_failed`` result. That conversion lives
in one place on purpose (plan §7.1: "never a raise past the boundary, never an
upload failure"), and a provider that quietly returned a pass on error would
defeat it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    parse_machine_review_v2_payload,
)
from app.services.machine_review.providers.prompt import (
    MACHINE_REVIEW_SYSTEM_PROMPT,
    build_user_message,
)
from app.services.machine_review.schemas import MachineReviewProviderResultV2

#: Ceiling on the model's reply. The contract caps findings at 50, each message
#: at 1000 characters, so a well-formed maximal answer is far below this; the
#: limit exists to bound a runaway generation, not to shape the answer.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

#: Wall-clock ceiling for one review. Machine review is advisory and runs out of
#: band, so a slow answer is worth waiting for -- but not unboundedly, because
#: the caller is holding a worker.
DEFAULT_TIMEOUT_SECONDS = 120.0


@runtime_checkable
class MachineReviewModelClient(Protocol):
    """The transport a cloud provider talks through.

    Deliberately narrow: one call, text in and text out. Everything the
    machine-review layer cares about -- the contract, the prompt, the parse --
    lives on this side of it, so a transport cannot change the meaning of a
    review, only how the bytes were fetched.
    """

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        """Return the model's raw reply text, or raise.

        Implementations raise on transport failure rather than returning a
        sentinel: the service layer converts an exception into a failed
        advisory review, and a sentinel would have to be distinguished from a
        real answer somewhere further in.
        """


class CloudMachineReviewProvider:
    """Reviews a submission with one hosted-model call.

    Satisfies
    :class:`~app.services.machine_review.providers.interface.MachineReviewProvider`.
    Holds no state between reviews and performs no I/O of its own beyond the
    injected client.
    """

    def __init__(
        self,
        *,
        client: MachineReviewModelClient,
        model: str,
        provider_name: str = "CloudMachineReviewProvider",
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._model = model
        self._provider_name = provider_name
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    def review_submission(
        self,
        context: MachineReviewContext,
    ) -> MachineReviewProviderResultV2:
        """Run one review and return the validated v2 result."""
        raw = self._client.complete(
            model=self._model,
            system=MACHINE_REVIEW_SYSTEM_PROMPT,
            user=build_user_message(context),
            max_output_tokens=self._max_output_tokens,
            timeout_seconds=self._timeout_seconds,
        )
        result = parse_machine_review_v2_payload(raw)
        # Identity is stamped, never accepted. A model asked to name itself can
        # be wrong or stale, and a stored review that misattributes its author
        # cannot be re-run or compared against a later one.
        return result.model_copy(
            update={"model": self._model, "provider": self._provider_name}
        )


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
    "CloudMachineReviewProvider",
    "MachineReviewModelClient",
]
