"""Provider interface + boundary helpers for native v2 machine review.

This module defines the *producer* boundary for machine review (the consumer
side — adapter, mapping, inspection, curator tasks — already exists). It holds:

* :class:`MachineReviewContext` — a minimal internal context a provider reviews
  over. In this foundation slice it simply wraps/references the existing
  :class:`~app.services.llm_precheck.schemas.LLMPrecheckContext`; the richer
  context builder described in
  ``backend/docs/specs/machine_review_real_provider_plumbing.md`` §7 is a later
  slice and is deliberately *not* built here.
* :class:`MachineReviewProvider` — the provider protocol. Every provider returns
  a validated :class:`~app.services.machine_review.schemas.MachineReviewProviderResultV2`.
* :class:`MachineReviewProviderConfigurationError` — raised when a configured
  mode (cloud/local) is missing required configuration. A subclass of the v1
  :class:`~app.services.llm_precheck.providers.LLMPrecheckConfigurationError` so
  the existing service-layer ``except`` paths (which convert it to a failed
  advisory result) keep working unchanged.
* :func:`parse_machine_review_v2_payload` — the single strict-parse / trust
  boundary: untrusted raw model output (``str`` or ``dict``) in, a validated v2
  model out, or an exception the caller converts to a failed review.
* :func:`machine_review_v2_result_to_details_json` — serialize a validated v2
  result into the ``submission_audit_event.details_json`` shape the adapter's v2
  path consumes. This is the only persistence-edge helper added in this slice;
  upload/precheck wiring of it is a later integration step (spec §5.2).

Configuration namespace: ``AI_REVIEW_ASSISTANT_MODE`` + ``LLM_PRECHECK_*``
remains the implementation/config namespace.
:class:`~app.services.machine_review.schemas.MachineReviewProviderResultV2` is
the output contract. ``machine_review`` is the future *public* concept. No
parallel ``MACHINE_REVIEW_*`` env vars are introduced (spec §4).

No real provider calls, no persistence, no public exposure here.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.services.llm_precheck.providers import LLMPrecheckConfigurationError
from app.services.llm_precheck.schemas import LLMPrecheckContext
from app.services.machine_review.schemas import MachineReviewProviderResultV2


class MachineReviewProviderConfigurationError(LLMPrecheckConfigurationError):
    """Raised when a configured machine-review mode is not usable.

    Subclasses the v1 :class:`LLMPrecheckConfigurationError` so any caller that
    already catches the v1 error (converting it into an advisory failed result)
    handles this one too, while machine-review code can still be specific.
    """


class MachineReviewContext(BaseModel):
    """Minimal internal context a machine-review provider reviews over.

    Intentionally thin for this foundation slice: it carries the
    ``submission_id`` and optionally references the existing
    :class:`LLMPrecheckContext` so a future provider has the compact submission
    metadata + record refs already assembled by
    :func:`~app.services.llm_precheck.context_builder.build_llm_precheck_context`.
    The deeper, evidence-rich context builder (spec §7) is a later slice.
    """

    model_config = ConfigDict(frozen=True)

    submission_id: int
    precheck_context: LLMPrecheckContext | None = None

    @classmethod
    def from_llm_precheck_context(
        cls,
        context: LLMPrecheckContext,
    ) -> "MachineReviewContext":
        """Wrap an existing :class:`LLMPrecheckContext` without re-querying."""
        return cls(
            submission_id=context.submission_id,
            precheck_context=context,
        )


@runtime_checkable
class MachineReviewProvider(Protocol):
    """Provider interface for native v2 machine-review results."""

    def review_submission(
        self,
        context: MachineReviewContext,
    ) -> MachineReviewProviderResultV2:
        """Return a validated native v2 machine-review result.

        Implementations may receive raw model output but MUST return a
        schema-validated :class:`MachineReviewProviderResultV2` (e.g. via
        :func:`parse_machine_review_v2_payload`). A provider never persists,
        never mutates, and never raises for model misbehavior — malformed
        output is converted to a failed advisory result by the service layer,
        not by the provider.
        """


def parse_machine_review_v2_payload(
    raw: str | dict[str, Any],
) -> MachineReviewProviderResultV2:
    """Strictly parse untrusted raw model output into the v2 contract.

    The single trust boundary for provider output. ``raw`` may be a JSON string
    (parsed with :func:`json.loads`) or an already-decoded ``dict``. The result
    is validated against :class:`MachineReviewProviderResultV2`, whose
    ``extra="forbid"`` / ``Literal[False]`` ``used_rag`` constraints reject any
    mutation payload or RAG claim.

    Raises :class:`json.JSONDecodeError` (bad JSON), :class:`TypeError`
    (non-object payload), or :class:`pydantic.ValidationError` (contract
    violation). Callers convert any of these into an advisory failed review;
    this helper never silently repairs malformed output.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise TypeError(
            "machine-review v2 payload must be a JSON object, got "
            f"{type(raw).__name__}."
        )
    return MachineReviewProviderResultV2.model_validate(raw)


def machine_review_v2_result_to_details_json(
    result: MachineReviewProviderResultV2,
) -> dict[str, Any]:
    """Serialize a validated v2 result into an audit-event ``details_json`` dict.

    The dump carries ``schema_version="machine_review_v2"`` at its root, so the
    adapter's version dispatch routes it to the native v2 path with no
    label->status translation. This is the persistence-edge helper for the
    *future* integration step; wiring it into the upload/precheck flow (so a v2
    provider result lands on a ``submission_audit_event``) is intentionally not
    part of this foundation slice (spec §5.2).
    """
    return result.model_dump(mode="json")
