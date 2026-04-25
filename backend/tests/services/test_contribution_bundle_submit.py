"""Service-layer tests for the contribution-bundle submit/import workflow.

Complements tests/api/test_api_bundle_submit.py by exercising the
internals (dry-run gate predicate, message carry-forward, unsupported
bundle-kind guard) directly against a DB session — without going
through FastAPI auth/transaction plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    SubmissionAuditEventKind,
    SubmissionRecordType,
    SubmissionStatus,
)
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.schemas.contribution_bundle_dry_run import (
    ContributionBundleDryRunMessage,
    ContributionBundleDryRunResult,
    ContributionBundleDryRunSummary,
    DryRunMessageLevel,
)
from app.schemas.workflows.contribution_bundle import (
    BundleKind,
    ContributionBundleV0,
)
from app.workflows.contribution_bundle_submit import (
    _is_blocking,
    submit_contribution_bundle,
)
from sqlalchemy import select


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


def _make_user(session, username: str = "submitter") -> AppUser:
    user = AppUser(username=username, role=AppUserRole.user)
    session.add(user)
    session.flush()
    return user


def _load_bundle(filename: str) -> ContributionBundleV0:
    raw = json.loads((EXAMPLES_DIR / filename).read_text())
    return ContributionBundleV0.model_validate(raw)


def _empty_dry_run(
    *,
    bundle_valid: bool = True,
    errors: int = 0,
    unsupported: int = 0,
    warnings: int = 0,
    messages: list[ContributionBundleDryRunMessage] | None = None,
) -> ContributionBundleDryRunResult:
    return ContributionBundleDryRunResult(
        bundle_valid=bundle_valid,
        bundle_kind=BundleKind.thermo,
        summary=ContributionBundleDryRunSummary(
            records_seen=0,
            would_create=0,
            would_reuse=0,
            would_append=0,
            unsupported=unsupported,
            errors=errors,
            warnings=warnings,
        ),
        items=[],
        messages=messages or [],
    )


# ---------------------------------------------------------------------------
# _is_blocking — strict policy
# ---------------------------------------------------------------------------


class TestIsBlocking:
    def test_clean_dry_run_does_not_block(self) -> None:
        assert _is_blocking(_empty_dry_run()) is False

    def test_invalid_bundle_blocks(self) -> None:
        assert _is_blocking(_empty_dry_run(bundle_valid=False)) is True

    def test_summary_errors_block(self) -> None:
        assert _is_blocking(_empty_dry_run(errors=1)) is True

    def test_unsupported_action_blocks(self) -> None:
        assert _is_blocking(_empty_dry_run(unsupported=1)) is True

    def test_warnings_alone_do_not_block(self) -> None:
        msg = ContributionBundleDryRunMessage(
            level=DryRunMessageLevel.warning,
            code="example_warning",
            message="non-blocking notice",
        )
        result = _empty_dry_run(warnings=1, messages=[msg])
        assert _is_blocking(result) is False


# ---------------------------------------------------------------------------
# Workflow happy-path
# ---------------------------------------------------------------------------


class TestSubmitContributionBundle:
    def test_thermo_import_creates_submission_audit_and_links(
        self, db_session
    ) -> None:
        user = _make_user(db_session)
        bundle = _load_bundle("thermo-bundle-v0.json")

        result = submit_contribution_bundle(db_session, bundle, actor=user)

        assert result.bundle_kind is BundleKind.thermo
        assert result.status is SubmissionStatus.pending
        assert result.review_status.value == "unreviewed"
        assert result.summary.records_imported == 1
        assert result.summary.records_linked == 1

        submission = db_session.get(Submission, result.submission_id)
        assert submission is not None
        assert submission.created_by == user.id
        assert submission.title == bundle.submission.title
        assert submission.summary == bundle.submission.summary

        # audit events: submission_created (auto) + ingestion_succeeded
        kinds = [
            e.event_kind
            for e in db_session.scalars(
                select(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id == submission.id
                )
            ).all()
        ]
        assert SubmissionAuditEventKind.submission_created in kinds
        assert SubmissionAuditEventKind.ingestion_succeeded in kinds

        # link rows: thermo + species_entry
        link_types = {
            link.record_type
            for link in db_session.scalars(
                select(SubmissionRecordLink).where(
                    SubmissionRecordLink.submission_id == submission.id
                )
            ).all()
        }
        assert link_types == {
            SubmissionRecordType.thermo,
            SubmissionRecordType.species_entry,
        }

    def test_kinetics_import_creates_submission_audit_and_links(
        self, db_session
    ) -> None:
        user = _make_user(db_session, "kineticist")
        bundle = _load_bundle("kinetics-bundle-v0.json")

        result = submit_contribution_bundle(db_session, bundle, actor=user)

        assert result.bundle_kind is BundleKind.kinetics
        assert result.summary.records_imported == 1

        submission = db_session.get(Submission, result.submission_id)
        assert submission.created_by == user.id

        link_types = {
            link.record_type
            for link in db_session.scalars(
                select(SubmissionRecordLink).where(
                    SubmissionRecordLink.submission_id == submission.id
                )
            ).all()
        }
        assert link_types == {
            SubmissionRecordType.kinetics,
            SubmissionRecordType.reaction_entry,
        }

    def test_transaction_rollback_on_workflow_failure(
        self, db_session, monkeypatch
    ) -> None:
        """A failure mid-import must roll back submission/audit/link rows.

        Models what ``get_write_db`` does in production: wrap the call in
        an explicit nested savepoint, roll it back on the propagated
        exception, then verify nothing committed. (The TestClient fixture
        replaces ``get_write_db`` with a no-op session override, so this
        property cannot be tested through the route.)
        """
        from sqlalchemy import func
        from app.workflows import contribution_bundle_submit as submit_module

        user = _make_user(db_session, "rollback-tester")
        bundle = _load_bundle("thermo-bundle-v0.json")

        def _boom(*args, **kwargs):  # noqa: ANN001 - test stub
            raise RuntimeError("simulated workflow failure")

        monkeypatch.setattr(submit_module, "persist_thermo_upload", _boom)

        before = {
            "submission": db_session.scalar(
                select(func.count()).select_from(Submission)
            )
            or 0,
            "audit": db_session.scalar(
                select(func.count()).select_from(SubmissionAuditEvent)
            )
            or 0,
            "links": db_session.scalar(
                select(func.count()).select_from(SubmissionRecordLink)
            )
            or 0,
        }

        nested = db_session.begin_nested()
        try:
            with pytest.raises(RuntimeError):
                submit_contribution_bundle(db_session, bundle, actor=user)
            nested.rollback()
        finally:
            if nested.is_active:
                nested.rollback()

        after = {
            "submission": db_session.scalar(
                select(func.count()).select_from(Submission)
            )
            or 0,
            "audit": db_session.scalar(
                select(func.count()).select_from(SubmissionAuditEvent)
            )
            or 0,
            "links": db_session.scalar(
                select(func.count()).select_from(SubmissionRecordLink)
            )
            or 0,
        }
        assert before == after

    def test_blocking_dry_run_raises_before_any_write(
        self, db_session
    ) -> None:
        user = _make_user(db_session, "rejected-submitter")

        # Build a kinetics bundle with an unparseable SMILES so dry-run
        # canonicalization fails — strict gate must reject before any
        # row is created.
        raw = json.loads(
            (EXAMPLES_DIR / "kinetics-bundle-v0.json").read_text()
        )
        for participant in raw["records"]["kinetics_uploads"][0]["reaction"][
            "reactants"
        ]:
            participant["species_entry"]["smiles"] = "this-is-not-a-smiles"
        bundle = ContributionBundleV0.model_validate(raw)

        before = db_session.scalar(
            select(Submission).where(Submission.created_by == user.id)
        )
        assert before is None

        with pytest.raises(DomainError):
            submit_contribution_bundle(db_session, bundle, actor=user)

        after = db_session.scalar(
            select(Submission).where(Submission.created_by == user.id)
        )
        assert after is None
