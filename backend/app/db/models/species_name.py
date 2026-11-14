"""Names of species, for finding a molecule by its common name.

Names are TCKDB's own words, written by a curator or loaded from a reviewed offline file; a depositor's text never
lands here. A name is retired, never deleted. ``name_key`` is the normalised form (see
``app.services.species_name_normalisation``) that searches compare against.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind


class SpeciesName(Base, TimestampMixin, PublicRefMixin):
    """One name of one species. The same name may belong to several species."""

    __tablename__ = "species_name"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    species_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("species.id", deferrable=True, initially="IMMEDIATE"), nullable=False
    )
    #: The name as displayed.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: The normalised name every search compares against. Code-point ("C") collation, so equality and prefix ranges
    #: are byte-wise and the index on it serves both.
    name_key: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    kind: Mapped[SpeciesNameKind] = mapped_column(SAEnum(SpeciesNameKind, name="species_name_kind"), nullable=False)
    source_kind: Mapped[SpeciesNameSourceKind] = mapped_column(
        SAEnum(SpeciesNameSourceKind, name="species_name_source_kind"), nullable=False
    )
    #: The external source an imported name came from.
    external_source_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("external_source.id", deferrable=True, initially="IMMEDIATE"), nullable=True
    )
    #: The record's id at the external source (a PubChem CID, as text).
    external_record_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: When the external record was fetched.
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"), nullable=True
    )
    #: Set when the name is retired; a retired name is not searched.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    #: The curator or admin who retired the name; null when the name is live or when no retirer is recorded.
    retired_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 128", name="name_length"),
        CheckConstraint("char_length(name_key) BETWEEN 1 AND 128", name="name_key_length"),
        CheckConstraint(
            "source_kind <> 'offline_import' OR (external_source_id IS NOT NULL AND external_record_key IS NOT NULL "
            "AND retrieved_at IS NOT NULL)",
            name="offline_import_has_source",
        ),
        CheckConstraint(
            "source_kind <> 'curated' OR (external_record_key IS NULL AND retrieved_at IS NULL)",
            name="curated_has_no_record",
        ),
        CheckConstraint("retired_by_user_id IS NULL OR retired_at IS NOT NULL", name="retired_by_needs_retired_at"),
        Index(
            "uq_species_name_species_id_name_key_live",
            "species_id",
            "name_key",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        Index("ix_species_name_name_key", "name_key"),
        Index("ix_species_name_species_id", "species_id"),
    )
