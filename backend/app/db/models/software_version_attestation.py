"""Owner attestations of a software version, and the calculations they moved.

Issue #305, owner decision (a) (2026-09-29). A calculation can cite a
``software_release`` whose ``version`` is NULL -- the program was recorded,
its version was not -- and have no stored artifact whose banner could say
which version ran. The only evidence left is the person who ran it saying
so. These two tables record that statement and exactly what it was used for.

* ``software_version_attestation`` -- one statement: who attested, whose
  deposits it covers, when, which (version-less) ``software_release`` it
  concerns, the version they attested, their words verbatim, and the
  evidence kind.
* ``software_version_attestation_calculation`` -- one row per calculation
  re-pointed under that statement, with its ``software_release`` before and
  after.

Both are **append-only** (``tckdb_reject_mutation`` / ``tckdb_reject_truncate``
triggers, the same functions guarding ``record_review_event``). An
attestation is a historical fact; a wrong one is answered by a new
attestation and a new re-point, never by editing the old row. Inserts are
validated by ``trg_sva_validate`` / ``trg_sva_calculation_validate`` against
facts that never change afterwards, so an archive restore replays them
cleanly (see revision ``86ffcd9d3c65``).

Written only by ``scripts/ops/fill_software_release_version.py``
``--attest-version`` (``app/services/software_release_version_fill.py``).
The re-point follows the same rule as a banner fill: the version-less
release is never modified, a new versioned release is resolved and the
calculation cites it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.common import SoftwareVersionEvidenceKind


class SoftwareVersionAttestation(Base):
    """One person's statement of the version of a program they ran."""

    __tablename__ = "software_version_attestation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: The version-less release the statement is about.
    software_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", name="fk_sva_software_release"),
        nullable=False,
    )
    #: The version exactly as attested ("6", not an inferred "6.1.0").
    attested_version: Mapped[str] = mapped_column(Text, nullable=False)
    #: The attesting person's words, verbatim.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_kind: Mapped[SoftwareVersionEvidenceKind] = mapped_column(
        SAEnum(SoftwareVersionEvidenceKind, name="software_version_evidence_kind"),
        nullable=False,
    )
    attested_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", name="fk_sva_attested_by"),
        nullable=False,
    )
    #: Whose deposits the statement covers. Usually the attester; an admin
    #: may attest for another account (e.g. deposits made under a shared
    #: service account). Only calculations ``created_by`` this user are moved.
    covers_depositor: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", name="fk_sva_covers_depositor"),
        nullable=False,
    )
    #: When the person made the statement (may precede ``created_at``).
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    #: When it was recorded here.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    calculations: Mapped[list["SoftwareVersionAttestationCalculation"]] = relationship(
        back_populates="attestation",
        order_by="SoftwareVersionAttestationCalculation.id",
    )

    __table_args__ = (
        CheckConstraint("length(btrim(attested_version)) > 0", name="version_nonblank"),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_nonblank"),
        Index("ix_software_version_attestation_software_release_id", "software_release_id"),
    )


class SoftwareVersionAttestationCalculation(Base):
    """One calculation re-pointed under an attestation, before and after."""

    __tablename__ = "software_version_attestation_calculation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attestation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software_version_attestation.id", name="fk_sva_calculation_attestation"),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", name="fk_sva_calculation_calculation"),
        nullable=False,
    )
    before_software_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", name="fk_sva_calculation_before_release"),
        nullable=False,
    )
    after_software_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", name="fk_sva_calculation_after_release"),
        nullable=False,
    )

    attestation: Mapped[SoftwareVersionAttestation] = relationship(back_populates="calculations")

    __table_args__ = (
        UniqueConstraint(
            "attestation_id", "calculation_id", name="uq_sva_calculation_attestation_calculation"
        ),
        CheckConstraint(
            "before_software_release_id <> after_software_release_id", name="release_changed"
        ),
        Index("ix_software_version_attestation_calculation_calculation_id", "calculation_id"),
    )
