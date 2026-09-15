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
from app.services.machine_review.schemas import (
    MachineReviewProviderResultV2,
    MachineReviewStatus,
)


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


#: Statuses a *provider* may not claim, because they are not statements about
#: the science -- they are this system's own account of what happened to the
#: run, and only the runner is in a position to make either.
#:
#: ``not_run``            the reviewer was never asked (off mode).
#: ``machine_review_failed``  asking it did not produce a usable answer.
#:
#: :class:`MachineReviewStatus` carries all five because the *stored* review
#: uses all five; the prompt asks for three. Nothing enforced that gap, so a
#: model could pick either reserved token and have it believed all the way to
#: the surface. MEASURED consequence before this guard: a model answering
#: ``{"status": "not_run"}`` produced a recorded audit event and a page that
#: told an admin "the machine reviewer is switched off in this deployment, so
#: no provider was asked anything and nothing was recorded" -- three
#: statements, all false, none of them the model's to make. A model answering
#: ``machine_review_failed`` alongside two findings produced "no record was
#: judged" while two had been, and then made ``failed`` dominate every record
#: that pass touched.
_PROVIDER_RESERVED_STATUSES: frozenset[MachineReviewStatus] = frozenset(
    {
        MachineReviewStatus.not_run,
        MachineReviewStatus.machine_review_failed,
    }
)


class ReservedMachineReviewStatusError(ValueError):
    """A provider claimed a status that only the runner may set.

    A ``ValueError`` so the service layer's existing conversion treats it like
    any other contract violation: the review is recorded as failed, with this
    sentence as the reason. The model does not get a second attempt at
    narrating its own absence.
    """


def parse_machine_review_v2_payload(
    raw: str | dict[str, Any],
) -> MachineReviewProviderResultV2:
    """Strictly parse untrusted raw model output into the v2 contract.

    The single trust boundary for provider output. ``raw`` may be a JSON string
    (parsed with :func:`json.loads`) or an already-decoded ``dict``. The result
    is validated against :class:`MachineReviewProviderResultV2`, whose
    ``extra="forbid"`` / ``Literal[False]`` ``used_rag`` constraints reject any
    mutation payload or RAG claim, and then against
    :data:`_PROVIDER_RESERVED_STATUSES`.

    **The narrowing lives here and not on the model** because the runner builds
    a ``machine_review_failed`` result of its own on every failure path, and a
    validator on :class:`MachineReviewProviderResultV2` would refuse that too.
    The distinction being drawn is not "is this value legal" but "who is
    entitled to say it", and this function is the one place that knows the
    answer came from a model.

    Raises :class:`json.JSONDecodeError` (bad JSON), :class:`TypeError`
    (non-object payload), :class:`pydantic.ValidationError` (contract
    violation), or :class:`ReservedMachineReviewStatusError`. Callers convert
    any of these into an advisory failed review; this helper never silently
    repairs malformed output.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise TypeError(
            "machine-review v2 payload must be a JSON object, got "
            f"{type(raw).__name__}."
        )
    parsed = MachineReviewProviderResultV2.model_validate(raw)
    if parsed.status in _PROVIDER_RESERVED_STATUSES:
        raise ReservedMachineReviewStatusError(
            f"A machine-review provider may not report status "
            f"{parsed.status.value!r}: that is this system's account of what "
            f"happened to the run, not the reviewer's account of the science."
        )
    return parsed


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
