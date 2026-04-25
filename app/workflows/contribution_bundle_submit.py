"""Hosted contribution-bundle submit/import workflow.

Composes the existing dry-run preview (as a strict gate) with the existing
thermo and kinetics upload workflows to import a :class:`ContributionBundleV0`
into the hosted database. Owns:

* the dry-run gate (anything blocking → reject before any write),
* import orchestration through the existing per-family workflows,
* :class:`Submission`, :class:`SubmissionAuditEvent`, and
  :class:`SubmissionRecordLink` creation,
* result/summary construction for the response.

Transaction management lives in the route's ``get_write_db`` dependency:
this function only needs to *raise* on failure for everything (including
the submission, audit, and link rows it created moments earlier) to roll
back atomically.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.kinetics import Kinetics
from app.db.models.thermo import Thermo
from app.schemas.contribution_bundle_dry_run import (
    ContributionBundleDryRunResult,
    DryRunAction,
    DryRunMessageLevel,
)
from app.schemas.contribution_bundle_submit import (
    ContributionBundleSubmitMessage,
    ContributionBundleSubmitResult,
    ContributionBundleSubmitSummary,
    ContributionBundleSubmittedRecord,
    SubmittedRecordAction,
    SubmittedRecordType,
    SubmitReviewStatus,
)
from app.schemas.workflows.contribution_bundle import (
    BundleKind,
    ContributionBundleV0,
)
from app.services.contribution_bundle_dry_run import dry_run_contribution_bundle
from app.services.submission import (
    create_submission,
    link_record,
    mark_ingestion_succeeded,
)
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.thermo import persist_thermo_upload


# ---------------------------------------------------------------------------
# Dry-run gate
# ---------------------------------------------------------------------------


def _is_blocking(dry_run: ContributionBundleDryRunResult) -> bool:
    """Return True iff dry-run reports anything that should block import.

    Strict policy (Decision 2 in the design notes): a bundle blocks the
    submit/import path if **any** of the following is true:

    * ``bundle_valid`` is False — the bundle did not pass dry-run's own
      structural/identity checks.
    * the summary reports any item-level or message-level errors
      (``summary.errors > 0``).
    * any item carries an ``unsupported`` action — v0 only supports
      thermo/kinetics, so an unsupported preview means there is no real
      import path for that record and we must not silently drop it.

    Warnings are *not* blocking; they are carried forward into the submit
    response so the client can render them but the import still commits.
    """
    if not dry_run.bundle_valid:
        return True
    if dry_run.summary.errors > 0:
        return True
    if dry_run.summary.unsupported > 0:
        return True
    return False


def _carry_forward_messages(
    dry_run: ContributionBundleDryRunResult,
) -> list[ContributionBundleSubmitMessage]:
    """Forward dry-run warnings into the submit response.

    Errors are not forwarded because a blocking dry-run never reaches the
    response builder; if any non-error dry-run message exists it is
    informational/warning text the client should still see.
    """
    return [
        ContributionBundleSubmitMessage(
            level=msg.level,
            code=msg.code,
            message=msg.message,
            field=msg.field,
            local_ref=msg.local_ref,
            record_type=msg.record_type,
        )
        for msg in dry_run.messages
        if msg.level is not DryRunMessageLevel.error
    ]


# ---------------------------------------------------------------------------
# Per-family import
# ---------------------------------------------------------------------------


def _import_thermo_bundle(
    session: Session,
    bundle: ContributionBundleV0,
    *,
    actor_id: int,
) -> list[ContributionBundleSubmittedRecord]:
    """Import every thermo upload in ``bundle`` and return submitted-record rows.

    Each upload produces one ``imported`` row for the new ``thermo`` and one
    ``linked`` row for its parent ``species_entry`` (the immediate parent
    decided in Decision 1).
    """
    records: list[ContributionBundleSubmittedRecord] = []
    for index, upload in enumerate(bundle.records.thermo_uploads):
        thermo: Thermo = persist_thermo_upload(
            session, upload, created_by=actor_id
        )
        local_ref = f"thermo_uploads[{index}]"
        records.append(
            ContributionBundleSubmittedRecord(
                record_type=SubmittedRecordType.thermo,
                record_id=thermo.id,
                action=SubmittedRecordAction.imported,
                local_ref=local_ref,
            )
        )
        records.append(
            ContributionBundleSubmittedRecord(
                record_type=SubmittedRecordType.species_entry,
                record_id=thermo.species_entry_id,
                action=SubmittedRecordAction.linked,
                local_ref=f"{local_ref}.species_entry",
            )
        )
    return records


def _import_kinetics_bundle(
    session: Session,
    bundle: ContributionBundleV0,
    *,
    actor_id: int,
) -> list[ContributionBundleSubmittedRecord]:
    """Import every kinetics upload in ``bundle`` and return submitted-record rows.

    Each upload produces one ``imported`` row for the new ``kinetics`` and
    one ``linked`` row for its parent ``reaction_entry``.
    """
    records: list[ContributionBundleSubmittedRecord] = []
    for index, upload in enumerate(bundle.records.kinetics_uploads):
        kinetics: Kinetics = persist_kinetics_upload(
            session, upload, created_by=actor_id
        )
        local_ref = f"kinetics_uploads[{index}]"
        records.append(
            ContributionBundleSubmittedRecord(
                record_type=SubmittedRecordType.kinetics,
                record_id=kinetics.id,
                action=SubmittedRecordAction.imported,
                local_ref=local_ref,
            )
        )
        records.append(
            ContributionBundleSubmittedRecord(
                record_type=SubmittedRecordType.reaction_entry,
                record_id=kinetics.reaction_entry_id,
                action=SubmittedRecordAction.linked,
                local_ref=f"{local_ref}.reaction",
            )
        )
    return records


# ---------------------------------------------------------------------------
# Submission/audit/link wiring
# ---------------------------------------------------------------------------


# Map bundle kind → submission kind (the moderation-layer classification).
_BUNDLE_KIND_TO_SUBMISSION_KIND: dict[BundleKind, SubmissionKind] = {
    BundleKind.thermo: SubmissionKind.thermo,
    BundleKind.kinetics: SubmissionKind.kinetics,
}

# Map our narrow submitted-record vocabulary onto the broader
# SubmissionRecordType enum used by the submission_record_link table.
_SUBMITTED_TO_LINK_TYPE: dict[SubmittedRecordType, SubmissionRecordType] = {
    SubmittedRecordType.thermo: SubmissionRecordType.thermo,
    SubmittedRecordType.kinetics: SubmissionRecordType.kinetics,
    SubmittedRecordType.species_entry: SubmissionRecordType.species_entry,
    SubmittedRecordType.reaction_entry: SubmissionRecordType.reaction_entry,
}


def submit_contribution_bundle(
    session: Session,
    bundle: ContributionBundleV0,
    *,
    actor: AppUser,
) -> ContributionBundleSubmitResult:
    """Validate, import, and record-link a contribution bundle.

    :param session: Active write session. Transaction management is the
        caller's responsibility (the FastAPI route uses ``get_write_db``);
        any exception raised here rolls back the entire submit.
    :param bundle: Schema-validated contribution bundle.
    :param actor: Authenticated hosted user. Becomes ``created_by`` on the
        submission *and* on every imported scientific row — local exporter
        metadata in ``bundle.exporter`` is provenance only and is never
        used as the hosted actor identity.
    :returns: Structured submit result describing the imported rows and
        their unreviewed status.
    :raises DomainError: When the dry-run gate reports blocking errors or
        when the bundle kind is unsupported by v0.
    """
    if bundle.bundle_kind not in _BUNDLE_KIND_TO_SUBMISSION_KIND:
        # Schema validation should have rejected this already; this is a
        # belt-and-braces guard so an unsupported kind never silently
        # creates a submission row.
        raise DomainError(
            f"Bundle kind {bundle.bundle_kind.value!r} is not supported by "
            "hosted submit/import v0."
        )

    # 1. Strict dry-run gate. Run before any writes; raise on blocking.
    dry_run = dry_run_contribution_bundle(session, bundle)
    if _is_blocking(dry_run):
        raise DomainError(
            "Bundle failed hosted dry-run validation; see dry-run errors."
        )

    # 2. Create the submission shell. Defaults to SubmissionStatus.pending,
    #    which is the existing enum value that means "publicly visible via
    #    read APIs that don't gate on review, but not curator-approved."
    submission = create_submission(
        session,
        created_by=actor.id,
        submission_kind=_BUNDLE_KIND_TO_SUBMISSION_KIND[bundle.bundle_kind],
        source_kind=SubmissionSourceKind.api,
        title=bundle.submission.title,
        summary=bundle.submission.summary,
    )

    # 3. Run the per-family import through existing workflows.
    if bundle.bundle_kind is BundleKind.thermo:
        records = _import_thermo_bundle(session, bundle, actor_id=actor.id)
    else:
        records = _import_kinetics_bundle(session, bundle, actor_id=actor.id)

    # 4. Create record links — products and immediate identity parents,
    #    deduped per (record_type, record_id) since a bundle may touch the
    #    same species_entry from multiple uploads.
    seen: set[tuple[SubmittedRecordType, int]] = set()
    for rec in records:
        key = (rec.record_type, rec.record_id)
        if key in seen:
            continue
        seen.add(key)
        link_record(
            session,
            submission=submission,
            record_type=_SUBMITTED_TO_LINK_TYPE[rec.record_type],
            record_id=rec.record_id,
        )

    # 5. Audit-log the successful import. Status stays at ``pending`` —
    #    ingestion success is not curator approval.
    imported_count = sum(
        1 for r in records if r.action is SubmittedRecordAction.imported
    )
    mark_ingestion_succeeded(
        session,
        submission=submission,
        summary=(
            f"Imported {imported_count} {bundle.bundle_kind.value} "
            f"record(s) from contribution bundle."
        ),
        details_json={
            "bundle_kind": bundle.bundle_kind.value,
            "records_imported": imported_count,
            "records_linked": len(records) - imported_count,
        },
    )

    # 6. Carry warnings forward; add an ingestion_succeeded info note so
    #    the client renders the same message clients already see in
    #    server-side audit logs.
    messages = _carry_forward_messages(dry_run)
    messages.append(
        ContributionBundleSubmitMessage(
            level=DryRunMessageLevel.info,
            code="ingestion_succeeded",
            message=(
                "Bundle imported successfully. Records are publicly visible "
                "but unreviewed; curator review is a separate, future step."
            ),
        )
    )

    return ContributionBundleSubmitResult(
        submission_id=submission.id,
        status=submission.status,
        review_status=SubmitReviewStatus.unreviewed,
        bundle_kind=bundle.bundle_kind,
        summary=ContributionBundleSubmitSummary(
            records_imported=imported_count,
            records_linked=len(records) - imported_count,
            warnings=sum(
                1 for m in messages if m.level is DryRunMessageLevel.warning
            ),
        ),
        records=records,
        messages=messages,
    )


__all__ = [
    "submit_contribution_bundle",
    "_is_blocking",  # exported for unit tests
]
