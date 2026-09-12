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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_write_db, require_admin
from app.api.errors import not_found
from app.api.routes._pagination import PaginatedResponse
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    ArtifactStorageCapacityObservation,
    MachineReviewCuratorTaskState,
    MachineReviewSeverity,
    MachineReviewStatus,
    SubmissionRecordType,
)
from app.db.models.energy_correction import EnergyCorrectionScheme
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import Submission
from app.schemas.fragments.refs import SoftwareRef, WorkflowToolReleaseRef
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.artifact_storage_capacity import (
    append_observation,
    current_full_state,
)
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.machine_review import (
    MachineReviewOrchestrationStatus,
    MachineReviewRecordSummary,
    MachineReviewReReviewDecision,
    MachineReviewReReviewExecutionStatus,
    SubmissionMachineReviewInspection,
    assign_curator_task,
    build_curator_tasks_for_submission,
    build_submission_machine_review_inspection,
    get_curator_task_or_404,
    reopen_curator_task,
    resolve_curator_task,
    run_admin_fake_machine_review,
    start_curator_task_review,
)
from app.services.scientific_read.handles import (
    resolve_energy_correction_scheme_handle,
)
from app.services.software_resolution import resolve_software_release

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
    """Return one curator task by id (admin only); 404 ``curator_task_not_found``.

    The 404 comes from the same service helper the four write routes use.
    It used to come from a private copy in this module that raised a bare
    ``HTTPException``, so reading a missing task said ``http_404`` while
    assigning the same missing task said ``curator_task_not_found`` — one
    condition with two contracts, and a client could branch on only one.
    """
    return _to_curator_task_response(get_curator_task_or_404(session, task_id))


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


# ---------------------------------------------------------------------------
# Artifact storage capacity — the operator's way to clear a stale "full"
# ---------------------------------------------------------------------------


class StorageCapacityClearRequest(BaseModel):
    """An operator's assertion that the store has room again."""

    #: Required, and required for the same reason a curated selection's
    #: rationale is: this is the one clearing path that rests on assertion
    #: rather than on measurement, so the log has to say who said what.
    reason: str = Field(min_length=1, max_length=1000)


class StorageCapacityStateResponse(BaseModel):
    """Whether a refusal is outstanding, after whatever was just appended.

    Carries no row ids, no digests and no bucket names — this is an
    operational report, and DR-0028 Req. 2 applies to it as much as to an
    error body.
    """

    storage_full: bool
    storage_full_observed_at: datetime | None = None
    s3_code: str | None = None
    refused_bytes: int | None = None


def _capacity_state_response(state) -> StorageCapacityStateResponse:
    if state is None:
        return StorageCapacityStateResponse(storage_full=False)
    return StorageCapacityStateResponse(
        storage_full=True,
        storage_full_observed_at=state.observed_at,
        s3_code=state.s3_code,
        refused_bytes=state.attempted_bytes,
    )


@router.get(
    "/artifact-storage/capacity",
    response_model=StorageCapacityStateResponse,
)
def get_artifact_storage_capacity(
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_db),
) -> StorageCapacityStateResponse:
    """Whether the object store is currently refusing writes for want of room.

    The same head-of-log computation ``/status`` reports, without the
    free-space probe — this is the operator's view of the record itself.
    """
    return _capacity_state_response(current_full_state(session))


