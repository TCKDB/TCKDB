"""Hosted contribution-bundle endpoints.

Exposes the v0 dry-run preview and the v0 submit/import endpoints. The
dry run rehearses submit itself (#577), so the two refuse identically.
Submit/import imports a bundle through existing thermo/kinetics upload
workflows and creates a ``submission`` row marked unreviewed/pending
review — see ``docs/contribution-bundles/hosted-submit-v0.md``.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from tckdb_schemas.coded_error import CodedValidationError

from app.api.bundle_limits import bundle_record_count, enforce_bundle_record_cap
from app.api.deps import get_current_user, get_db, get_write_db
from app.api.errors import render_handled_exception
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.schemas.contribution_bundle_dry_run import ContributionBundleDryRunResult
from app.schemas.contribution_bundle_submit import (
    ContributionBundleSubmitMessage,
    ContributionBundleSubmitResult,
)
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_dry_run import (
    dry_run_contribution_bundle,
    with_import_warnings,
    with_submit_refusal,
)
from app.workflows.contribution_bundle_submit import (
    rehearse_contribution_bundle_submit,
    submit_contribution_bundle,
)
from app.workflows.rehearsal import RehearsalContended, discard_unflushed_writes

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/dry-run",
    response_model=ContributionBundleDryRunResult,
    status_code=200,
)
def dry_run_bundle(
    bundle: ContributionBundleV0,
    request: Request,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> ContributionBundleDryRunResult:
    """Preview what a real import would do, and whether submit would accept it.

    Requires authentication (session cookie or API key). Returns a
    structured preview describing which identities would be reused,
    which would be created, and that thermo/kinetics result rows would
    be appended.

    Then rehearses ``/bundles/submit`` for the same bundle — the same
    function, inside a savepoint that is always rolled back — so every
    check submit applies is applied here too. If submit would refuse the
    bundle, the result carries that refusal as an ``error`` message with
    submit's own ``code`` and message, and ``bundle_valid`` is false.
    Nothing is kept: the rehearsal's writes are rolled back and this
    session never commits. When submit would accept the bundle, the upload
    warnings it would return -- the same ones, in the same order, that the
    direct ``/uploads/<kind>`` routes give -- are appended to ``messages`` as
    ``warning`` entries carrying the upload's ``local_ref``.

    If another deposit holds a lock the rehearsal needs, the rehearsal gives
    way rather than delay or deadlock that deposit, and this route answers
    ``503 dry_run_contended`` with a ``Retry-After`` header: nothing was
    decided about the bundle, and retrying is the right response.

    A bundle over the size caps -- ``413 bundle_too_large`` for the request
    body, ``422 bundle_too_many_records`` for the record count -- is refused
    before any rehearsal, exactly as ``/bundles/submit`` refuses it. This
    route has its own, tighter rate bucket.
    """
    started = time.monotonic()
    records = bundle_record_count(bundle)
    outcome = "error"  # replaced below unless an exception is what leaves
    try:
        try:
            enforce_bundle_record_cap(bundle)
        except CodedValidationError:
            outcome = "over_cap"
            raise
        # A leftover safeguard: ``authenticate_api_key`` no longer leaves
        # ``api_key.last_used_at`` unflushed in this session (the stamp is
        # written on its own connection), but a dry run never commits, so
        # anything pending would only be held locks for the whole rehearsal.
        # See ``discard_unflushed_writes``.
        discard_unflushed_writes(session)
        result = dry_run_contribution_bundle(session, bundle)
        import_warnings: list[ContributionBundleSubmitMessage] = []
        refusal = rehearse_contribution_bundle_submit(
            session,
            bundle,
            actor=current_user,
            import_warnings_out=import_warnings,
        )
        if refusal is None:
            outcome = "accepted"
            # The upload warnings submit would return for these records: only
            # the rehearsal can know them, so they are added to the preview.
            return with_import_warnings(result, import_warnings)
        rendered = render_handled_exception(request, refusal)
        if rendered is None or rendered[0] >= 500:
            # Not a refusal of the bundle but a failure of the server: submit
            # would answer with this status, so the dry run does too.
            if isinstance(refusal, RehearsalContended):
                outcome = "contended"
            raise refusal
        outcome = "refused"
        _status, body = rendered
        detail = body.get("detail")
        context = body.get("context")
        field = context.get("field") if isinstance(context, dict) else None
        return with_submit_refusal(
            result,
            code=body["code"],
            message=detail if isinstance(detail, str) else json.dumps(detail),
            field=field if isinstance(field, str) else None,
        )
    finally:
        # One line per dry run; ids and counts only, never the payload.
        logger.info(
            "bundle dry run: user=%s records=%d duration_ms=%d outcome=%s",
            current_user.id,
            records,
            round((time.monotonic() - started) * 1000),
            outcome,
        )


@router.post(
    "/submit",
    response_model=ContributionBundleSubmitResult,
    status_code=201,
)
def submit_bundle(
    bundle: ContributionBundleV0,
    session: Session = Depends(get_write_db, scope="function"),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    """Import a contribution bundle into the hosted database.

    Requires authentication (session cookie or API key). Runs the dry-run
    preview as a strict gate (any error or unsupported action blocks the
    import), then imports thermo or kinetics records through the existing
    upload workflows, creates a ``submission`` plus matching audit/link
    rows, and returns a structured result.

    Imported rows are publicly visible by default but explicitly
    ``unreviewed``: validation means importable, not curated/approved.
    Transaction management lives in ``get_write_db`` — any failure rolls
    back the whole bundle (no partial imports). Sending an
    ``Idempotency-Key`` header makes the submit retry-safe; an exact retry
    replays the stored response without re-importing the bundle.

    A bundle over the size caps is refused as ``/bundles/dry-run`` refuses
    it: ``413 bundle_too_large`` (request body) or
    ``422 bundle_too_many_records``.
    """
    if (replay := idem.maybe_replay()) is not None:
        # Before the record cap: a replay does no work, and a cap lowered
        # since the original request must not turn a legitimate replay of an
        # accepted deposit into a 422. (The body cap still applies to a replay:
        # it is enforced before any of this runs.)
        return replay
    enforce_bundle_record_cap(bundle)
    result = submit_contribution_bundle(session, bundle, actor=current_user)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result
