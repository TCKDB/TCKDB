"""Admin-only management endpoints.

Scope in v1 is intentionally tiny: role changes plus a private machine-review
inspection endpoint.  Everything here is gated behind the ``admin`` role —
curators cannot promote each other, and the machine-review inspection endpoint
is deliberately admin-only (the stricter of the two existing gates) because it
is a debugging surface, not public scientific trust.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_write_db, require_admin
from app.api.routes._pagination import PaginatedResponse
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MachineReviewCuratorTaskState,
    MachineReviewSeverity,
    MachineReviewStatus,
    SubmissionRecordType,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import Submission
from app.services.machine_review import (
    MachineReviewOrchestrationStatus,
    MachineReviewRecordSummary,
    MachineReviewReReviewDecision,
    MachineReviewReReviewExecutionStatus,
    SubmissionMachineReviewInspection,
    assign_curator_task,
    build_curator_tasks_for_submission,
    build_submission_machine_review_inspection,
    reopen_curator_task,
    resolve_curator_task,
    run_admin_fake_machine_review,
    start_curator_task_review,
)

router = APIRouter()


class RoleChangeRequest(BaseModel):
    role: AppUserRole


class UserRoleResponse(BaseModel):
    id: int
    username: str
    role: AppUserRole


@router.patch("/users/{user_id}/role", response_model=UserRoleResponse)
def change_user_role(
    user_id: int,
    request: RoleChangeRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> UserRoleResponse:
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.role = request.role
    session.flush()
    return UserRoleResponse(id=user.id, username=user.username, role=user.role)


# ---------------------------------------------------------------------------
# Private machine-review inspection (admin debugging only)
# ---------------------------------------------------------------------------
#
# This endpoint surfaces how a submission's existing ``llm_precheck_recorded``
# audit events project onto the records linked to that submission, reusing the
# private machine-review stack (audit adapter -> safe mapping -> read model). It
# is a debugging aid for maintainers deciding whether to expose
# ``trust.machine_review`` publicly later. It is **not** public scientific
# trust: nothing here touches the public ``TrustFragment`` or the scientific
# read routes, and the response is its own admin-only schema. The handler reads
# through ``get_db`` (no write session) and mutates nothing.


class AdminMachineReviewRecordInspection(BaseModel):
    """Admin-only machine-review projection for one linked record."""

    model_config = ConfigDict(extra="forbid")

    record_type: str
    record_ref: str | None = None
    record_id: int | None = None
    latest_summary: MachineReviewRecordSummary
    all_record_reviews_count: int = 0


class AdminSubmissionMachineReviewInspectionResponse(BaseModel):
    """Admin-only machine-review inspection response for one submission.

    Private/debugging shape — intentionally distinct from the public
    scientific ``TrustFragment``. ``record_summaries`` carries one entry per
    linked record that received at least one mapped machine-review finding;
    submission-scoped, unlinked, and sibling findings appear only in the
    diagnostics counters/warnings, never as a record summary.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: int
    record_summaries: tuple[AdminMachineReviewRecordInspection, ...] = ()
    unmapped_findings_count: int = 0
    mapping_warnings: tuple[str, ...] = ()
    parse_warnings: tuple[str, ...] = ()
    source_audit_event_ids: tuple[int, ...] = ()


def _to_admin_inspection_response(
    inspection: SubmissionMachineReviewInspection,
) -> AdminSubmissionMachineReviewInspectionResponse:
    """Map the private inspection result onto the admin response schema."""
    return AdminSubmissionMachineReviewInspectionResponse(
        submission_id=inspection.submission_id,
        record_summaries=tuple(
            AdminMachineReviewRecordInspection(
                record_type=record.record_type,
                record_ref=record.record_ref,
                record_id=record.record_id,
                latest_summary=record.latest_summary,
                all_record_reviews_count=len(record.all_record_reviews),
            )
            for record in inspection.record_inspections
        ),
        unmapped_findings_count=len(inspection.unmapped_findings),
        mapping_warnings=inspection.mapping_warnings,
        parse_warnings=inspection.parse_warnings,
        source_audit_event_ids=inspection.source_audit_event_ids,
    )


