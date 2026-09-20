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
from enum import Enum

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import UnaryExpression

from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import MachineReviewCurrencyClassification
from app.services.machine_review.persistence import (
    classify_record_machine_review_currency_from_rows,
)

#: The ``provider`` namespace reserved for deterministic scientific-check
#: runners (e.g. ``app.services.external_comparison.cp``) that persist
#: through :func:`~app.services.machine_review.persistence.create_record_machine_review_row`
#: alongside the reviewer recipe (LLM / fake-provider) rows for the same
#: record. This is the single source of truth for that namespace -- any
#: scientific-check runner that wants its rows to be discriminated from the
#: reviewer family imports this constant rather than redefining its own
#: string (see ``app.services.external_comparison.cp.PROVIDER``).
SCIENTIFIC_CHECK_PROVIDER_NAMESPACE = "tckdb.scientific_checks"


class MachineReviewRecordFamily(str, Enum):
    """Which rows a machine-review query should see for a record.

    ``record_machine_review`` is one append-only table shared by two
    unrelated kinds of row: the **reviewer** recipe (human/LLM machine
    review, ``computed_thermo_v1`` / ``machine_review_v1`` and friends) and
    deterministic **scientific-check** runners (e.g. the review-tier
    external-Cp-comparison check) that persist through the same helper with
    ``provider=SCIENTIFIC_CHECK_PROVIDER_NAMESPACE``. Before this enum
    existed, every reader of this table implicitly saw both kinds mixed
    together, newest-first -- which meant a scientific-check row (whose
    recipe a reviewer-family currency check never matches) could outrank and
    demote a genuine reviewer-family review from "current" to "historical".

    ``reviewer`` (the default, preserving every existing consumer's
    behaviour) selects rows whose ``provider`` is ``NULL`` or anything
    *outside* :data:`SCIENTIFIC_CHECK_PROVIDER_NAMESPACE`.

    ``scientific_check`` selects rows *inside* that namespace, optionally
    narrowed further by ``model`` (each scientific-check runner's
    :data:`~app.services.external_comparison.cp.RUNNER_VERSION`-style
    identity) so "the current run of check X" is a well-defined notion.
    """

    reviewer = "reviewer"
    scientific_check = "scientific_check"


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
    family: MachineReviewRecordFamily = MachineReviewRecordFamily.reviewer,
    model: str | None = None,
) -> list[RecordMachineReviewRow]:
    """Return persisted machine-review rows for one record, newest first.

    Queries only rows matching the exact ``(record_type, record_id)`` and orders
    them by the classifier's latest-selection policy (:func:`_latest_first_order_by`).
    ``limit`` is applied **after** ordering, so ``limit=1`` yields the single
    latest row. Returns an empty list when no rows exist. Read-only.

    ``family`` (default :attr:`MachineReviewRecordFamily.reviewer`) scopes the
    rows to one of the two kinds sharing this table -- see
    :class:`MachineReviewRecordFamily`. The default reproduces every caller's
    behaviour from before this parameter existed. ``model`` is accepted only
    to further narrow :attr:`~MachineReviewRecordFamily.scientific_check`
    (e.g. to one runner's identity) and is ignored for the ``reviewer``
    family.
    """
    stmt = (
        select(RecordMachineReviewRow)
        .where(
            RecordMachineReviewRow.record_type == _record_type_value(record_type),
            RecordMachineReviewRow.record_id == record_id,
        )
        .order_by(*_latest_first_order_by())
    )
    if family is MachineReviewRecordFamily.scientific_check:
        stmt = stmt.where(
            RecordMachineReviewRow.provider == SCIENTIFIC_CHECK_PROVIDER_NAMESPACE
        )
        if model is not None:
            stmt = stmt.where(RecordMachineReviewRow.model == model)
    else:
        stmt = stmt.where(
            or_(
                RecordMachineReviewRow.provider.is_(None),
                RecordMachineReviewRow.provider != SCIENTIFIC_CHECK_PROVIDER_NAMESPACE,
            )
        )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def get_latest_record_machine_review_row(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    family: MachineReviewRecordFamily = MachineReviewRecordFamily.reviewer,
    model: str | None = None,
) -> RecordMachineReviewRow | None:
    """Return the single latest persisted row for one record, or ``None``.

    The latest is the first row under the policy ordering
    (:func:`_latest_first_order_by`); ``None`` when no rows exist. Read-only.
    ``family`` / ``model`` are forwarded to
    :func:`list_record_machine_review_rows_for_record` -- see there.
    """
    rows = list_record_machine_review_rows_for_record(
        session,
        record_type=record_type,
        record_id=record_id,
        limit=1,
        family=family,
        model=model,
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
    family: MachineReviewRecordFamily = MachineReviewRecordFamily.reviewer,
    model: str | None = None,
) -> MachineReviewCurrencyClassification:
    """Classify persisted machine-review currency for one record.

    Loads every persisted row for the record **within the requested
    family** (default :attr:`MachineReviewRecordFamily.reviewer` -- see
    :class:`MachineReviewRecordFamily`) and delegates to the pure classifier
    (via :func:`classify_record_machine_review_currency_from_rows`): the
    latest row is ``current`` iff its currency key matches the active
    recipe, else ``stale`` (with reasons); older rows are ``historical``.
    Returns a ``not_run`` classification when no rows exist. Read-only; the
    classifier re-derives the latest internally, so the result is
    independent of input order.

    Before ``family`` existed, this always loaded every row for
    ``(record_type, record_id)`` regardless of ``provider`` -- so a
    scientific-check row (whose recipe a reviewer-family active recipe never
    matches) could be newest, classify as ``stale``, and demote a genuine
    current reviewer-family review to ``historical``. Restricting to one
    family at a time removes that cross-family interference.
    """
    rows = list_record_machine_review_rows_for_record(
        session,
        record_type=record_type,
        record_id=record_id,
        family=family,
        model=model,
    )
    return classify_record_machine_review_currency_from_rows(
        rows,
        current_context=current_context,
        active_prompt_version=active_prompt_version,
        active_rubric_versions=active_rubric_versions,
    )
