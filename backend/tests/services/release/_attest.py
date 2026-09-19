"""Put a scientific record behind a licensed deposit, the way a real upload does.

A release refuses any record whose deposits do not all carry a standing
rights attestation naming the release's license. Fixtures that create
records straight through the factories therefore have to do what
``open_upload_submission`` does for a real upload: open a submission, link
the records to it, and record the depositor's agreement. This is that, in
one call, so the test files say *what* they license rather than how.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import RightsBasisKind, SubmissionKind, SubmissionRecordType
from app.db.models.submission import Submission
from app.services.rights import record_attestation
from app.services.submission import create_submission, link_records

DEFAULT_LICENSE = "CC-BY-4.0"


def deposit_and_attest(
    session: Session,
    *,
    depositor: AppUser,
    records: Iterable[tuple[SubmissionRecordType, int]],
    license_id: str = DEFAULT_LICENSE,
    kind: SubmissionKind = SubmissionKind.thermo,
    title: str = "attested test deposit",
) -> Submission:
    """One submission by ``depositor``, linking ``records``, attested under ``license_id``.

    The attestation is a ``depositor_agreement`` by the depositor -- exactly
    what the ``rights`` fragment of an upload produces -- so the records are
    selectable into a release that publishes under ``license_id``.
    """
    submission = create_submission(
        session,
        created_by=depositor.id,
        submission_kind=kind,
        title=title,
    )
    link_records(
        session,
        submission=submission,
        records=[(record_type, record_id, None) for record_type, record_id in records],
    )
    record_attestation(
        session,
        submission=submission,
        license_id=license_id,
        basis=RightsBasisKind.depositor_agreement,
        actor=depositor,
    )
    return submission


def attest_thermo(
    session: Session, *, depositor: AppUser, rows, license_id: str = DEFAULT_LICENSE
) -> Submission:
    """Shorthand for the common case: thermo rows, one deposit."""
    return deposit_and_attest(
        session,
        depositor=depositor,
        records=[(SubmissionRecordType.thermo, row.id) for row in rows],
        license_id=license_id,
    )
