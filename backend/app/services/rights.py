"""Recording who agreed to license a deposit, and reading back what stands.

The single choke point for ``submission_rights_attestation``. Every path
that opens a submission -- the twelve direct upload routes, the async job
enqueue, and the contribution-bundle importer -- passes the upload's
``rights`` fragment through :func:`attest_from_deposit`; curators record the
other basis kinds through :func:`record_attestation`. The release layer asks
:func:`standing_attestations` which statement currently stands for each
submission and refuses to ship anything it cannot answer for.

Rows are append-only (database trigger); a correction is a new row whose
``supersedes_attestation_id`` names the one it replaces. "Standing" therefore
means *head of the chain*: the latest row nothing supersedes.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.rights import DepositRights

from app.db.models.app_user import AppUser, AppUserRole
from app.db.models.common import RightsBasisKind
from app.db.models.submission import Submission
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.services.submission import resolve_actor_kind

_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})


class RightsAttestationError(ValueError):
    """The attestation request is malformed -- 422 on the wire."""


class RightsAttestationForbidden(RightsAttestationError):
    """The actor may not make this attestation -- 403 on the wire.

    A class rather than a status literal so the raise site says who may do
    what and the route only translates.
    """


def _license_key(license_id: str) -> str:
    """The comparison form of a license identifier: trimmed, case-folded."""
    return license_id.strip().casefold()


def licenses_match(attested: str, released: str) -> bool:
    """Exact, case-insensitive identifier match -- the whole of v1 compatibility.

    No lattice. ``CC0-1.0`` does not satisfy a ``CC-BY-4.0`` release even
    though it is more permissive, because deciding that is a legal judgement
    this code has no business encoding. A curator who knows better records a
    new attestation naming the release's license.
    """
    return _license_key(attested) == _license_key(released)


def record_attestation(
    session: Session,
    *,
    submission: Submission,
    license_id: str,
    basis: RightsBasisKind,
    actor: AppUser,
    note: str | None = None,
    source_terms: str | None = None,
) -> SubmissionRightsAttestation:
    """Append one attestation for ``submission``, superseding the standing one.

    :raises RightsAttestationForbidden: a ``depositor_agreement`` by anyone
        but the submission's creator, or any other basis by an actor who is
        neither curator nor admin.
    :raises RightsAttestationError: a blank license, or ``basis=source_terms``
        without ``source_terms``.
    """
    license_id = license_id.strip()
    if not license_id:
        raise RightsAttestationError(
            "rights_license_blank: a rights attestation must name a license."
        )
    source_terms = source_terms.strip() if source_terms else None
    if basis is RightsBasisKind.source_terms and not source_terms:
        raise RightsAttestationError(
            "rights_source_terms_required: a source_terms attestation must "
            "quote or cite the terms the records were taken under."
        )

    if basis is RightsBasisKind.depositor_agreement:
        if actor.id != submission.created_by:
            raise RightsAttestationForbidden(
                "rights_attestation_not_depositor: only the account that made "
                "the deposit can attest a depositor agreement for it."
            )
    elif actor.role not in _CURATION_ROLES:
        raise RightsAttestationForbidden(
            "rights_attestation_requires_curator: a "
            f"{basis.value} attestation records a curator's judgement and needs "
            "the curator or admin role."
        )

    standing = standing_attestation(session, submission_id=submission.id)
    row = SubmissionRightsAttestation(
        submission_id=submission.id,
        license_id=license_id,
        basis=basis,
        attested_by=actor.id,
        actor_kind=resolve_actor_kind(actor),
        source_terms=source_terms,
        note=note,
        supersedes_attestation_id=standing.id if standing is not None else None,
    )
    session.add(row)
    session.flush()
    return row


def attest_from_deposit(
    session: Session,
    *,
    submission: Submission,
    rights: DepositRights | None,
    actor: AppUser,
) -> SubmissionRightsAttestation | None:
    """Record the upload's ``rights`` fragment as a depositor agreement.

    ``None`` when the upload carried no fragment: absence is not refused at
    upload time (v1 keeps existing clients working), it is refused at release
    time. The actor must be the submission's creator -- a depositor agreement
    is the depositor's statement and nobody else's.
    """
    if rights is None:
        return None
    return record_attestation(
        session,
        submission=submission,
        license_id=rights.license,
        basis=RightsBasisKind.depositor_agreement,
        actor=actor,
        source_terms=rights.source_terms,
    )


def list_attestations(
    session: Session, *, submission_id: int
) -> list[SubmissionRightsAttestation]:
    """Every attestation ever made for a submission, oldest first."""
    return list(
        session.scalars(
            select(SubmissionRightsAttestation)
            .where(SubmissionRightsAttestation.submission_id == submission_id)
            .order_by(SubmissionRightsAttestation.id)
        )
    )


def standing_attestation(
    session: Session, *, submission_id: int
) -> SubmissionRightsAttestation | None:
    """The attestation that currently stands for one submission, if any."""
    return standing_attestations(session, submission_ids=[submission_id]).get(
        submission_id
    )


def standing_attestations(
    session: Session, *, submission_ids: Iterable[int]
) -> dict[int, SubmissionRightsAttestation]:
    """Head of the attestation chain per submission, for those that have one.

    Computed from the append-only chain rather than read off a flag: a row
    stands when no other row names it in ``supersedes_attestation_id``.
    """
    ids = sorted(set(submission_ids))
    if not ids:
        return {}
    rows = list(
        session.scalars(
            select(SubmissionRightsAttestation)
            .where(SubmissionRightsAttestation.submission_id.in_(ids))
            .order_by(SubmissionRightsAttestation.id)
        )
    )
    superseded = {
        row.supersedes_attestation_id
        for row in rows
        if row.supersedes_attestation_id is not None
    }
    standing: dict[int, SubmissionRightsAttestation] = {}
    for row in rows:
        if row.id in superseded:
            continue
        # Later rows win only if two heads exist, which the UNIQUE on
        # ``supersedes_attestation_id`` plus this ordering makes impossible
        # for a correctly appended chain; ordering by id keeps it stable
        # regardless.
        standing[row.submission_id] = row
    return standing


__all__ = [
    "RightsAttestationError",
    "RightsAttestationForbidden",
    "attest_from_deposit",
    "licenses_match",
    "list_attestations",
    "record_attestation",
    "standing_attestation",
    "standing_attestations",
]
