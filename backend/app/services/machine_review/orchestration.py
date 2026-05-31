"""Private fake/supplied-review machine-review orchestration driver.

This module performs the **full private machine-review loop for one record**,
explicitly invoked, using only a fake or caller-supplied review — never a real
provider (policy ``backend/docs/specs/record_machine_review_policy.md`` §5)::

    live deterministic trust/evidence (TrustFragment)
      -> MachineReviewEvidenceContext        (context adapter)
      -> MachineReviewContextDigest           (hash builder)
      -> re-review plan                       (planner)
      -> fake / supplied RecordMachineReview
      -> execute_record_machine_rereview_plan (executor; the sole write path)
      -> appended row OR skipped_current

It is a thin composition of already-tested pieces; it adds no new currency,
mapping, or persistence logic. It is allowed to append exactly one
``record_machine_review`` row, but **only** through the executor. It mutates
nothing else — not ``review_status`` / ``benchmark_reference`` / ``is_certified``,
deterministic evidence/trust, scientific records, ``submission.status`` /
approval / rejection fields, nor the public ``TrustFragment`` (which it only
reads). Nothing here is wired into uploads, a background worker, or any public
read; no ``trust.machine_review`` is emitted.

No real provider is imported or called. A default benign review is synthesised
only when a ``reviewed_at`` clock is supplied (the project avoids wall-clock);
otherwise the caller must supply ``fake_review``. If neither is available the
run reports ``failed_to_produce_review`` and appends nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.services.machine_review.context_adapter import (
    build_machine_review_evidence_context_from_trust,
)
from app.services.machine_review.context_hash import (
    MachineReviewEvidenceContext,
    build_machine_review_context_hash,
)
from app.services.machine_review.producer import (
    FakeMachineReviewProducer,
    MachineReviewProducer,
    MachineReviewProductionError,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    plan_record_machine_rereview,
)
from app.services.machine_review.rereview_execution import (
    MachineReviewReReviewExecutionStatus,
    execute_record_machine_rereview_plan,
)
from app.services.trust.models import TrustFragment


class MachineReviewOrchestrationStatus(str, Enum):
    """Private outcome of an explicitly invoked machine-review orchestration run.

    ``skipped_current`` — the plan (or the executor's idempotency guard) found
    the record already current; nothing was appended. ``appended`` — exactly one
    row was appended. ``failed_to_produce_review`` — a re-review was required but
    the producer could not produce a review (it raised
    :class:`~app.services.machine_review.producer.MachineReviewProductionError`
    or returned invalid output); nothing was appended. This is a driver-side
    production failure, never a record verdict.
    """

    skipped_current = "skipped_current"
    appended = "appended"
    failed_to_produce_review = "failed_to_produce_review"


class MachineReviewOrchestrationResult(BaseModel):
    """Private result of an explicitly invoked machine-review orchestration run.

    Reports the orchestration ``status`` and the plan ``decision`` it acted on,
    the underlying ``execution_status`` (``None`` when the executor was not
    reached), the appended row id when one was written, the record it concerns,
    and the live currency key used. ``extra="forbid"`` so it can carry no
    mutation instruction — it reports an outcome, it does not perform one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MachineReviewOrchestrationStatus
    decision: MachineReviewReReviewDecision
    execution_status: MachineReviewReReviewExecutionStatus | None = None
    appended_review_id: int | None = None
    record_type: str
    record_id: int
    context_hash: str
    context_schema_version: str
    prompt_version: str
    rubric_versions: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None


def _run_with_produce(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    record_ref: str | None,
    trust_fragment: TrustFragment,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
    produce: Callable[[MachineReviewEvidenceContext], RecordMachineReview],
    source_submission_id: int | None = None,
    source_audit_event_id: int | None = None,
) -> MachineReviewOrchestrationResult:
    """Shared private orchestration core: plan, produce (for run_*), execute.

    ``produce`` is the only injected variation: it turns the evidence context
    into a :class:`RecordMachineReview`, or raises
    :class:`MachineReviewProductionError` to signal it cannot. It is invoked
    **only** when the plan requires a review (never for ``skip_current``), so a
    current record never triggers production. A raised
    ``MachineReviewProductionError`` — or an invalid review (wrong record, or no
    ``reviewed_at``) — yields ``failed_to_produce_review`` with no row appended.
    The append goes through the executor (the sole write path), so an unchanged
    recipe is idempotent. Nothing outside ``record_machine_review`` is mutated.
    """
    effective_ref = record_ref if record_ref is not None else str(record_id)

    context = build_machine_review_evidence_context_from_trust(
        record_type=record_type,
        record_ref=effective_ref,
        trust_fragment=trust_fragment,
    )
    digest = build_machine_review_context_hash(context)

    plan = plan_record_machine_rereview(
        session,
        record_type=record_type,
        record_id=record_id,
        current_context=digest,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
    )

    def _result(
        status: MachineReviewOrchestrationStatus,
        *,
        execution_status: MachineReviewReReviewExecutionStatus | None = None,
        appended_review_id: int | None = None,
        summary: str | None = None,
    ) -> MachineReviewOrchestrationResult:
        return MachineReviewOrchestrationResult(
            status=status,
            decision=plan.decision,
            execution_status=execution_status,
            appended_review_id=appended_review_id,
            record_type=record_type,
            record_id=record_id,
            context_hash=plan.current_context_hash,
            context_schema_version=plan.context_schema_version,
            prompt_version=plan.prompt_version,
            rubric_versions=dict(plan.rubric_versions),
            summary=summary,
        )

    # Plan says the record is already current -> never call the producer.
    if plan.decision is MachineReviewReReviewDecision.skip_current:
        return _result(MachineReviewOrchestrationStatus.skipped_current)

    # run_not_reviewed / run_stale -> produce a review (failure => no append).
    try:
        review = produce(context)
    except MachineReviewProductionError as exc:
        return _result(
            MachineReviewOrchestrationStatus.failed_to_produce_review,
            summary=f"producer failed: {exc}",
        )
    if not isinstance(review, RecordMachineReview) or review.reviewed_at is None:
        return _result(
            MachineReviewOrchestrationStatus.failed_to_produce_review,
            summary="producer returned invalid output",
        )

    # Execute through the sole write path (re-checks currency, appends/skips).
    execution = execute_record_machine_rereview_plan(
        session,
        record_type=record_type,
        record_id=record_id,
        plan=plan,
        review=review,
        source_submission_id=source_submission_id,
        source_audit_event_id=source_audit_event_id,
    )

    status = (
        MachineReviewOrchestrationStatus.appended
        if execution.status is MachineReviewReReviewExecutionStatus.appended
        else MachineReviewOrchestrationStatus.skipped_current
    )
    return _result(
        status,
        execution_status=execution.status,
        appended_review_id=execution.appended_review_id,
        summary=(
            f"machine review: status={review.status.value}, "
            f"findings={len(review.findings)}"
        ),
    )


def run_record_machine_review_with_producer(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    record_ref: str | None,
    trust_fragment: TrustFragment,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
    producer: MachineReviewProducer,
    reviewed_at: datetime,
    source_submission_id: int | None = None,
    source_audit_event_id: int | None = None,
) -> MachineReviewOrchestrationResult:
    """Run the private machine-review loop using an injected producer.

    Builds the live evidence context/digest, plans re-review, and — only for
    ``run_not_reviewed`` / ``run_stale`` — calls ``producer.review_record`` for
    the supplied ``reviewed_at`` clock, then appends through the executor. A
    producer that raises :class:`MachineReviewProductionError` (or returns
    invalid output) yields ``failed_to_produce_review`` and appends nothing.
    ``skip_current`` never calls the producer. This is the general seam for a
    future real producer; this slice supplies only fake producers.
    """
    return _run_with_produce(
        session,
        record_type=record_type,
        record_id=record_id,
        record_ref=record_ref,
        trust_fragment=trust_fragment,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
        produce=lambda ctx: producer.review_record(ctx, reviewed_at=reviewed_at),
        source_submission_id=source_submission_id,
        source_audit_event_id=source_audit_event_id,
    )


def run_fake_record_machine_review(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    record_ref: str | None,
    trust_fragment: TrustFragment,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
    fake_review: RecordMachineReview | None = None,
    reviewed_at: datetime | None = None,
    source_submission_id: int | None = None,
    source_audit_event_id: int | None = None,
) -> MachineReviewOrchestrationResult:
    """Run the private fake/supplied machine-review loop for one record.

    A thin wrapper over :func:`_run_with_produce` (the producer-based core) that
    preserves the original fake/supplied semantics:

    * a supplied ``fake_review`` is used verbatim;
    * otherwise a :class:`FakeMachineReviewProducer` synthesises a benign default
      when ``reviewed_at`` is given;
    * otherwise (no review, no clock) production fails ->
      ``failed_to_produce_review`` (only reachable for ``run_*``; a current
      record still skips without needing a clock).

    The default-review path now goes through the producer interface rather than
    being constructed inline. No real provider is called.
    """

    def _produce(context: MachineReviewEvidenceContext) -> RecordMachineReview:
        if fake_review is not None:
            return fake_review
        if reviewed_at is None:
            raise MachineReviewProductionError(
                "no fake_review supplied and no reviewed_at clock to "
                "synthesise one"
            )
        return FakeMachineReviewProducer().review_record(
            context, reviewed_at=reviewed_at
        )

    return _run_with_produce(
        session,
        record_type=record_type,
        record_id=record_id,
        record_ref=record_ref,
        trust_fragment=trust_fragment,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
        produce=_produce,
        source_submission_id=source_submission_id,
        source_audit_event_id=source_audit_event_id,
    )