@router.get(
    "/submissions/{submission_id}/machine-review-inspection",
    response_model=AdminSubmissionMachineReviewInspectionResponse,
)
def inspect_submission_machine_review(
    submission_id: int,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminSubmissionMachineReviewInspectionResponse:
    """Inspect machine-review projections for one submission (admin only).

    Loads the submission (404 if missing), then projects its machine-review
    audit events onto its linked records via the private inspection service.
    Non-machine-review events are ignored. This is read-only: it never mutates
    submission status/lifecycle fields, review state, deterministic evidence,
    or scientific records.
    """
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    inspection = build_submission_machine_review_inspection(
        submission_id=submission.id,
        submission_record_links=submission.record_links,
        submission_audit_events=submission.audit_events,
    )
    return _to_admin_inspection_response(inspection)


# ---------------------------------------------------------------------------
# Explicit fake machine-review trigger (admin debugging only)
# ---------------------------------------------------------------------------
#
# Admin-only, explicitly invoked: run **fake** machine review for one record and
# append a ``record_machine_review`` row only when the active recipe says one is
# needed (``run_not_reviewed`` / ``run_stale``); an already-current record is
# skipped. This is the maintainer/debug seam for the private re-review loop
# (policy ``record_machine_review_policy.md`` §5.3) — it is **not** public
# scientific trust exposure. It uses only the :class:`FakeMachineReviewProducer`
# (no real provider, no RAG, no background worker), is not wired into uploads or
# any public read, and emits no ``trust.machine_review``.
#
# The handler writes (at most) one row, and only through the orchestration /
# executor path. It never mutates ``submission.status``, ``RecordReviewStatus``,
# scientific records, deterministic evidence/trust, certification/benchmark
# fields, or the public ``TrustFragment``. ``record_id`` is an internal id —
# acceptable here because the durable table is internal-id based and the surface
# is admin-only. Unsupported ``record_type`` -> 400 (``DomainError``); a missing
# record -> 404 (``NotFoundError``), both via the global handlers.


class AdminRunFakeMachineReviewResponse(BaseModel):
    """Admin-only response for an explicitly invoked fake machine-review run.

    Mirrors :class:`~app.services.machine_review.MachineReviewOrchestrationResult`
    one-for-one. ``extra="forbid"`` so it can carry no mutation instruction; it
    reports an outcome, it does not perform one. No public ``trust.machine_review``.
    """

    model_config = ConfigDict(extra="forbid")

    status: MachineReviewOrchestrationStatus
    decision: MachineReviewReReviewDecision
    execution_status: MachineReviewReReviewExecutionStatus | None = None
    appended_review_id: int | None = None
    record_type: str
    record_id: int
    context_hash: str
    context_schema_version: str
    prompt_version: str
    rubric_versions: dict[str, str]
    summary: str | None = None


@router.post(
    "/machine-review/records/{record_type}/{record_id}/run-fake",
    response_model=AdminRunFakeMachineReviewResponse,
)
def run_fake_machine_review_for_record(
    record_type: str,
    record_id: int,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminRunFakeMachineReviewResponse:
    """Explicitly run fake machine review for one record (admin only).

    Validates ``record_type`` (400 if unsupported), loads the record (404 if
    missing), builds its live deterministic trust/evidence fragment, looks up
    the active prompt/rubric recipe, and runs the private fake machine-review
    loop. A ``record_machine_review`` row is appended only for
    ``run_not_reviewed`` / ``run_stale``; an already-current record is skipped,
    so re-running an unchanged recipe is idempotent. Uses the fake producer
    only; mutates nothing outside ``record_machine_review``.
    """
    reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    result = run_admin_fake_machine_review(
        session,
        record_type=record_type,
        record_id=record_id,
        reviewed_at=reviewed_at,
    )
    return AdminRunFakeMachineReviewResponse(
        status=result.status,
        decision=result.decision,
        execution_status=result.execution_status,
        appended_review_id=result.appended_review_id,
        record_type=result.record_type,
        record_id=result.record_id,
        context_hash=result.context_hash,
        context_schema_version=result.context_schema_version,
        prompt_version=result.prompt_version,
        rubric_versions=result.rubric_versions,
        summary=result.summary,
    )


# ---------------------------------------------------------------------------
# Machine-review curator task queue (admin workflow API)
# ---------------------------------------------------------------------------
#
# Admin-only CRUD-ish workflow over ``machine_review_curator_task``: list,
# inspect, explicitly build from the inspection projection, assign, start
# review, resolve, and reopen. This is the *human workflow* axis (spec
# ``machine_review_curator_task_queue.md`` §2/§5) — it never approves, rejects,
# certifies, or mutates a scientific record, ``submission.status``,
# ``RecordReviewStatus``, deterministic evidence, or any public ``trust.*``
# fragment. Responses are admin-only schemas; the public scientific
# ``TrustFragment`` is untouched and ``trust.machine_review`` is not exposed.
#
# All routes are gated behind ``require_admin`` (curators get 403 in this
# slice). Service-layer ``DomainError`` / ``NotFoundError`` map to 400 / 404 via
# the global handlers registered in ``app.api.errors``.

_CURATOR_TASK_BASE = "/machine-review/curator-tasks"

# Open workflow states, ordered to land first in the queue (spec §10).
_OPEN_STATES: tuple[MachineReviewCuratorTaskState, ...] = (
    MachineReviewCuratorTaskState.untriaged,
    MachineReviewCuratorTaskState.needs_curator_review,
    MachineReviewCuratorTaskState.in_curator_review,
)


class AdminCuratorTaskResponse(BaseModel):
    """Admin-only view of one curator task.

    Distinct from any public scientific schema; carries no public
    ``trust.machine_review``. ``record_id`` is an internal id, acceptable here
    because the surface is admin-only (spec §3).
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    submission_id: int
    record_type: SubmissionRecordType
    record_id: int
    finding_fingerprint: str
    workflow_state: MachineReviewCuratorTaskState
    machine_review_status: MachineReviewStatus
    highest_severity: MachineReviewSeverity
    findings_count: int
    source_audit_event_id: int | None = None
    assigned_to: int | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    resolved_by: int | None = None
    resolution_note: str | None = None


class AdminCuratorTaskBuildResponse(BaseModel):
    """Result of an explicit build-for-submission run (mirrors
    :class:`~app.services.machine_review.CuratorTaskBuildResult`)."""

    model_config = ConfigDict(extra="forbid")

    created_count: int
    reused_count: int
    refreshed_count: int
    skipped_info_count: int
    skipped_unmapped_count: int
    skipped_terminal_count: int
    task_ids: tuple[int, ...]
    warnings: tuple[str, ...]


class AdminCuratorTaskAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``null`` unassigns the task.
    assignee_id: int | None = None


class AdminCuratorTaskStartReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Optional override for the actor assigned when the task is unassigned;
    #: defaults to the authenticated admin.
    actor_user_id: int | None = None
    assign_actor_if_unassigned: bool = True


class AdminCuratorTaskResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_state: MachineReviewCuratorTaskState
    resolution_note: str


class AdminCuratorTaskReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_state: MachineReviewCuratorTaskState = (
        MachineReviewCuratorTaskState.needs_curator_review
    )
    clear_assignment: bool = False


def _to_curator_task_response(
    task: MachineReviewCuratorTask,
) -> AdminCuratorTaskResponse:
    return AdminCuratorTaskResponse(
        id=task.id,
        submission_id=task.submission_id,
        record_type=task.record_type,
        record_id=task.record_id,
        finding_fingerprint=task.finding_fingerprint,
        workflow_state=task.workflow_state,
        machine_review_status=task.machine_review_status,
        highest_severity=task.highest_severity,
        findings_count=task.findings_count,
        source_audit_event_id=task.source_audit_event_id,
        assigned_to=task.assigned_to,
        created_at=task.created_at,
        updated_at=task.updated_at,
        resolved_at=task.resolved_at,
        resolved_by=task.resolved_by,
        resolution_note=task.resolution_note,
    )


def _get_curator_task_or_404(
    session: Session, task_id: int
) -> MachineReviewCuratorTask:
    task = session.get(MachineReviewCuratorTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Curator task not found.")
    return task


@router.get(
    _CURATOR_TASK_BASE,
    response_model=PaginatedResponse[AdminCuratorTaskResponse],
)
def list_curator_tasks(
    workflow_state: MachineReviewCuratorTaskState | None = None,
    assigned_to: int | None = None,
    record_type: SubmissionRecordType | None = None,
    record_id: int | None = None,
    submission_id: int | None = None,
    highest_severity: MachineReviewSeverity | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> PaginatedResponse[AdminCuratorTaskResponse]:
    """List curator tasks (admin only).

    Deterministic ordering: open states first, then highest severity first,
    then most-recently-updated, with ``id`` as the final tie-break. Filters are
    ANDed. Terminal tasks are included; filter on ``workflow_state`` to narrow.
    """
    filters = []
    if workflow_state is not None:
        filters.append(MachineReviewCuratorTask.workflow_state == workflow_state)
    if assigned_to is not None:
        filters.append(MachineReviewCuratorTask.assigned_to == assigned_to)
    if record_type is not None:
        filters.append(MachineReviewCuratorTask.record_type == record_type)
    if record_id is not None:
        filters.append(MachineReviewCuratorTask.record_id == record_id)
    if submission_id is not None:
        filters.append(MachineReviewCuratorTask.submission_id == submission_id)
    if highest_severity is not None:
        filters.append(MachineReviewCuratorTask.highest_severity == highest_severity)

    total = session.scalar(
        select(func.count())
        .select_from(MachineReviewCuratorTask)
        .where(*filters)
    )

    open_first = case(
        (MachineReviewCuratorTask.workflow_state.in_(_OPEN_STATES), 0),
        else_=1,
    )
    severity_rank = case(
        (MachineReviewCuratorTask.highest_severity == MachineReviewSeverity.critical, 0),
        (MachineReviewCuratorTask.highest_severity == MachineReviewSeverity.warning, 1),
        else_=2,
    )
    stmt = (
        select(MachineReviewCuratorTask)
        .where(*filters)
        .order_by(
            open_first,
            severity_rank,
            MachineReviewCuratorTask.updated_at.desc(),
            MachineReviewCuratorTask.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    tasks = session.scalars(stmt).all()
    return PaginatedResponse(
        items=[_to_curator_task_response(t) for t in tasks],
        total=total or 0,
        skip=offset,
        limit=limit,
    )


@router.get(
    f"{_CURATOR_TASK_BASE}/{{task_id}}",
    response_model=AdminCuratorTaskResponse,
)
def get_curator_task(
    task_id: int,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> AdminCuratorTaskResponse:
    """Return one curator task by id (admin only); 404 if missing."""
    return _to_curator_task_response(_get_curator_task_or_404(session, task_id))


@router.post(
    f"{_CURATOR_TASK_BASE}/build-for-submission/{{submission_id}}",
    response_model=AdminCuratorTaskBuildResponse,
)
def build_curator_tasks_for_submission_endpoint(
    submission_id: int,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminCuratorTaskBuildResponse:
    """Explicitly build/upsert curator tasks for one submission (admin only).

    Loads the submission (404 if missing), projects its machine-review audit
    events onto its linked records via the private inspection service, then
    creates/upserts tasks for the exact mapped warning/critical findings.
    Info, submission-scoped, unmapped, and parse-warning diagnostics never
    become tasks. Explicit/admin-triggered only — never runs on upload. Writes
    only curator-task rows; ``submission.status`` is untouched.
    """
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    inspection = build_submission_machine_review_inspection(
        submission_id=submission.id,
        submission_record_links=submission.record_links,
        submission_audit_events=submission.audit_events,
    )
    result = build_curator_tasks_for_submission(session, inspection=inspection)
    return AdminCuratorTaskBuildResponse(
        created_count=result.created_count,
        reused_count=result.reused_count,
        refreshed_count=result.refreshed_count,
        skipped_info_count=result.skipped_info_count,
        skipped_unmapped_count=result.skipped_unmapped_count,
        skipped_terminal_count=result.skipped_terminal_count,
        task_ids=result.task_ids,
        warnings=result.warnings,
    )


@router.post(
    f"{_CURATOR_TASK_BASE}/{{task_id}}/assign",
    response_model=AdminCuratorTaskResponse,
)
def assign_curator_task_endpoint(
    task_id: int,
    request: AdminCuratorTaskAssignRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminCuratorTaskResponse:
    """Set or clear a task's assignee (admin only). ``assignee_id=null``
    unassigns. Does not change workflow state or any review/submission state."""
    task = assign_curator_task(
        session, task_id=task_id, assignee_id=request.assignee_id
    )
    return _to_curator_task_response(task)


@router.post(
    f"{_CURATOR_TASK_BASE}/{{task_id}}/start-review",
    response_model=AdminCuratorTaskResponse,
)
def start_curator_task_review_endpoint(
    task_id: int,
    request: AdminCuratorTaskStartReviewRequest | None = None,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminCuratorTaskResponse:
    """Move an open task into ``in_curator_review`` (admin only).

    The acting user defaults to the authenticated admin; a body may override
    ``actor_user_id`` or disable the auto-assign side effect.
    """
    body = request or AdminCuratorTaskStartReviewRequest()
    actor_id = body.actor_user_id if body.actor_user_id is not None else _admin.id
    task = start_curator_task_review(
        session,
        task_id=task_id,
        actor_id=actor_id,
        assign_actor_if_unassigned=body.assign_actor_if_unassigned,
    )
    return _to_curator_task_response(task)


@router.post(
    f"{_CURATOR_TASK_BASE}/{{task_id}}/resolve",
    response_model=AdminCuratorTaskResponse,
)
def resolve_curator_task_endpoint(
    task_id: int,
    request: AdminCuratorTaskResolveRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminCuratorTaskResponse:
    """Resolve a task into a terminal state (admin only).

    ``resolution_note`` is required and non-empty; ``resolved_by`` is the
    authenticated admin; ``resolved_at`` is set by the service. A non-terminal
    ``resolution_state`` or a blank note yields 400. ``resolved_human_reviewed``
    does NOT write ``RecordReviewStatus`` — it only records that a human review
    happened elsewhere.
    """
    task = resolve_curator_task(
        session,
        task_id=task_id,
        resolution=request.resolution_state,
        resolved_by=_admin.id,
        resolution_note=request.resolution_note,
    )
    return _to_curator_task_response(task)


@router.post(
    f"{_CURATOR_TASK_BASE}/{{task_id}}/reopen",
    response_model=AdminCuratorTaskResponse,
)
def reopen_curator_task_endpoint(
    task_id: int,
    request: AdminCuratorTaskReopenRequest | None = None,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminCuratorTaskResponse:
    """Reopen a terminal task into an open state (admin only).

    Clears the resolution triple; preserves ``assigned_to`` unless
    ``clear_assignment=true``. Mutates no review or submission state.
    """
    body = request or AdminCuratorTaskReopenRequest()
    task = reopen_curator_task(
        session,
        task_id=task_id,
        target_state=body.target_state,
        clear_assignment=body.clear_assignment,
    )
    return _to_curator_task_response(task)
