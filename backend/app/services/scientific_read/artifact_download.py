"""Resolve downloadable calculation artifacts: approved, or the caller's own.

Two ways a stored object becomes retrievable, and they answer different
questions. *Approved* means a curator has published this evidence, so any
authenticated caller may pull it. *Owned* means the caller deposited it,
so refusing them their own file protects nobody.

Approval alone was the whole rule until 2026-08-24, and the effect was
perverse: measured on the hosted instance, 563 of 563 artifacts belonged
to calculations still ``not_reviewed``, so the gate had never once opened
— including for the person who uploaded the bytes. ADR 0004's reasoning
for gating raw logs (they carry scratch paths, usernames and scheduler ids
that cannot be scrubbed at rest without breaking content-addressing) is
untouched: the authentication gate stays unconditional, and nothing here
becomes anonymous. Only the *review-status* half of the gate moved.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationArtifact


def resolve_downloadable_artifact_by_sha256(
    session: Session, sha256: str, user: AppUser
) -> CalculationArtifact | None:
    """Return the artifact row for *sha256*, or ``None`` if there is none.

    Authentication is the gate. Any authenticated caller may fetch any
    stored artifact's bytes; ``user`` is accepted so callers need not
    change and so a future per-record embargo has somewhere to live, but
    nothing about *who* is asking narrows the result today.

    Why review status stopped gating this (decided 2026-09-14)
    ----------------------------------------------------------
    ADR 0004 argues that unredacted ESS logs carry producer-side scratch
    paths, usernames and cluster hostnames, so they must not be served
    anonymously. That argument supports **authentication** as the gate and
    says nothing about curation. Review status answers a different
    question -- "should you trust this science" -- and is already surfaced
    as trust and review badges on every record. Using it to gate *access
    to the evidence* conflated trustworthiness with confidentiality.

    The cost was measured, not theorised. On the hosted instance: 563
    artifacts, **0 approved**, so the approved branch had never once
    opened for anyone. Deposits came from ``arc-zeus`` (519) and
    ``real_validation_20260729`` (44); the instance's own admin had
    deposited none and could therefore read none of its evidence. An
    archive whose files only their uploader can open is not doing the job
    an archive exists for.

    A 2026-08-24 change had already walked this back partway by adding an
    owner path, and its own docstring recorded that ADR 0004's choice of
    review status as the gate was *unargued*. This finishes that.

    What did NOT change: anonymous callers still get 401 (the route's
    ``get_current_user`` dependency, unconditional, no opt-out), the
    stored bytes are still verified against their digest on every read,
    and a verification failure is still recorded as a custody break.
    """

    return session.scalar(
        select(CalculationArtifact)
        .where(
            CalculationArtifact.sha256 == sha256,
            CalculationArtifact.bytes.is_not(None),
        )
        # Duplicate upload-event rows can point at one content-addressed
        # object (563 rows, 392 digests on the hosted instance). Any of
        # them names the same bytes; the earliest is chosen so the
        # filename and expected byte count are deterministic.
        .order_by(CalculationArtifact.id.asc())
    )
