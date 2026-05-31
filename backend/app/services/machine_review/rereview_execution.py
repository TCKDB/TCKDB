"""Private execution half of the record-level machine re-review path.

The planner (:mod:`app.services.machine_review.rereview`) decides *whether* a
record needs a fresh machine review; this module **executes** that decision by
appending one persisted row — and nothing more. It does not produce the review
(provider / fake-provider orchestration is a separate concern); it only persists
an already-built :class:`~app.services.machine_review.read_model.RecordMachineReview`
through the existing append helper (policy
``backend/docs/specs/record_machine_review_policy.md`` §5 execution half).

Boundaries:

* **Append-only, one write path.** The only mutation is a single
  :func:`~app.services.machine_review.persistence.create_record_machine_review_row`
  call (flush, no commit — the caller owns the transaction). No row is ever
  updated or deleted; the table stays append-only.
* **Idempotency guard (no locks).** Before appending, currency is re-checked for
  the exact record against the *plan's* currency key. If the record is already
  ``current`` at execution time, nothing is appended (``skipped_current``). This
  closes the plan→append→re-execute race conservatively: the append itself makes
  the record current, so a repeat execution of the same unchanged plan skips.
* **Non-interfering / private.** It never mutates ``review_status``,
  deterministic evidence/trust, scientific records, or ``submission.status`` /
  approval / rejection fields, and is not wired into uploads or any public read
  (no ``trust.machine_review``, public ``TrustFragment`` untouched).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import MachineReviewCurrencyState
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.query import (
    get_record_machine_review_currency_for_record,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    MachineReviewReReviewPlan,
)


class MachineReviewReReviewExecutionStatus(str, Enum):
    """Execution outcome for a private machine re-review attempt.

    ``appended`` — exactly one row was appended. ``skipped_current`` — nothing
    was appended, either because the plan already said ``skip_current`` or
    because the idempotency guard found the record already current at execution
    time. There is no failure status here: producing the review (and any
    provider failure → ``machine_review_failed``) happens upstream of this slice.
    """

    skipped_current = "skipped_current"
    appended = "appended"


class MachineReviewReReviewExecutionResult(BaseModel):
    """Private result of executing a machine re-review plan.

    Records what happened (``status``) for what the plan asked (``decision``),
    the appended row's id when one was written, the record it concerns, and the
    currency key the execution used (carried from the plan). ``extra="forbid"``
    so it can carry no mutation instruction — it reports an outcome, it does not
    perform one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MachineReviewReReviewExecutionStatus
    decision: MachineReviewReReviewDecision
    appended_review_id: int | None = None
    record_type: str
    record_id: int
    context_hash: str
    context_schema_version: str
    prompt_version: str
    rubric_versions: dict[str, str] = Field(default_factory=dict)


def execute_record_machine_rereview_plan(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    plan: MachineReviewReReviewPlan,
    review: RecordMachineReview,
    source_submission_id: int | None = None,
    source_audit_event_id: int | None = None,
) -> MachineReviewReReviewExecutionResult:
    """Append a record-machine-review row when a private re-review plan requires it.

    Steps:

    1. If ``plan.decision`` is ``skip_current``, append nothing
       (``skipped_current``).
    2. Otherwise (``run_not_reviewed`` / ``run_stale``), re-check currency for
       the exact record against the plan's currency key (the idempotency guard).
       If the record is already ``current``, append nothing (``skipped_current``).
    3. Otherwise append exactly one row via
       :func:`create_record_machine_review_row`, stamping the plan's
       ``current_context_hash`` / ``context_schema_version`` / ``prompt_version``
       / ``rubric_versions`` as the stored currency key, preserving
       ``source_submission_id`` / ``source_audit_event_id`` when supplied.

    The supplied ``review`` is persisted as-is; this function does not produce or
    validate the review content. It flushes (so the new id is available) but does
    not commit, and mutates nothing outside ``record_machine_review``.
    """
    # The plan's currency key, reused both as the idempotency check context and
    # as the stored currency key for the appended row.
    plan_digest = MachineReviewContextDigest(
        context_hash=plan.current_context_hash,
        context_schema_version=plan.context_schema_version,
    )

    def _result(
        status: MachineReviewReReviewExecutionStatus,
        appended_review_id: int | None,
    ) -> MachineReviewReReviewExecutionResult:
        return MachineReviewReReviewExecutionResult(
            status=status,
            decision=plan.decision,
            appended_review_id=appended_review_id,
            record_type=record_type,
            record_id=record_id,
            context_hash=plan.current_context_hash,
            context_schema_version=plan.context_schema_version,
            prompt_version=plan.prompt_version,
            rubric_versions=dict(plan.rubric_versions),
        )

    # 1. The plan itself says skip — refuse to append.
    if plan.decision is MachineReviewReReviewDecision.skip_current:
        return _result(
            MachineReviewReReviewExecutionStatus.skipped_current, None
        )

    # 2. Idempotency guard: re-check at execution time against the plan's recipe.
    classification = get_record_machine_review_currency_for_record(
        session,
        record_type=record_type,
        record_id=record_id,
        current_context=plan_digest,
        active_prompt_version=plan.prompt_version,
        active_rubric_versions=plan.rubric_versions,
    )
    if classification.state is MachineReviewCurrencyState.current:
        return _result(
            MachineReviewReReviewExecutionStatus.skipped_current, None
        )

    # 3. Append exactly one row through the sole write path.
    row = create_record_machine_review_row(
        session,
        record_type=record_type,
        record_id=record_id,
        review=review,
        context_digest=plan_digest,
        prompt_version=plan.prompt_version,
        rubric_versions=plan.rubric_versions,
        source_submission_id=source_submission_id,
        source_audit_event_id=source_audit_event_id,
    )
    return _result(MachineReviewReReviewExecutionStatus.appended, row.id)
