"""Hosted contribution-bundle endpoints.

Exposes the v0 dry-run preview and the v0 submit/import endpoints.
Submit/import imports a bundle through existing thermo/kinetics upload
workflows and creates a ``submission`` row marked unreviewed/pending
review — see ``docs/contribution-bundles/hosted-submit-v0.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_write_db
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.schemas.contribution_bundle_dry_run import ContributionBundleDryRunResult
from app.schemas.contribution_bundle_submit import ContributionBundleSubmitResult
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_dry_run import dry_run_contribution_bundle
from app.workflows.contribution_bundle_submit import submit_contribution_bundle

router = APIRouter()


@router.post(
    "/dry-run",
    response_model=ContributionBundleDryRunResult,
    status_code=200,
)
def dry_run_bundle(
    bundle: ContributionBundleV0,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> ContributionBundleDryRunResult:
    """Preview what a real import would do for a contribution bundle.

    Requires authentication (session cookie or API key). Returns a
    structured preview describing which identities would be reused,
    which would be created, and that thermo/kinetics result rows would
    be appended. Performs only read-only queries — never mutates the
    database, even if a request fails partway through.
    """
    return dry_run_contribution_bundle(session, bundle)


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
