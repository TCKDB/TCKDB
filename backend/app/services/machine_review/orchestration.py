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

from collections.abc import Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.services.machine_review.context_adapter import (
    build_machine_review_evidence_context_from_trust,
)
from app.services.machine_review.context_hash import (
    build_machine_review_context_hash,
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
from app.services.machine_review.schemas import MachineReviewStatus
from app.services.trust.models import TrustFragment

# Provenance stamps for the synthesised default fake review. Deliberately
# obvious placeholders so a persisted row is never mistaken for a real-provider
# result.
_FAKE_MODEL = "fake-test"
_FAKE_PROVIDER = "fake"


class MachineReviewOrchestrationStatus(str, Enum):
    """Private outcome of an explicitly invoked machine-review orchestration run.

    ``skipped_current`` — the plan (or the executor's idempotency guard) found
    the record already current; nothing was appended. ``appended`` — exactly one
    row was appended. ``failed_to_produce_review`` — a re-review was required but
    no review could be produced (no ``fake_review`` and no clock to synthesise
    one); nothing was appended. This is a driver-side production failure, never a
    record verdict.
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


def _default_fake_review(
    *,
    record_type: str,
    record_ref: str,
    reviewed_at: datetime,
) -> RecordMachineReview:
    """Synthesise a deterministic, benign fake review (no findings, pass).

    Stamped with obvious ``fake-test`` / ``fake`` provenance and the supplied
    ``reviewed_at`` clock, so it is never mistaken for a real-provider result and
    never depends on wall-clock time.
    """
    return RecordMachineReview(
        record_type=record_type,
        record_ref=record_ref,
        status=MachineReviewStatus.machine_screened_pass,
        findings=(),
        model=_FAKE_MODEL,
        provider=_FAKE_PROVIDER,
        reviewed_at=reviewed_at,
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

    Steps (each delegates to an existing, tested helper):

    1. Build the evidence context from ``trust_fragment`` and hash it to a live
       :class:`MachineReviewContextDigest`.
    2. Plan re-review against that digest + the active recipe.
    3. ``skip_current`` -> append nothing (``skipped_current``).
    4. ``run_not_reviewed`` / ``run_stale`` -> use ``fake_review`` if supplied,
       else synthesise a benign default when ``reviewed_at`` is given, else
       report ``failed_to_produce_review`` (append nothing).
    5. Execute the plan via :func:`execute_record_machine_rereview_plan` (the
       sole write path), which re-checks currency and appends one row or skips.

    The orchestration status mirrors the executor outcome (``appended`` /
    ``skipped_current``), so an unchanged recipe is idempotent end-to-end.
    ``source_submission_id`` / ``source_audit_event_id`` are preserved. No real
    provider is called; nothing outside ``record_machine_review`` is mutated.
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

    # 3. Plan says the record is already current -> nothing to do.
    if plan.decision is MachineReviewReReviewDecision.skip_current:
        return _result(MachineReviewOrchestrationStatus.skipped_current)

    # 4. Obtain the review to persist (supplied, or a synthesised default).
    review = fake_review
    if review is None:
        if reviewed_at is None:
            # No review and no clock to synthesise one -> cannot produce.
            return _result(
                MachineReviewOrchestrationStatus.failed_to_produce_review,
                summary=(
                    "no fake_review supplied and no reviewed_at clock to "
                    "synthesise one"
                ),
            )
        review = _default_fake_review(
            record_type=record_type,
            record_ref=effective_ref,
            reviewed_at=reviewed_at,
        )

    # 5. Execute through the sole write path (re-checks currency, appends/skips).
    execution = execute_record_machine_rereview_plan(
        session,
        record_type=record_type,
        record_id=record_id,
        plan=plan,
        review=review,
        source_submission_id=source_submission_id,
        source_audit_event_id=source_audit_event_id,
    )

    if execution.status is MachineReviewReReviewExecutionStatus.appended:
        status = MachineReviewOrchestrationStatus.appended
    else:
        status = MachineReviewOrchestrationStatus.skipped_current

    return _result(
        status,
        execution_status=execution.status,
        appended_review_id=execution.appended_review_id,
        summary=(
            f"fake machine review: status={review.status.value}, "
            f"findings={len(review.findings)}"
        ),
    )
