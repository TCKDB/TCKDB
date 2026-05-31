"""Lifecycle service for machine-review curator tasks.

The *creation* service (:mod:`app.services.machine_review.curator_tasks`)
builds/upserts tasks from an inspection projection. This module owns the
human-workflow transitions over an existing task: assignment, picking a task
up for review, resolving it, and (explicitly) reopening it.

Authority boundary (spec
``backend/docs/specs/machine_review_curator_task_queue.md`` §8/§9):

* These functions mutate **only** the workflow columns of one
  ``machine_review_curator_task`` row — ``workflow_state``, ``assigned_to``,
  ``resolved_at``, ``resolved_by``, ``resolution_note`` (and ``updated_at``
  via the model's ``onupdate``). They never write ``submission.status``,
  ``record_review`` / ``RecordReviewStatus``, certification, deterministic
  evidence, scientific records, or any public ``trust.*`` fragment.
* In particular, resolving a task as ``resolved_human_reviewed`` records that
  a human review happened *elsewhere*; it does **not** itself write
  ``RecordReviewStatus``. Human review is performed through the authoritative
  review layer and merely reflected here (spec §8).

Transaction & authorization: like the sibling write services
(:mod:`app.services.record_review`), each function flushes but does not
commit — the caller owns the transaction. Role/permission gating (curator vs
admin) is the responsibility of the future admin endpoint, not these
low-level helpers; they take resolver/assignee user *ids* so they compose
cleanly under whatever the endpoint authorizes.

State model (:class:`MachineReviewCuratorTaskState`):

    open:     untriaged, needs_curator_review, in_curator_review
    terminal: resolved_no_action, resolved_human_reviewed,
              dismissed_machine_finding
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import MachineReviewCuratorTaskState
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask

# Open states a reopen is allowed to move a terminal task *back to*. All three
# open states are valid landing spots; the default is needs_curator_review.
_REOPEN_TARGETS: frozenset[MachineReviewCuratorTaskState] = frozenset(
    {
        MachineReviewCuratorTaskState.untriaged,
        MachineReviewCuratorTaskState.needs_curator_review,
        MachineReviewCuratorTaskState.in_curator_review,
    }
)


def _now_naive_utc() -> datetime:
    """Naive-UTC ``datetime`` for the timezone-less timestamp columns.

    Matches the convention in :mod:`app.services.record_review`.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_task_or_404(session: Session, task_id: int) -> MachineReviewCuratorTask:
    task = session.get(MachineReviewCuratorTask, task_id)
    if task is None:
        raise NotFoundError(
            f"Curator task {task_id} not found", code="curator_task_not_found"
        )
    return task


def assign_curator_task(
    session: Session,
    *,
    task_id: int,
    assignee_id: int | None,
    allow_terminal: bool = False,
) -> MachineReviewCuratorTask:
    """Set (or clear) a task's assignee without changing its workflow state.

    ``assignee_id=None`` unassigns. Assigning never resolves the task and never
    touches human-review or submission state. A resolved/terminal task cannot
    be (re)assigned unless ``allow_terminal=True`` — reassigning a closed task
    is almost always a mistake, so it must be explicit.
    """
    task = _get_task_or_404(session, task_id)

    if task.workflow_state.is_terminal and not allow_terminal:
        raise DomainError(
            f"Cannot assign a resolved curator task "
            f"(state={task.workflow_state.value}); pass allow_terminal=True to "
            "assign a terminal task."
        )

    task.assigned_to = assignee_id
    session.flush()
    return task


