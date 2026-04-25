"""Response schemas for the hosted contribution-bundle submit/import endpoint.

Submit/import is the first write path for ``ContributionBundleV0``. It runs
the dry-run preview as a strict gate, then — if no errors are reported —
imports the bundle's records through the existing thermo/kinetics upload
workflows, creates a :class:`Submission` plus matching audit/link rows, and
returns a structured summary describing the imported records.

The response intentionally uses ``submitted`` / ``imported`` /
``unreviewed`` / ``pending_review`` wording. Records returned here are
publicly visible by default but are **not** curator-approved; the response
states that explicitly so clients cannot mistake validation for curation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.db.models.common import SubmissionStatus
from app.schemas.common import SchemaBase
from app.schemas.contribution_bundle_dry_run import ContributionBundleDryRunMessage
from app.schemas.workflows.contribution_bundle import BundleKind


class SubmittedRecordType(str, Enum):
    """Domain row kind reported in :class:`ContributionBundleSubmittedRecord`.

    Kept narrower than :class:`app.db.models.common.SubmissionRecordType`
    because v0 only imports thermo/kinetics product rows plus their immediate
    identity parents (species_entry, reaction_entry).
    """

    thermo = "thermo"
    kinetics = "kinetics"
    species_entry = "species_entry"
    reaction_entry = "reaction_entry"


class SubmittedRecordAction(str, Enum):
    """What submit/import did for a given record.

    ``imported`` covers append-only product rows the importer just wrote.
    ``linked`` covers identity rows (species_entry, reaction_entry) that the
    submission now references for traceability — the row may have been
    created during this submit or reused from a prior import; either way the
    link is new.
    """

    imported = "imported"
    linked = "linked"


class SubmitReviewStatus(str, Enum):
    """Curation/review state surfaced to clients in the submit response.

    Distinct from :class:`SubmissionStatus`: that enum is the moderation
    lifecycle column on the row, while this is the human-readable trust
    state for response payloads. ``unreviewed`` is the only value submit/v0
    ever returns — full lifecycle exposure is a later milestone.
    """

    unreviewed = "unreviewed"


class ContributionBundleSubmitMessage(ContributionBundleDryRunMessage):
    """A submit-time message about the bundle as a whole.

    Reuses the dry-run message shape so clients that already render dry-run
    feedback can render submit feedback unchanged. Submit messages typically
    carry forward dry-run warnings/errors and add an ``ingestion_succeeded``
    info note when the import committed.
    """


class ContributionBundleSubmittedRecord(SchemaBase):
    """One scientific row touched by a successful bundle submit.

    :param record_type: Which domain table this row lives in.
    :param record_id: Hosted database id of the row.
    :param action: ``imported`` for append-only product rows the importer
        just wrote; ``linked`` for identity rows (species_entry,
        reaction_entry) the submission now references.
    :param review_status: Always ``unreviewed`` in v0 — submit/import never
        marks anything curated.
    :param local_ref: Optional bundle-local pointer (e.g. ``thermo_uploads[0]``)
        so clients can correlate hosted ids back to bundle entries.
    """

    record_type: SubmittedRecordType
    record_id: int
    action: SubmittedRecordAction
    review_status: SubmitReviewStatus = SubmitReviewStatus.unreviewed
    local_ref: str | None = None


class ContributionBundleSubmitSummary(SchemaBase):
    """Counts across all rows the submit produced or linked.

    Counts are derived from ``ContributionBundleSubmitResult.records``;
    they are not an alternative source of truth.
    """

    records_imported: int = Field(ge=0)
    records_linked: int = Field(ge=0)
    warnings: int = Field(ge=0)


class ContributionBundleSubmitResult(SchemaBase):
    """Top-level submit response.

    A successful HTTP 201 response carries this shape; dry-run blocking
    errors surface as 422 with a structured detail payload, and unsupported
    bundle kinds surface as 422 from the bundle schema itself.

    :param submission_id: Hosted ``submission`` row id.
    :param status: Raw moderation-lifecycle status on the submission row
        (``pending`` in v0; reserved so future statuses surface here without
        a schema change).
    :param review_status: Human-readable trust state — always
        ``unreviewed`` in v0.
    :param bundle_kind: Bundle family that was imported.
    :param summary: Counts derived from ``records``.
    :param records: Per-row report covering every imported and linked row.
    :param messages: Bundle-wide notes (dry-run warnings carried forward,
        plus an ``ingestion_succeeded`` info entry).
    """

    submission_id: int
    status: SubmissionStatus
    review_status: SubmitReviewStatus = SubmitReviewStatus.unreviewed
    bundle_kind: BundleKind
    summary: ContributionBundleSubmitSummary
    records: list[ContributionBundleSubmittedRecord] = Field(default_factory=list)
    messages: list[ContributionBundleSubmitMessage] = Field(default_factory=list)
