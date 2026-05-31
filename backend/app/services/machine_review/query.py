"""Private read/query service for persisted ``record_machine_review`` rows.

This is the **single read path** over the append-only
:class:`~app.db.models.record_machine_review.RecordMachineReviewRow` table that
later consumers (admin inspection, re-review triggers, eventual public-trust
projection) build on. It is deliberately read-only and private: nothing here is
wired into a public scientific read, no ``trust.machine_review`` is emitted, and
the public ``TrustFragment`` is untouched (policy
``backend/docs/specs/record_machine_review_policy.md`` §4/§8).

Boundaries:

* **Read-only.** These functions only ``SELECT``; they never insert, update, or
  delete a row, and never mutate ``submission.status`` / approval / rejection
  fields, scientific records, deterministic evidence/trust, or any public
  fragment.
* **Ordering parity.** Rows are returned newest-first in the *exact*
  latest-selection order the classifier uses (policy §4): ``reviewed_at`` DESC,
  ``source_audit_event_id`` DESC NULLS LAST, ``id`` DESC NULLS LAST. Persisted
  rows always carry a unique non-null ``id``, so these three keys fully
  determine the order.
* **Reuse.** Row→projection and classification reuse the persistence helpers
  (:func:`stored_projection_from_record_machine_review_row`,
  :func:`classify_record_machine_review_currency_from_rows`); the classifier
  itself stays pure.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import UnaryExpression

from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import MachineReviewCurrencyClassification
from app.services.machine_review.persistence import (
    classify_record_machine_review_currency_from_rows,
)


def _latest_first_order_by() -> list[UnaryExpression]:
    """The ORDER BY mirroring the classifier's latest-selection policy (§4).

    ``reviewed_at`` DESC, then ``source_audit_event_id`` DESC NULLS LAST, then
    ``id`` DESC NULLS LAST. ``.nulls_last()`` is emitted explicitly because
    PostgreSQL otherwise sorts NULLs first under DESC, which would invert the
    policy's "a real id outranks NULL". ``id`` is non-null in practice, but the
    clause is kept for exact parity with the classifier key.
    """
    return [
        RecordMachineReviewRow.reviewed_at.desc(),
        RecordMachineReviewRow.source_audit_event_id.desc().nulls_last(),
        RecordMachineReviewRow.id.desc().nulls_last(),
    ]


def _record_type_value(record_type: str | SubmissionRecordType) -> SubmissionRecordType:
    """Coerce a record-type string to the controlled enum for the WHERE clause."""
    if isinstance(record_type, SubmissionRecordType):
        return record_type
    return SubmissionRecordType(record_type)


def list_record_machine_review_rows_for_record(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    limit: int | None = None,
) -> list[RecordMachineReviewRow]:
    """Return persisted machine-review rows for one record, newest first.

    Queries only rows matching the exact ``(record_type, record_id)`` and orders
    them by the classifier's latest-selection policy (:func:`_latest_first_order_by`).
    ``limit`` is applied **after** ordering, so ``limit=1`` yields the single
    latest row. Returns an empty list when no rows exist. Read-only.
    """
    stmt = (
        select(RecordMachineReviewRow)
        .where(
            RecordMachineReviewRow.record_type == _record_type_value(record_type),
            RecordMachineReviewRow.record_id == record_id,
        )
        .order_by(*_latest_first_order_by())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def get_latest_record_machine_review_row(
    session: Session,
    *,
    record_type: str,
    record_id: int,
) -> RecordMachineReviewRow | None:
    """Return the single latest persisted row for one record, or ``None``.

    The latest is the first row under the policy ordering
    (:func:`_latest_first_order_by`); ``None`` when no rows exist. Read-only.
    """
    rows = list_record_machine_review_rows_for_record(
        session, record_type=record_type, record_id=record_id, limit=1
    )
    return rows[0] if rows else None


def get_record_machine_review_currency_for_record(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    current_context: MachineReviewContextDigest,
    active_prompt_version: str,
    active_rubric_versions: Mapping[str, str],
) -> MachineReviewCurrencyClassification:
    """Classify persisted machine-review currency for one record.

    Loads every persisted row for the record and delegates to the pure
    classifier (via :func:`classify_record_machine_review_currency_from_rows`):
    the latest row is ``current`` iff its currency key matches the active recipe,
    else ``stale`` (with reasons); older rows are ``historical``. Returns a
    ``not_run`` classification when no rows exist. Read-only; the classifier
    re-derives the latest internally, so the result is independent of input
    order.
    """
    rows = list_record_machine_review_rows_for_record(
        session, record_type=record_type, record_id=record_id
    )
    return classify_record_machine_review_currency_from_rows(
        rows,
        current_context=current_context,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
    )
