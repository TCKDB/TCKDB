"""Resolve downloadable calculation artifacts: approved, or the caller's own.

Two ways a stored object becomes retrievable, and they answer different
questions. *Approved* means a curator has published this evidence, so any
authenticated caller may pull it. *Owned* means the caller deposited it,
so refusing them their own file protects nobody.

The authentication gate is unconditional, because raw logs carry scratch
paths, usernames and scheduler ids that cannot be scrubbed at rest without
breaking content-addressing: nothing here becomes anonymous.
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

    Why review status does not gate this
    ------------------------------------
    Unredacted ESS logs carry producer-side scratch paths, usernames and
    cluster hostnames, so they must not be served anonymously. That
    supports **authentication** as the gate and says nothing about
    curation. Review status answers a different question -- "should you
    trust this science" -- and is already surfaced as trust and review
    badges on every record. Using it to gate *access to the evidence*
    would conflate trustworthiness with confidentiality, and an archive
    whose files only their uploader can open is not doing the job an
    archive exists for.

    What holds: anonymous callers still get 401 (the route's
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
