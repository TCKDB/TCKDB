"""Producer interface for private record-level machine-review generation.

This module defines the **seam** between "what evidence was reviewed"
(:class:`~app.services.machine_review.context_hash.MachineReviewEvidenceContext`)
and "the review that resulted"
(:class:`~app.services.machine_review.read_model.RecordMachineReview`). The
orchestration driver depends on this :class:`MachineReviewProducer` protocol
rather than constructing reviews inline, so a future real provider can be
slotted in without touching orchestration.

This slice ships **only** a deterministic :class:`FakeMachineReviewProducer`
(private, for tests and orchestration smoke runs). No real, cloud, or local-HTTP
provider is implemented here, and nothing calls one. A producer signals it could
not produce a review by raising :class:`MachineReviewProductionError`, which the
orchestration layer turns into ``failed_to_produce_review`` (appending no row).

A producer is pure with respect to persistence: it reads the evidence context
and returns a review object. It performs no database access and mutates nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.services.machine_review.context_hash import MachineReviewEvidenceContext
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.schemas import (
    CuratorPriority,
    MachineReviewFinding,
    MachineReviewStatus,
)

# Obvious placeholder provenance so a fake-produced row is never mistaken for a
# real-provider result.
_FAKE_MODEL = "fake-test"
_FAKE_PROVIDER = "fake"


class MachineReviewProductionError(Exception):
    """Raised when a machine-review producer cannot produce a review.

    The orchestration layer catches this and reports
    ``failed_to_produce_review`` without appending a row. It is the documented
    failure signal a producer (fake or, later, real) uses instead of returning
    a malformed review.
    """


@runtime_checkable
class MachineReviewProducer(Protocol):
    """Producer interface for private record-level machine-review generation.

    An implementation turns a deterministic-evidence context into a
    record-scoped review for the supplied ``reviewed_at`` clock. It must either
    return a valid :class:`RecordMachineReview` (with ``record_type`` /
    ``record_ref`` matching the context and ``reviewed_at`` set) or raise
    :class:`MachineReviewProductionError`. It performs no persistence.
    """

    def review_record(
        self,
        context: MachineReviewEvidenceContext,
        *,
        reviewed_at: datetime,
    ) -> RecordMachineReview:
        """Produce a record-scoped machine-review result from evidence context."""
        ...


class FakeMachineReviewProducer:
    """Deterministic fake producer for tests and private orchestration smoke runs.

    Defaults are benign — ``machine_screened_pass`` with no findings, stamped
    ``fake-test`` / ``fake`` — so a smoke run never looks like a real verdict.
    Tests may override ``status`` / ``findings`` / ``curator_priority`` to drive
    specific cases, or set ``raise_error=True`` to simulate a production failure
    (the producer then raises :class:`MachineReviewProductionError`).
    """

    def __init__(
        self,
        *,
        status: MachineReviewStatus = MachineReviewStatus.machine_screened_pass,
        findings: tuple[MachineReviewFinding, ...] = (),
        curator_priority: CuratorPriority | None = None,
        model: str | None = _FAKE_MODEL,
        provider: str | None = _FAKE_PROVIDER,
        raise_error: bool = False,
    ) -> None:
        self._status = status
        self._findings = tuple(findings)
        self._curator_priority = curator_priority
        self._model = model
        self._provider = provider
        self._raise_error = raise_error

    def review_record(
        self,
        context: MachineReviewEvidenceContext,
        *,
        reviewed_at: datetime,
    ) -> RecordMachineReview:
        """Return a deterministic review for the context, or raise on failure.

        The review's ``record_type`` / ``record_ref`` are copied from the
        evidence context, so it addresses exactly the reviewed record. With
        ``raise_error=True`` it raises :class:`MachineReviewProductionError`
        instead, exercising the orchestration failure path.
        """
        if self._raise_error:
            raise MachineReviewProductionError(
                "fake producer configured to fail"
            )
        return RecordMachineReview(
            record_type=context.record_type,
            record_ref=context.record_ref,
            status=self._status,
            findings=self._findings,
            curator_priority=self._curator_priority,
            model=self._model,
            provider=self._provider,
            reviewed_at=reviewed_at,
        )