@router.post(
    "/artifact-storage/capacity/clear",
    response_model=StorageCapacityStateResponse,
)
def clear_artifact_storage_capacity(
    request: StorageCapacityClearRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> StorageCapacityStateResponse:
    """Declare a storage-full condition resolved (admin only).

    An operator is who resolves a full disk, so an operator has to be able
    to say so. This is the clearing path of last resort and the only one
    that can answer a refusal no measurement will: one whose size was
    never known, or a bucket-quota refusal that a free-space report cannot
    see.

    It **appends**; it never edits or deletes. The refusal stays in the
    log as the account of what happened, and this observation supersedes
    it — the same shape as clearing an artifact integrity break, and for
    the same reason. If the store is in fact still full, the next refused
    upload appends a new refusal and ``/status`` degrades again, which is
    the system working rather than a clear that was lost.
    """
    append_observation(
        session,
        observation=ArtifactStorageCapacityObservation.operator_clear,
        detail=request.reason,
        created_by=_admin.id,
    )
    return _capacity_state_response(current_full_state(session))


# ---------------------------------------------------------------------------
# Energy-correction-scheme provenance attach (admin-only, append-only)
# ---------------------------------------------------------------------------
#
# correction-scheme-provenance plan §4.3: the only path that can add a
# citation or software identity to a scheme deposited before it had one.
# ``resolve_or_create_scheme`` (the upload path) never mutates an
# existing row's identity fields -- a differing citation/software makes
# a *new* row under the widened unique index rather than editing the old
# one. This route is the deliberate exception: narrow, admin-gated, and
# append-only per field. It fills a null; it never overwrites a value
# someone already recorded, so it cannot be used to silently rewrite a
# scheme's provenance out from under every ``applied_energy_correction``
# that cites it. ``kind``/``name``/``level_of_theory_id``/``version``/
# ``units`` are not accepted here on purpose -- rewriting those is a far
# bigger surface than "attach missing provenance" (``EnergyCorrectionSchemeUpdate``
# already exists and is deliberately left unrouted for that reason).


class AdminEnergyCorrectionSchemeProvenanceRequest(BaseModel):
    """Provenance to attach to an existing, already-deposited scheme.

    Every field is optional and independent: an admin may fill only the
    citation, only the software, only the workflow-tool release, or any
    combination -- whichever the row is missing. Supplying a field whose
    slot on the row is already non-null is refused (409), never
    silently ignored or overwritten.

    ``software`` stays ``SoftwareRef`` (name only) here -- the underlying
    column is ``software_release_id`` as of the correction-scheme-
    provenance plan v2 (PR 1), so this now fills the version-less release
    row for the named program ("program known, build not stated" is a
    complete, honest value, see the plan's §3.2). Widening this field to
    accept a full release (version/revision/build), the way
    ``EnergyCorrectionSchemeRef.software`` already does on the upload
    path, is PR 3's job, not this one's.
    """

    model_config = ConfigDict(extra="forbid")

    source_literature: LiteratureUploadRequest | None = None
    software: SoftwareRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None


class AdminEnergyCorrectionSchemeProvenanceResponse(BaseModel):
    """Auditable from the response alone: the scheme's own ref plus every
    resolved provenance ref it now carries (not just the ones this call
    just set)."""

    model_config = ConfigDict(extra="forbid")

    energy_correction_scheme_ref: str
    source_literature_ref: str | None = None
    #: Renamed from ``software_ref`` when ``energy_correction_scheme``
    #: moved from ``software_id`` to ``software_release_id``
    #: (c24ce2d9c198). The value this field carries changed referent at
    #: the same moment -- it is now a ``software_release`` public ref
    #: (``srel_...``), not a ``software`` one (``soft_...``). Keeping the
    #: old name would have left an admin client silently resolving the
    #: ref against the wrong table; renaming makes the break visible.
    software_release_ref: str | None = None
    workflow_tool_release_ref: str | None = None


_ALREADY_SET_CODES: dict[str, str] = {
    "literature": "energy_correction_scheme_literature_already_set",
    "software": "energy_correction_scheme_software_already_set",
    "workflow_tool_release": (
        "energy_correction_scheme_workflow_tool_release_already_set"
    ),
}


def _already_set_conflict(field: str) -> HTTPException:
    code = _ALREADY_SET_CODES[field]
    return HTTPException(
        status_code=409,
        detail=(
            f"{code}: this scheme already carries a recorded "
            f"{field.replace('_', ' ')}. This route only fills a missing "
            "field -- it never overwrites a value someone already "
            "recorded."
        ),
    )


@router.patch(
    "/energy-correction-schemes/{ref}/provenance",
    response_model=AdminEnergyCorrectionSchemeProvenanceResponse,
)
def attach_energy_correction_scheme_provenance(
    ref: str,
    request: AdminEnergyCorrectionSchemeProvenanceRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> AdminEnergyCorrectionSchemeProvenanceResponse:
    """Fill missing citation/software provenance on a scheme (admin only).

    Path handle accepts an integer ``energy_correction_scheme.id`` or a
    public ref of the form ``ecs_...``; unknown handles 404. Each of
    ``source_literature``/``software``/``workflow_tool_release`` is
    refused with 409 if the corresponding column is already non-null on
    the row (per-field, not all-or-nothing -- one call can fill the
    citation on a scheme that already has software recorded, or vice
    versa). Resolution reuses the exact same services the upload path
    uses (``resolve_or_create_literature``, ``resolve_software_release``,
    ``resolve_workflow_tool_release_ref``), so a citation/software
    release that already exists elsewhere in the archive is reused, not
    duplicated.

    Mutating these fields on an already-inserted row does not regenerate
    its public ref -- refs are content-derived only at INSERT time
    (``PublicRefMixin``), and keeping the ref stable across a
    provenance-fill matters more than the ref perfectly reflecting the
    row's current content. If the resulting (kind, name, lot, version,
    units, literature, software_release, workflow_tool_release) tuple
    collides with another existing scheme row, the write is refused with
    409 rather than silently merging two rows' identities.
    """
    scheme_id = resolve_energy_correction_scheme_handle(session, ref)
    scheme = session.get(EnergyCorrectionScheme, scheme_id)
    if scheme is None:  # pragma: no cover — defended by resolver 404
        raise not_found(
            "energy_correction_scheme", row_id=scheme_id, code="handle_not_found"
        )

    if request.source_literature is not None:
        if scheme.source_literature_id is not None:
            raise _already_set_conflict("literature")
        literature = resolve_or_create_literature(session, request.source_literature)
        scheme.source_literature_id = literature.id

    if request.software is not None:
        if scheme.software_release_id is not None:
            raise _already_set_conflict("software")
        # SoftwareRef carries a bare name; resolve it to the version-less
        # release row for that program (see the request model's docstring).
        release = resolve_software_release(session, name=request.software.name)
        scheme.software_release_id = release.id

    if request.workflow_tool_release is not None:
        if scheme.workflow_tool_release_id is not None:
            raise _already_set_conflict("workflow_tool_release")
        wtr = resolve_workflow_tool_release_ref(session, request.workflow_tool_release)
        scheme.workflow_tool_release_id = wtr.id if wtr is not None else None

    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "energy_correction_scheme_identity_conflict: attaching this "
                "provenance would make this scheme identical to another "
                "existing scheme row."
            ),
        ) from exc

    return AdminEnergyCorrectionSchemeProvenanceResponse(
        energy_correction_scheme_ref=scheme.public_ref,
        source_literature_ref=(
            scheme.source_literature.public_ref
            if scheme.source_literature_id is not None
            else None
        ),
        software_release_ref=(
            scheme.software_release.public_ref
            if scheme.software_release_id is not None
            else None
        ),
        workflow_tool_release_ref=(
            scheme.workflow_tool_release.public_ref
            if scheme.workflow_tool_release_id is not None
            else None
        ),
    )
