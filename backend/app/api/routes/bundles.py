"""Hosted contribution-bundle endpoints.

Exposes the v0 dry-run preview and the v0 submit/import endpoints. The
dry run rehearses submit itself (#577), so the two refuse identically.
Submit/import imports a bundle through existing thermo/kinetics upload
workflows and creates a ``submission`` row marked unreviewed/pending
review — see ``docs/contribution-bundles/hosted-submit-v0.md``.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_write_db
from app.api.errors import render_handled_exception
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.schemas.contribution_bundle_dry_run import ContributionBundleDryRunResult
from app.schemas.contribution_bundle_submit import ContributionBundleSubmitResult
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_dry_run import (
    dry_run_contribution_bundle,
    with_submit_refusal,
)
from app.workflows.contribution_bundle_submit import (
    rehearse_contribution_bundle_submit,
    submit_contribution_bundle,
)

router = APIRouter()


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
    session never commits.
    """
    result = dry_run_contribution_bundle(session, bundle)
    refusal = rehearse_contribution_bundle_submit(session, bundle, actor=current_user)
    if refusal is None:
        return result
    rendered = render_handled_exception(request, refusal)
    if rendered is None or rendered[0] >= 500:
        # Not a refusal of the bundle but a failure of the server: submit
        # would answer with this status, so the dry run does too.
        raise refusal
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


@router.post(
    "/submit",
    response_model=ContributionBundleSubmitResult,
    status_code=201,
)
def submit_bundle(
    bundle: ContributionBundleV0,
    session: Session = Depends(get_write_db),
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
    """
    if (replay := idem.maybe_replay()) is not None:
        return replay
    result = submit_contribution_bundle(session, bundle, actor=current_user)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result
