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

That atomicity is right for the import and its record links, and wrong for
the audit event, and the difference is what a row *is* rather than when it is
written. A ``submission_record_link`` is load-bearing — approval flips exactly
the linked records, so a bundle imported without its links is permanently
unapprovable. The ``ingestion_succeeded`` event describes an import that has
already happened. Only the latter is confined to a ``SAVEPOINT``; see
:func:`_append_import_audit` and the comments at each site.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.db.models.kinetics import Kinetics
from app.db.models.thermo import Thermo
from app.schemas.contribution_bundle_dry_run import (
    ContributionBundleDryRunResult,
    DryRunMessageLevel,
    DryRunRecordType,
)
from app.schemas.contribution_bundle_submit import (
    ContributionBundleSubmitMessage,
    ContributionBundleSubmitResult,
    ContributionBundleSubmitSummary,
    ContributionBundleSubmittedRecord,
    SubmitReviewStatus,
    SubmittedRecordAction,
    SubmittedRecordType,
)
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.contribution_bundle import (
    BundleKind,
    ContributionBundleV0,
)
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.contribution_bundle_dry_run import dry_run_contribution_bundle
from app.services.literature_metadata import failure_scope
from app.services.literature_resolution import prefetch_literature_metadata
from app.services.record_review import ReviewPolicy
from app.services.rights import attest_from_deposit
from app.services.submission import (
    create_submission,
    link_record,
    mark_ingestion_succeeded,
)
from app.services.upload_request_warnings import (
    kinetics_request_warnings,
    thermo_request_warnings,
)
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.rehearsal import discard_unflushed_writes, rehearsal
from app.workflows.thermo import persist_thermo_upload

logger = logging.getLogger(__name__)


def _append_import_audit(
    session: Session,
    *,
    submission,
    bundle_kind: BundleKind,
    imported_count: int,
    linked_count: int,
) -> bool:
    """Append ``ingestion_succeeded`` without risking the imported bundle.

    Lowest failure probability in this workflow, largest blast radius: it runs
    after the biggest payload the API accepts has been fully imported. The
    event's ``summary`` is ``Text`` and its ``details_json`` is ``JSONB`` — the
    two column types that failed in the 2026-08-05 incident — so it is flushed
    inside a ``SAVEPOINT``, after an explicit flush that puts the whole import
    outside the savepoint's reach.

    Returns whether the event was written. Never raises for a reason confined
    to the event.
    """
    session.flush()
    savepoint = session.begin_nested()
    try:
        mark_ingestion_succeeded(
            session,
            submission=submission,
            summary=(
                f"Imported {imported_count} {bundle_kind.value} "
                f"record(s) from contribution bundle."
            ),
            details_json={
                "bundle_kind": bundle_kind.value,
                "records_imported": imported_count,
                "records_linked": linked_count,
            },
        )
    except Exception as exc:
        savepoint.rollback()
        logger.error(
            "ingestion_succeeded audit event could not be written for bundle "
            "submission_id=%s (kind=%s): %s. The bundle imported successfully "
            "and its records, links and review rows are intact; only this line "
            "of the submission's audit history is missing.",
            submission.id,
            bundle_kind.value,
            type(exc).__name__,
            exc_info=exc,
        )
        return False
    savepoint.commit()
    return True


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


def _import_warning_messages(
    warnings: list[UploadWarning],
    *,
    local_ref: str,
    record_type: DryRunRecordType,
) -> list[ContributionBundleSubmitMessage]:
    """Render one upload's warnings as bundle messages.

    The warning's own ``field``, ``code`` and ``message`` are kept exactly as
    the direct upload route returns them (``field`` is relative to that one
    upload); ``local_ref`` says which upload of the bundle it belongs to.
    """
    return [
        ContributionBundleSubmitMessage(
            level=DryRunMessageLevel.warning,
            code=w.code,
            message=w.message,
            field=w.field,
            local_ref=local_ref,
            record_type=record_type,
        )
        for w in warnings
    ]


# ---------------------------------------------------------------------------
# Per-family import
# ---------------------------------------------------------------------------


