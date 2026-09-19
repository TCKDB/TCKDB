"""The rights basis behind a scientific record, read through its submissions.

A record does not carry a license. The deposit it arrived in does -- as a
standing ``submission_rights_attestation`` on the submission the record is
linked to through ``submission_record_link``. This module walks that join
for a batch of ``(record_type, record_id)`` pairs and hands the release layer
one answer per record: which submissions link it, and what, if anything,
stands attested for each.

Two consumers, one reader, so they cannot disagree about what "attested"
means:

* :mod:`app.services.release.curation` refuses a selection or a publication
  when any link is unattested or attested under a different license;
* :mod:`app.services.release.artifacts` prints the same facts on every line
  of ``selected_records.ndjson`` and ``candidate_records.ndjson``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.services.rights import standing_attestations

RecordPair = tuple[SubmissionRecordType, int]


@dataclass(frozen=True)
class LinkedRights:
    """One submission a record is linked to, and what stands attested for it."""

    submission_id: int
    submission_ref: str
    attestation: SubmissionRightsAttestation | None


def linked_rights(
    session: Session, *, pairs: Iterable[RecordPair]
) -> dict[RecordPair, list[LinkedRights]]:
    """Every linked submission per record, with its standing attestation.

    A record with no link at all maps to an empty list -- the caller decides
    what that means (the release layer: refuse). Links are deduplicated per
    submission, since one submission may link a record under several roles.
    """
    wanted = set(pairs)
    if not wanted:
        return {}

    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in wanted:
        by_type.setdefault(record_type, []).append(record_id)

    links: dict[RecordPair, dict[int, str]] = {pair: {} for pair in wanted}
    for record_type, ids in by_type.items():
        rows = session.execute(
            select(
                SubmissionRecordLink.record_id,
                Submission.id,
                Submission.public_ref,
            )
            .join(Submission, Submission.id == SubmissionRecordLink.submission_id)
            .where(
                SubmissionRecordLink.record_type == record_type,
                SubmissionRecordLink.record_id.in_(sorted(set(ids))),
            )
            .order_by(Submission.id)
        ).all()
        for record_id, submission_id, submission_ref in rows:
            links[(record_type, record_id)][submission_id] = submission_ref

    submission_ids = {sid for by_sub in links.values() for sid in by_sub}
    standing = standing_attestations(session, submission_ids=submission_ids)

    return {
        pair: [
            LinkedRights(
                submission_id=submission_id,
                submission_ref=submission_ref,
                attestation=standing.get(submission_id),
            )
            for submission_id, submission_ref in sorted(by_sub.items())
        ]
        for pair, by_sub in links.items()
    }


def rights_summary(
    rights: dict[RecordPair, list[LinkedRights]], *, data_license: str
) -> dict[str, Any]:
    """The manifest's ``rights`` block over everything a release ships.

    Counts *distinct* attestations, not links: one deposit of forty records
    is one agreement, and the block says how many agreements stand behind
    the release rather than how many rows repeat them.
    """
    seen: dict[int, SubmissionRightsAttestation] = {}
    for links in rights.values():
        for link in links:
            if link.attestation is not None:
                seen[link.attestation.id] = link.attestation
    basis_kinds: dict[str, int] = {}
    for row in seen.values():
        basis_kinds[row.basis.value] = basis_kinds.get(row.basis.value, 0) + 1
    return {
        "data_license": data_license,
        "attestation_count": len(seen),
        "basis_kinds": dict(sorted(basis_kinds.items())),
    }


__all__ = ["LinkedRights", "RecordPair", "linked_rights", "rights_summary"]
