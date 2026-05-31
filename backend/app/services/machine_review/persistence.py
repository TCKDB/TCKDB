"""Thin persistence helpers for the append-only ``record_machine_review`` table.

This module is the durable counterpart to the pure context/currency stack: it
appends rows to :class:`~app.db.models.record_machine_review.RecordMachineReviewRow`
and projects them back into the pure
:class:`~app.services.machine_review.currency.StoredMachineReviewProjection` the
classifier consumes (policy
``backend/docs/specs/record_machine_review_policy.md`` §8).

Boundaries kept deliberately tight:

* **Append-only.** :func:`create_record_machine_review_row` only inserts; it
  never updates or deletes an existing row.
* **Classifier stays pure.** The DB wrapper
  (:func:`classify_record_machine_review_currency_from_rows`) only loads/projects
  rows; all currency logic lives in
  :func:`~app.services.machine_review.currency.classify_machine_review_currency`.
* **Non-interfering.** These helpers write only to ``record_machine_review``.
  They never touch ``submission.status``, ``record_review``, certification,
  deterministic evidence/trust, scientific records, or any public ``trust.*``
  fragment. Nothing here is wired into the upload path or any public read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import (
    MachineReviewCurrencyClassification,
    StoredMachineReviewProjection,
    classify_machine_review_currency,
)
from app.services.machine_review.read_model import RecordMachineReview


def stored_projection_from_record_machine_review_row(
    row: RecordMachineReviewRow,
) -> StoredMachineReviewProjection:
    """Project a persisted record-machine-review row into the classifier input.

    A pure value-for-value mapping: the row's currency key
    (``context_hash`` / ``context_schema_version`` / ``prompt_version`` /
    ``rubric_versions_json``), ``status``, ``reviewed_at``, and the latest-
    selection tiebreaks (``id``, ``source_audit_event_id``) become a
    :class:`StoredMachineReviewProjection`. The DB enum ``record_type`` /
    ``status`` are surfaced as their string/app-enum values. No DB access.
    """
    return StoredMachineReviewProjection(
        record_type=row.record_type.value,
        record_id=row.record_id,
        reviewed_at=row.reviewed_at,
        context_schema_version=row.context_schema_version,
        context_hash=row.context_hash,
        prompt_version=row.prompt_version,
        rubric_versions=dict(row.rubric_versions_json or {}),
        status=row.status,
        id=row.id,
        source_audit_event_id=row.source_audit_event_id,
    )


def classify_record_machine_review_currency_from_rows(
    rows: Sequence[RecordMachineReviewRow],
    *,
    current_context: MachineReviewContextDigest,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
) -> MachineReviewCurrencyClassification:
    """Classify persisted record-machine-review rows for one record.

    A thin wrapper: projects each row via
    :func:`stored_projection_from_record_machine_review_row` and delegates to the
    pure :func:`classify_machine_review_currency`. ``rows`` are assumed already
    scoped to one record (the caller filters); ordering and current/stale/
    historical selection are the classifier's job.
    """
    projections = [
        stored_projection_from_record_machine_review_row(row) for row in rows
    ]
    return classify_machine_review_currency(
        projections,
        current_context=current_context,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
    )


def create_record_machine_review_row(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    review: RecordMachineReview,
    context_digest: MachineReviewContextDigest,
    prompt_version: str,
    rubric_versions: Mapping[str, str],
    source_submission_id: int | None = None,
    source_audit_event_id: int | None = None,
) -> RecordMachineReviewRow:
    """Append a persisted record-machine-review row (append-only).

    Inserts exactly one new row from an in-memory :class:`RecordMachineReview`
    pass plus its currency stamps; it never updates an existing row. The
    review's ``findings`` are serialised to ``findings_json``; the digest's
    ``context_hash`` / ``context_schema_version`` and the supplied
    ``prompt_version`` / ``rubric_versions`` form the currency key; ``model`` /
    ``provider`` / ``curator_priority`` / ``status`` / ``reviewed_at`` are copied
    from the review. Source ids default to the review's own
    ``submission_id`` / ``audit_event_id`` when not supplied (the read model's
    ``audit_event_id`` is this table's ``source_audit_event_id``).

    ``reviewed_at`` is required (the table's primary latest-selection key); a
    review without one raises :class:`ValueError`. The row is added to the
    session and flushed so its ``id`` is populated, but the surrounding
    transaction is owned by the caller (this helper does not commit).

    Writes only to ``record_machine_review`` — no other table, no scientific
    record, no submission status, no trust/evidence state is touched.
    """
    if review.reviewed_at is None:
        raise ValueError(
            "RecordMachineReview.reviewed_at is required to append a "
            "record_machine_review row (it is the primary latest-selection key)."
        )

    resolved_record_type = (
        record_type
        if isinstance(record_type, SubmissionRecordType)
        else SubmissionRecordType(record_type)
    )
    resolved_submission_id = (
        source_submission_id
        if source_submission_id is not None
        else review.submission_id
    )
    resolved_audit_event_id = (
        source_audit_event_id
        if source_audit_event_id is not None
        else review.audit_event_id
    )
    curator_priority = (
        review.curator_priority.value
        if review.curator_priority is not None
        else None
    )

    row = RecordMachineReviewRow(
        record_type=resolved_record_type,
        record_id=record_id,
        status=review.status,
        curator_priority=curator_priority,
        summary=None,
        findings_json=[f.model_dump(mode="json") for f in review.findings],
        model=review.model,
        provider=review.provider,
        context_hash=context_digest.context_hash,
        context_schema_version=context_digest.context_schema_version,
        prompt_version=prompt_version,
        rubric_versions_json=dict(rubric_versions),
        source_submission_id=resolved_submission_id,
        source_audit_event_id=resolved_audit_event_id,
        reviewed_at=review.reviewed_at,
    )
    session.add(row)
    session.flush()
    return row
