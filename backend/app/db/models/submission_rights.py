"""Who agreed to license a deposit, and on what basis.

``submission_rights_attestation`` is the one table that answers the question
``LICENSE-DATA`` says a release must be able to answer: *did anybody with the
right to do so agree that these records may be published under this license?*
A per-release ``data_license`` string cannot answer it — that is the terms the
operator applies, and an operator can license their own deposits and nobody
else's.

Where it sits in the four-way split
-----------------------------------
This is a **curation** row about a **provenance** row. It is keyed to the
submission (the contribution event) and never to a thermo, kinetics or any
other scientific record: a deposit is licensed as a whole, by the person who
made it or by a curator standing in for one, and hanging rights off a
scientific row would invite the same value to carry two different answers
through two different deposits.

Append-only
-----------
A correction is an insert. ``supersedes_attestation_id`` points at the row
being replaced, is ``UNIQUE`` so the chain stays linear, and the database
refuses ``UPDATE``/``DELETE``/``TRUNCATE`` outright (same pattern as
``release_manifest``). The *standing* attestation for a submission is the
head of the chain — the latest row nothing supersedes.

No audit-event enum value is added for this: the row *is* the audit record.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin
from app.db.models.common import RightsBasisKind, SubmissionActorKind

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser
    from app.db.models.submission import Submission


class SubmissionRightsAttestation(Base, PublicRefMixin):
    """One append-only statement that a submission may be licensed as stated.

    ``license_id`` is an SPDX identifier (``CC-BY-4.0``, ``CC0-1.0``) and is
    compared with a release's ``data_license`` by exact, case-insensitive
    match — no compatibility lattice, deliberately.
    """

    __tablename__ = "submission_rights_attestation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    submission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("submission.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    license_id: Mapped[str] = mapped_column(String(64), nullable=False)
    basis: Mapped[RightsBasisKind] = mapped_column(
        SAEnum(RightsBasisKind, name="rights_basis_kind"),
        nullable=False,
    )

    # Who stood behind the statement. A ``depositor_agreement`` row always
    # carries the submission's creator; the service layer enforces that.
    attested_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    actor_kind: Mapped[SubmissionActorKind] = mapped_column(
        SAEnum(SubmissionActorKind, name="submission_actor_kind"),
        nullable=False,
    )
    attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    # Required exactly when ``basis`` is ``source_terms``: a claim that the
    # source permits republication has to say which terms.
    source_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    supersedes_attestation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        # Explicit name: the convention's
        # ``fk_<table>_<column>_<referred>`` spelling is 87 characters here
        # and PostgreSQL would silently truncate it at 63.
        ForeignKey(
            "submission_rights_attestation.id",
            name="fk_submission_rights_attestation_supersedes",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    )

    submission: Mapped["Submission"] = relationship(foreign_keys=[submission_id])
    attester: Mapped["AppUser"] = relationship(foreign_keys=[attested_by])
    supersedes: Mapped[Optional["SubmissionRightsAttestation"]] = relationship(
        remote_side=[id], foreign_keys=[supersedes_attestation_id]
    )

    __table_args__ = (
        Index("ix_submission_rights_attestation_submission_id", "submission_id"),
        # Linear chain: a row can be superseded at most once.
        UniqueConstraint(
            "supersedes_attestation_id",
            name="uq_submission_rights_attestation_supersedes_attestation_id",
        ),
        CheckConstraint("length(btrim(license_id)) > 0", name="license_id_nonblank"),
        CheckConstraint(
            "(basis <> 'source_terms') OR "
            "(source_terms IS NOT NULL AND length(btrim(source_terms)) > 0)",
            name="source_terms_required",
        ),
    )


__all__ = ["SubmissionRightsAttestation"]