def start_curator_task_review(
    session: Session,
    *,
    task_id: int,
    actor_id: int | None = None,
    assign_actor_if_unassigned: bool = True,
) -> MachineReviewCuratorTask:
    """Move an open task into ``in_curator_review``.

    Allowed from ``untriaged`` or ``needs_curator_review``; idempotent if the
    task is already ``in_curator_review``; rejected if the task is terminal.
    When ``assign_actor_if_unassigned`` and the task has no assignee, the
    acting user (``actor_id``) is assigned as a side effect. Touches no
    human-review state.
    """
    task = _get_task_or_404(session, task_id)
    state = task.workflow_state

    if state.is_terminal:
        raise DomainError(
            f"Cannot start review on a resolved curator task "
            f"(state={state.value}); reopen it first."
        )

    if state is not MachineReviewCuratorTaskState.in_curator_review:
        # untriaged / needs_curator_review -> in_curator_review
        task.workflow_state = MachineReviewCuratorTaskState.in_curator_review

    if (
        assign_actor_if_unassigned
        and task.assigned_to is None
        and actor_id is not None
    ):
        task.assigned_to = actor_id

    session.flush()
    return task


def resolve_curator_task(
    session: Session,
    *,
    task_id: int,
    resolution: MachineReviewCuratorTaskState,
    resolved_by: int,
    resolution_note: str,
) -> MachineReviewCuratorTask:
    """Resolve a task into one of the terminal states.

    Requires ``resolution`` to be a terminal state, a non-empty
    ``resolution_note``, and a ``resolved_by`` user id; ``resolved_at`` is
    stamped by the service. All resolution fields are set together (the DB
    CHECK constraint enforces this too). ``assigned_to`` is left unchanged.

    Re-resolving an already-terminal task is idempotent **only** when the same
    terminal state is requested (returns the existing row unchanged, preserving
    the original resolver/note/timestamp); a *different* terminal state is
    rejected. ``resolved_human_reviewed`` does not write ``RecordReviewStatus``
    — it records that a human review happened elsewhere (spec §7/§8).
    """
    if resolution not in MachineReviewCuratorTaskState.terminal_states():
        raise DomainError(
            f"resolution must be a terminal state, got {resolution.value!r}."
        )

    note = (resolution_note or "").strip()
    if not note:
        raise DomainError("resolution_note is required and must be non-empty.")

    task = _get_task_or_404(session, task_id)

    if task.workflow_state.is_terminal:
        if task.workflow_state is resolution:
            # Idempotent: same terminal state already recorded. Leave the
            # original resolver/note/timestamp intact.
            return task
        raise DomainError(
            f"Curator task {task_id} is already resolved as "
            f"{task.workflow_state.value!r}; cannot re-resolve as "
            f"{resolution.value!r}. Reopen it first to change the resolution."
        )

    task.workflow_state = resolution
    task.resolved_by = resolved_by
    task.resolved_at = _now_naive_utc()
    task.resolution_note = note
    session.flush()
    return task


def reopen_curator_task(
    session: Session,
    *,
    task_id: int,
    target_state: MachineReviewCuratorTaskState = (
        MachineReviewCuratorTaskState.needs_curator_review
    ),
    clear_assignment: bool = False,
) -> MachineReviewCuratorTask:
    """Reopen a resolved/terminal task into an open state.

    ``target_state`` must be an open state (``untriaged``,
    ``needs_curator_review``, or ``in_curator_review``); it defaults to
    ``needs_curator_review``. The resolution triple (``resolved_at`` / ``resolved_by`` /
    ``resolution_note``) is cleared so the row satisfies the resolution
    consistency CHECK as an open task. ``assigned_to`` is preserved unless
    ``clear_assignment=True``. Mutates no human-review or submission state.
    """
    if target_state not in _REOPEN_TARGETS:
        raise DomainError(
            "reopen target_state must be an open state (untriaged, "
            "needs_curator_review, or in_curator_review), got "
            f"{target_state.value!r}."
        )

    task = _get_task_or_404(session, task_id)

    if not task.workflow_state.is_terminal:
        raise DomainError(
            f"Only a resolved/terminal curator task can be reopened "
            f"(state={task.workflow_state.value})."
        )

    task.workflow_state = target_state
    task.resolved_at = None
    task.resolved_by = None
    task.resolution_note = None
    if clear_assignment:
        task.assigned_to = None

    session.flush()
    return task
