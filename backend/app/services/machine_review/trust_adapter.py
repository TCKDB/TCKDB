"""Private trust-envelope adapter: machine review *beside* deterministic evidence.

This module proves that a future ``trust.machine_review`` block can be
assembled **next to** the deterministic evidence/trust evaluator output —
without altering, recomputing, or perturbing that output, and without touching
the public :class:`~app.services.trust.models.TrustFragment` response shape.

It is **private/internal plumbing only** (spec
``backend/docs/specs/provisional_machine_review.md`` §4/§10). Nothing here is
wired into any scientific read route, no ``include=machine_review`` flag is
added, and the public ``trust`` fragment is unchanged. The envelope this builds
is a *future-facing projection*, prepared so the direction is type-safe and
testable, deliberately not exposed.

Core invariant
--------------

The deterministic evaluator output must be **byte-identical** whether or not a
machine-review projection is assembled. This adapter never calls the evaluator,
never rebuilds the evidence dict, and never mutates its inputs: it accepts the
*already-built* public :class:`TrustFragment` and copies its
``review_status`` / ``trust_status`` / ``evidence`` / ``llm_precheck`` /
``is_certified`` through verbatim, attaching only an additional
``machine_review`` summary. Preservation is therefore structural, not a runtime
coincidence.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.services.machine_review.read_model import (
    MachineReviewRecordSummary,
    RecordMachineReview,
    build_machine_review_record_summary,
)
from app.services.machine_review.schemas import MachineReviewStatus
from app.services.trust.models import TrustFragment, TrustLLMPrecheck


class InternalTrustEnvelopeWithMachineReview(BaseModel):
    """Private future-facing trust envelope. **Not** a public API schema.

    Mirrors the public :class:`~app.services.trust.models.TrustFragment` field
    set exactly and adds a single ``machine_review`` block beside it. It exists
    only to demonstrate the future assembly shape (spec §4 sample); no read
    route returns it. ``extra="forbid"`` plus ``frozen`` guarantee it can carry
    no provider-supplied mutation payload and cannot be mutated after
    construction.

    The deterministic fields (``review_status``, ``trust_status``,
    ``evidence``, ``llm_precheck``, ``is_certified``) are copied verbatim from
    the supplied :class:`TrustFragment` — this envelope never recomputes them,
    so the evaluator output it wraps stays byte-identical.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_status: str = "not_reviewed"
    trust_status: str
    evidence: dict
    llm_precheck: TrustLLMPrecheck = Field(default_factory=TrustLLMPrecheck)
    machine_review: MachineReviewRecordSummary
    is_certified: bool = False


def build_internal_machine_review_trust_fragment(
    *,
    reviews: Sequence[RecordMachineReview] = (),
    record_type: str | None = None,
    record_ref: str | None = None,
    summary: MachineReviewRecordSummary | None = None,
) -> MachineReviewRecordSummary:
    """Build the private ``machine_review`` fragment for one record.

    Reuses :class:`MachineReviewRecordSummary` (already ``extra="forbid"``,
    already mutation-free) as the fragment shape rather than introducing a
    near-duplicate type.

    Resolution policy:

    * ``summary`` supplied -> returned as-is (a precomputed latest summary).
    * no ``reviews`` -> a ``status=not_run`` summary (no machine review exists
      for the record; ``not_run`` is the default state).
    * one or more ``reviews`` -> the latest is selected via the existing
      read-model helper
      (:func:`~app.services.machine_review.read_model.build_machine_review_record_summary`),
      which requires the target ``record_type`` / ``record_ref`` to scope and
      tie-break the selection.

    This never mutates the supplied reviews and performs no persistence.
    """
    if summary is not None:
        return summary

    if not reviews:
        # No record-level machine review exists -> the absence state, distinct
        # from machine_review_failed (a review ran but could not complete).
        return MachineReviewRecordSummary(status=MachineReviewStatus.not_run)

    if record_type is None or record_ref is None:
        raise ValueError(
            "record_type and record_ref are required to select the latest "
            "machine review among multiple reviews."
        )

    return build_machine_review_record_summary(
        record_type=record_type, record_ref=record_ref, reviews=reviews
    )


def build_private_trust_envelope_with_machine_review(
    *,
    trust_fragment: TrustFragment,
    reviews: Sequence[RecordMachineReview] = (),
    record_type: str | None = None,
    record_ref: str | None = None,
    machine_review_summary: MachineReviewRecordSummary | None = None,
) -> InternalTrustEnvelopeWithMachineReview:
    """Assemble a private envelope: the supplied trust fragment + machine review.

    The deterministic half of the envelope is the supplied **public**
    :class:`TrustFragment`, copied field-for-field — so ``review_status``,
    ``trust_status``, ``evidence``, ``llm_precheck`` (its disabled/``not_run``
    default included), and ``is_certified`` are all preserved *exactly as
    supplied*. The only thing added is the ``machine_review`` summary, built
    via :func:`build_internal_machine_review_trust_fragment`.

    Pass either a precomputed ``machine_review_summary`` or the
    ``reviews`` (with ``record_type`` / ``record_ref`` when more than zero) to
    let the read model select the latest. This function mutates nothing and
    builds no public output.
    """
    machine_review = build_internal_machine_review_trust_fragment(
        reviews=reviews,
        record_type=record_type,
        record_ref=record_ref,
        summary=machine_review_summary,
    )

    return InternalTrustEnvelopeWithMachineReview(
        review_status=trust_fragment.review_status,
        trust_status=trust_fragment.trust_status,
        evidence=trust_fragment.evidence,
        llm_precheck=trust_fragment.llm_precheck,
        machine_review=machine_review,
        is_certified=trust_fragment.is_certified,
    )
