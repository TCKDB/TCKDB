"""Source-custody models: ``external_source`` and ``external_source_record``.

Implements CCCBDB importer spec Gap 4 (``backend/docs/specs/cccbdb_importer.md``
§7) for **new rows only** -- existing CCCBDB observations keep their
flattened ``external_source_*`` columns on ``molecular_property_observation``
untouched (see that model's module docstring). These two tables are the
custody chain a new importer (ThermoML, Phase C-E2/E3) attaches to instead:

* ``external_source`` identifies the source *database/collection* (e.g. "NIST
  ThermoML Archive", release "2024-06") a document was fetched from.
* ``external_source_record`` is one immutable snapshot of one document
  fetched from that source: retrieval metadata, a content digest, where the
  raw bytes live, and the parser/mapping versions that turned it into typed
  rows. A row here is provenance -- append-only, never edited in place.
  Re-parsing with a new ``parser_version``/``mapping_version`` inserts a new
  row rather than overwriting the old one (see the table's unique
  constraint), so a re-import always has a byte-identical or explicitly
  superseding custody trail.

Both tables are included in the ``tckdb.archive.v1`` registry
(``app/services/archive/registry.py``); their columns use only generic
codec-supported SQL types, so no archive core.py change is needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.elements import conv

from app.db.base import Base, TimestampMixin
from app.db.models.common import ExternalSourceRecordKind

if TYPE_CHECKING:
    from app.db.models.molecular_property_observation import (
        MolecularPropertyObservation,
    )


class ExternalSource(Base, TimestampMixin):
    """One external database/collection a custody record can cite.

    E.g. ("NIST ThermoML Archive", "2024-06") or ("CCCBDB", "Release 22, May
    2022"). Deliberately separate from :class:`~app.db.models.literature.
    Literature`: a literature row is one citable article; a source here is
    the *database* an importer fetched documents from, which may itself
    aggregate many articles (ThermoML) or be a purpose-built compilation
    (CCCBDB) with no single DOI.
    """

    __tablename__ = "external_source"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_release: Mapped[str] = mapped_column(Text, nullable=False)
    source_database_doi: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    citation_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    records: Mapped[list["ExternalSourceRecord"]] = relationship(
        back_populates="external_source"
    )

    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_release",
            name="uq_external_source_name_release",
        ),
    )


class ExternalSourceRecord(Base, TimestampMixin):
    """One immutable snapshot of one document fetched from an
    :class:`ExternalSource`, with the parser/mapping versions that
    produced typed rows from it.

    Append-only by convention (like other provenance rows): a changed
    parser or mapping version inserts a new row rather than mutating this
    one, so every ``molecular_property_observation.external_source_record_id``
    keeps pointing at the exact custody row its values were derived under.
    """

    __tablename__ = "external_source_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "external_source.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_external_source_record_external_source_id",
        ),
        nullable=False,
    )
    record_kind: Mapped[ExternalSourceRecordKind] = mapped_column(
        SAEnum(ExternalSourceRecordKind, name="external_source_record_kind"),
        nullable=False,
    )
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Object-store key for the raw snapshot bytes. Not a FK -- the artifact
    # store is content-addressed (see ``content_addressed_key`` in
    # ``app/services/artifact_storage.py``), and a custody row may exist
    # (retrieval metadata recorded) before or independent of the raw bytes
    # being uploaded to that store.
    raw_uri: Mapped[str] = mapped_column(Text, nullable=False)
    container_digest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_version: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_report_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    external_source: Mapped["ExternalSource"] = relationship(back_populates="records")
    observations: Mapped[list["MolecularPropertyObservation"]] = relationship(
        back_populates="external_source_record"
    )

    __table_args__ = (
        # ``conv(...)`` marks each name as already final: a bare string here
        # would be treated as input to ``NAMING_CONVENTION["ck"]`` and get
        # ``ck_external_source_record_`` glued on a second time (see the
        # longer explanation on ``MolecularPropertyObservation``). Matches
        # what migration ``0b4a3afabfd3`` creates via ``op.f(...)``.
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=conv("ck_external_source_record_content_sha256_hex"),
        ),
        CheckConstraint(
            "content_length >= 0",
            name=conv("ck_external_source_record_content_length_ge_0"),
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=conv("ck_external_source_record_http_status_range"),
        ),
        # A changed parser or mapping version appends a new custody row
        # rather than overwriting the old one -- see class docstring.
        UniqueConstraint(
            "external_source_id",
            "source_record_key",
            "content_sha256",
            "parser_version",
            "mapping_version",
            name="uq_external_source_record_identity",
        ),
    )
