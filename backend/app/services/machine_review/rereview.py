"""Private re-review decision/planning service for record-level machine review.

Given a record's identity, its current deterministic-evidence context, and the
active reviewer recipe, this service decides **whether a fresh machine review is
needed** — without calling a provider, running a background job, or appending a
row. It turns the currency model (policy
``backend/docs/specs/record_machine_review_policy.md`` §2/§5) into a testable,
read-only **plan**, so the trigger policy is provable before any execution slice
wires it to the fake provider / admin trigger.

It is built entirely on the existing private stack:

    query service  -> get_record_machine_review_currency_for_record (loads rows)
    currency model -> classify (current / stale / not_run; stale_reasons)
    this service   -> map the currency state to a re-review decision (a plan)

Strictly read-only and non-interfering: it never appends/updates/deletes a
``record_machine_review`` row, and never touches ``submission.status``,
scientific records, deterministic evidence/trust, or the public
``TrustFragment``. No public exposure: nothing here is wired into a scientific
read and no ``trust.machine_review`` is emitted.

Trigger policy (this slice detects only; it does not execute). A re-review is
required exactly when the currency classifier reports the latest review is not
``current`` — i.e. no previous review, or any of the four currency dimensions
mismatched (``context_schema_version`` / ``context_hash`` / ``prompt_version`` /
``rubric_versions``). ``provider`` / ``model`` are deliberately **not** trigger
dimensions (policy §3.5), and no ``reviewed_at`` age/TTL trigger is introduced
(not specified in the policy).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import (
    MachineReviewCurrencyState,
    MachineReviewStaleReason,
)
from app.services.machine_review.query import (
    get_record_machine_review_currency_for_record,
)


class MachineReviewReReviewDecision(str, Enum):
    """Decision for whether a record needs machine re-review.

    A direct, total mapping of the currency state the classifier can return for
    the latest review (``current`` / ``not_run`` / ``stale``); the latest review
    is never ``historical``, so there is no decision for it.
    """

    skip_current = "skip_current"
    run_not_reviewed = "run_not_reviewed"
    run_stale = "run_stale"


# Total mapping from the latest-derived currency state to a re-review decision.
_STATE_TO_DECISION: dict[MachineReviewCurrencyState, MachineReviewReReviewDecision] = {
    MachineReviewCurrencyState.current: MachineReviewReReviewDecision.skip_current,
    MachineReviewCurrencyState.not_run: MachineReviewReReviewDecision.run_not_reviewed,
    MachineReviewCurrencyState.stale: MachineReviewReReviewDecision.run_stale,
}

# The decisions that call for a fresh review (everything except skip_current).
_RUN_DECISIONS: frozenset[MachineReviewReReviewDecision] = frozenset(
    {
        MachineReviewReReviewDecision.run_not_reviewed,
        MachineReviewReReviewDecision.run_stale,
    }
)


class MachineReviewReReviewPlan(BaseModel):
    """Private plan describing whether a machine re-review should run.

    A read-only, frozen snapshot: the ``decision`` plus the currency evidence it
    was derived from (``currency_state`` / ``stale_reasons``), the active review
    it was compared against (``active_review_id`` /
    ``active_source_audit_event_id``, ``None`` when no review exists), and the
    active recipe the plan is for (the context hash + schema/prompt/rubric
    versions). ``extra="forbid"`` so it can carry no mutation instruction; it
    describes *what to do*, it does not do it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: MachineReviewReReviewDecision
    currency_state: MachineReviewCurrencyState
    stale_reasons: tuple[MachineReviewStaleReason, ...] = ()
    active_review_id: int | None = None
    active_source_audit_event_id: int | None = None
    current_context_hash: str
    context_schema_version: str
    prompt_version: str
    rubric_versions: dict[str, str] = Field(default_factory=dict)


def plan_record_machine_rereview(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    current_context: MachineReviewContextDigest,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
) -> MachineReviewReReviewPlan:
    """Plan whether a record needs a fresh machine review.

    Loads the record's persisted rows and classifies their currency against the
    active recipe (both via the existing query service / classifier), then maps
    the currency state to a decision:

    * ``current``  -> ``skip_current``     (a live review already exists)
    * ``not_run``  -> ``run_not_reviewed`` (no review exists yet)
    * ``stale``    -> ``run_stale``        (latest review's recipe no longer matches)

    ``stale_reasons`` (the mismatched currency dimensions) and the active
    review's id / source-audit-event id are carried through when available. The
    plan is for the supplied active recipe, recorded on it for the execution
    slice. Read-only: no row is appended, updated, or deleted, and nothing else
    is mutated.
    """
    classification = get_record_machine_review_currency_for_record(
        session,
        record_type=record_type,
        record_id=record_id,
        current_context=current_context,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
    )

    active = classification.active_review
    return MachineReviewReReviewPlan(
        decision=_STATE_TO_DECISION[classification.state],
        currency_state=classification.state,
        stale_reasons=classification.stale_reasons,
        active_review_id=active.id if active is not None else None,
        active_source_audit_event_id=(
            active.source_audit_event_id if active is not None else None
        ),
        current_context_hash=current_context.context_hash,
        context_schema_version=current_context.context_schema_version,
        prompt_version=active_prompt_version,
        rubric_versions=dict(active_rubric_versions),
    )


def should_run_machine_rereview(plan: MachineReviewReReviewPlan) -> bool:
    """Return ``True`` when the plan requires a fresh machine review.

    ``True`` for ``run_not_reviewed`` and ``run_stale``; ``False`` for
    ``skip_current``. A thin predicate over :attr:`MachineReviewReReviewPlan.decision`
    for callers that only need the boolean.
    """
    return plan.decision in _RUN_DECISIONS