def _import_thermo_bundle(
    session: Session,
    bundle: ContributionBundleV0,
    *,
    actor_id: int,
    review_policy: ReviewPolicy,
    messages_out: list[ContributionBundleSubmitMessage],
) -> list[ContributionBundleSubmittedRecord]:
    """Import every thermo upload in ``bundle`` and return submitted-record rows.

    Each upload produces one ``imported`` row for the new ``thermo`` and one
    ``linked`` row for its parent ``species_entry`` (the immediate parent
    decided in Decision 1).

    The warnings each upload earns -- the request-derived ones and the ones
    the workflow appends -- are the ones ``POST /uploads/thermo`` returns for
    the same record, in the same order, and are appended to ``messages_out``.
    """
    records: list[ContributionBundleSubmittedRecord] = []
    for index, upload in enumerate(bundle.records.thermo_uploads):
        local_ref = f"thermo_uploads[{index}]"
        warnings = thermo_request_warnings(upload)
        thermo: Thermo = persist_thermo_upload(
            session,
            upload,
            created_by=actor_id,
            review_policy=review_policy,
            warnings_out=warnings,
        )
        messages_out.extend(
            _import_warning_messages(
                warnings, local_ref=local_ref, record_type=DryRunRecordType.thermo
            )
        )
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
    review_policy: ReviewPolicy,
    messages_out: list[ContributionBundleSubmitMessage],
) -> list[ContributionBundleSubmittedRecord]:
    """Import every kinetics upload in ``bundle`` and return submitted-record rows.

    Each upload produces one ``imported`` row for the new ``kinetics`` and
    one ``linked`` row for its parent ``reaction_entry``.

    Warnings are collected exactly as :func:`_import_thermo_bundle` does, and
    match ``POST /uploads/kinetics``.
    """
    records: list[ContributionBundleSubmittedRecord] = []
    # One bundle import: uploads that state the same determination content share one
    # reaction entry and so one determination, as the exported bundle grouped them.
    determination_anchors: dict[str, int] = {}
    for index, upload in enumerate(bundle.records.kinetics_uploads):
        local_ref = f"kinetics_uploads[{index}]"
        warnings = kinetics_request_warnings(upload)
        kinetics: Kinetics = persist_kinetics_upload(
            session,
            upload,
            created_by=actor_id,
            review_policy=review_policy,
            warnings=warnings,
            determination_anchors=determination_anchors,
            require_energy_sources=False,
        )
        messages_out.extend(
            _import_warning_messages(
                warnings, local_ref=local_ref, record_type=DryRunRecordType.kinetics
            )
        )
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
    #    The bundle's deposit-time license agreement is recorded against
    #    this submission by the authenticated submitter -- the hosted actor,
    #    never the local exporter label. Absent, nothing is recorded and the
    #    release gate refuses the records until a curator attests a basis.
    attest_from_deposit(
        session, submission=submission, rights=bundle.submission.rights, actor=actor
    )

    # 3. Run the per-family import through existing workflows. The
    #    review policy carries the moderation context: bundle submissions
    #    land `not_reviewed` and link every produced record back to this
    #    submission, so on approval we can flip them to approved in bulk.
    #
    #    `not_reviewed` is the honest word for a bundle nobody has opened
    #    yet. `under_review` begins when a curator picks the record up —
    #    a transition the review service already permits from here — and
    #    depositing straight into it made the status describe the queue
    #    rather than anyone's attention.
    review_policy = ReviewPolicy(
        status=RecordReviewStatus.not_reviewed,
        submission_id=submission.id,
    )
    import_messages: list[ContributionBundleSubmitMessage] = []
    if bundle.bundle_kind is BundleKind.thermo:
        records = _import_thermo_bundle(
            session,
            bundle,
            actor_id=actor.id,
            review_policy=review_policy,
            messages_out=import_messages,
        )
    else:
        records = _import_kinetics_bundle(
            session,
            bundle,
            actor_id=actor.id,
            review_policy=review_policy,
            messages_out=import_messages,
        )

    # 4. Create record links — products and immediate identity parents,
    #    deduped per (record_type, record_id) since a bundle may touch the
    #    same species_entry from multiple uploads.
    #
    #    These are deliberately NOT isolated from the import, unlike the audit
    #    event below. A ``submission_record_link`` is not a description of the
    #    bundle; it is the index a curator approves *through*
    #    (``approve_submission`` flips exactly the linked records). A bundle
    #    imported with missing links would be permanently unapprovable —
    #    stranded science that reads as ``not_reviewed`` forever. And because
    #    this runs before the response is determined, failing here yields an
    #    honest error to a client that can simply resubmit the same bundle.
    #    Isolation would trade a recoverable failure for an unrecoverable one.
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
    #
    #    This one *is* isolated. It is purely a description of an import that
    #    has already happened, and it lands after the largest payload the API
    #    accepts: the lowest probability of failure in the flow, attached to
    #    the largest blast radius. Losing it costs one line of a submission's
    #    audit history, which the submission row, its ``submission_created``
    #    event, the record links and the review rows all survive.
    imported_count = sum(
        1 for r in records if r.action is SubmittedRecordAction.imported
    )
    _append_import_audit(
        session,
        submission=submission,
        bundle_kind=bundle.bundle_kind,
        imported_count=imported_count,
        linked_count=len(records) - imported_count,
    )

    # 6. Carry warnings forward; add an ingestion_succeeded info note so
    #    the client renders the same message clients already see in
    #    server-side audit logs.
    #    The upload warnings the import produced come between the two: the
    #    gate's messages first, as before, then each upload's own warnings in
    #    upload order, then the closing note.
    messages = _carry_forward_messages(dry_run)
    messages.extend(import_messages)
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

    result = ContributionBundleSubmitResult(
        submission_id=submission.id,
        submission_ref=submission.public_ref,
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
    # Which of ``messages`` the import produced, as opposed to the dry-run
    # gate's own: a dry run that rehearses this function needs just those, to
    # add to a preview that already holds the gate's. A private attribute, so
    # it is not part of the response schema.
    result._import_messages = import_messages
    return result


def _literature_requests(node: object) -> Iterator[LiteratureUploadRequest]:
    """Every literature reference anywhere in a bundle, in document order."""
    if isinstance(node, LiteratureUploadRequest):
        yield node
    elif isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _literature_requests(getattr(node, name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _literature_requests(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _literature_requests(item)


def rehearse_contribution_bundle_submit(
    session: Session,
    bundle: ContributionBundleV0,
    *,
    actor: AppUser,
    import_warnings_out: list[ContributionBundleSubmitMessage] | None = None,
) -> Exception | None:
    """Run :func:`submit_contribution_bundle` and undo it; return its refusal.

    This is how ``/bundles/dry-run`` answers "will this submit?" (#577). It
    used to answer from the read-only preview alone, which is only submit's
    *gate*: every check inside ``persist_thermo_upload`` /
    ``persist_kinetics_upload`` -- the enthalpy-reference rule, reference
    resolution, ownership, role/type, reaction anchoring, database
    constraints -- ran on submit and never on a dry run, so a bundle could
    pass one and be refused by the other. Copying those checks into the
    preview would give the two routes a second place to disagree, so the
    dry run calls the very function the submit route calls, inside
    :func:`app.workflows.rehearsal.rehearsal` -- which rolls it back,
    refuses any attempt to publish it, and keeps it from out-waiting a real
    submit for a lock. The guarantees and their limits are stated there.

    Literature metadata is fetched *before* the rehearsal opens, so the
    Crossref/ISBN lookup a new reference needs is not made while the
    rehearsal holds row locks: the fetch is cached in-process
    (``app.services.literature_metadata``), and the rehearsal's own lookup
    then answers from the cache. A fetch that failed is not cached: another
    caller, or a later request, retries it. It is remembered only inside this
    function, through ``literature_metadata.failure_scope``, so the
    rehearsal's own lookup does not repeat the failed request while holding its
    locks; nothing outside this call can see it.

    :param import_warnings_out: Filled with the upload warnings submit would
        return for this bundle, so the dry run can report them (#647). Left
        empty when submit would refuse.
    :returns: ``None`` when submit would succeed, otherwise the exception it
        raised -- rendered by the caller through the app's own handlers --
        or the rehearsal's own contention or commit-refusal error.
    """
    discard_unflushed_writes(session)
    with failure_scope():
        prefetch_literature_metadata(session, _literature_requests(bundle.records))
        try:
            with rehearsal(session):
                result = submit_contribution_bundle(session, bundle, actor=actor)
                if import_warnings_out is not None:
                    import_warnings_out.extend(getattr(result, "_import_messages", ()))
        except Exception as exc:  # every failure is the verdict, whatever its type
            return exc
    return None


__all__ = [
    "_is_blocking",  # exported for unit tests
    "rehearse_contribution_bundle_submit",
    "submit_contribution_bundle",
]
