"""Curated product selection and citable dataset releases (Stage 3).

This module adds the *curation overlay* that lets TCKDB answer "what is **the**
TCKDB value?" without ever touching the science it points at.

Five tables, each in exactly one of the four buckets the schema is built on
(see ``docs/guides/system_flow.md`` §1):

===========================  ==========  ==================================
Table                        Bucket      Why
===========================  ==========  ==================================
``curation_policy``          Identity    A named, versioned expert policy.
                                         Deduped on ``(name, version)`` so
                                         many selections cite one policy row.
``dataset_release``          Curation    The citable unit. Carries license,
                                         citation and contact metadata.
``release_selection``        Curation    One attributed, **append-only**
                                         decision: policy + curator +
                                         candidate + rationale + release.
``release_manifest``         Result      The frozen, checksummed description
                                         of a published release. Immutable.
``release_artifact``         Result      One checksummed file inside a
                                         manifest. Immutable.
===========================  ==========  ==================================

Three invariants this module exists to protect:

1. **A selection never mutates the record it selects.** ``release_selection``
   holds ``(record_type, record_id)`` the same way ``record_review`` and
   ``scientific_record_supersession`` do — a loose, typed pointer, not an FK
   into the science, and certainly not an ``is_best`` column on ``thermo``.
   Nothing in this module writes to a product table.

2. **Superseding appends; it never edits.** A curator who changes their mind
   inserts a new row with ``action='supersede'`` and
   ``supersedes_selection_id`` pointing at the row it replaces. A DB trigger
   (see the Stage 3 migration) rejects ``UPDATE``/``DELETE`` on
   ``release_selection`` so this holds independently of application code —
   the same belt-and-braces approach used by
   ``record_reproducibility_assessment``.

3. **A published release is frozen and stays downloadable forever.**
   ``release_manifest`` stores the manifest document (``document_json``), a
   SHA-256 over its canonical serialization, and a snapshot of every claim it
   makes about the release; ``release_artifact`` stores each file's bytes and
   its SHA-256. Publication is the only write. Later activity in the corpus —
   new uploads, review progressing, a DOI being attached — cannot change what
   a citation resolves to.

   Whether the *live* database still matches the frozen release is a separate,
   genuinely useful question, reported non-fatally by
   ``app.services.release.manifest.live_divergence``. It never blocks a
   download: normal growth is the overwhelmingly common cause, and refusing
   the citation for it would be both wrong and undiagnosable.

This layer is deliberately **not** the disaster-recovery archive
(``tckdb.archive.v1``) and **not** a convenience projection
(``tckdb.export.v0`` NDJSON / CHEMKIN / ML). See
``backend/docs/specs/dataset_release_and_profiles.md`` §"Three formats, three
contracts".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    DatasetReleaseStatus,
    ReadProfile,
    ReleaseArtifactKind,
    ReleaseSelectionAction,
    SubmissionRecordType,
)

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser


# Record types a release selection may name. Deliberately narrower than
# ``SubmissionRecordType``: a release recommends *scientific product values*
# and the entries that carry them, not submissions, artifacts, or raw
# identity rows. Kept as a module constant so the ORM check constraint and
# the service-layer validation cannot drift apart.
SELECTABLE_RECORD_TYPES: frozenset[SubmissionRecordType] = frozenset(
    {
        SubmissionRecordType.thermo,
        SubmissionRecordType.statmech,
        SubmissionRecordType.transport,
        SubmissionRecordType.kinetics,
        SubmissionRecordType.network_solve,
        SubmissionRecordType.transition_state_entry,
    }
)

_SELECTABLE_SQL_LIST = ", ".join(
    f"'{t.value}'" for t in sorted(SELECTABLE_RECORD_TYPES, key=lambda t: t.value)
)


class CurationPolicy(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """A named, versioned expert policy for choosing among candidate records.

    This is the persisted counterpart to the *read-time* ``SelectionPolicy``
    enum in ``app/schemas/reads/scientific_common.py``. The read-time enum
    ranks candidates deterministically from record data alone and persists
    nothing; a ``CurationPolicy`` is a human-authored rubric that a named
    curator applies and that a release cites forever.

    Identity table: deduped on ``(name, version)``. Two releases a year apart
    may cite the same policy row, and a policy revision is a *new row*, never
    an edit — otherwise a published release's stated policy could silently
    change after the fact.
    """

    __tablename__ = "curation_policy"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Machine-readable statement of the rubric (thresholds, required
    # evidence, tie-breaks). Free-form on purpose: rubrics differ by product
    # family and encoding them as columns would freeze the vocabulary before
    # there is a second real policy to generalize from.
    criteria_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    author: Mapped[Optional["AppUser"]] = relationship(foreign_keys="CurationPolicy.created_by")

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_curation_policy_name"),
        CheckConstraint("length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("length(btrim(version)) > 0", name="version_nonblank"),
        CheckConstraint("length(btrim(description)) > 0", name="description_nonblank"),
    )


class DatasetRelease(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """A citable dataset release.

    The unit a paper cites. Everything needed to make the citation meaningful
    lives here or on its :class:`ReleaseManifest`: what policy governed it,
    who published it, under which licenses, how to contact the maintainers,
    and — once minted — which DOI.

    ``doi`` is nullable and stays ``NULL`` until a deposit is actually made.
    A DOI is not retractable, so the release machinery deliberately does not
    mint one; see ``backend/docs/deployment/cutting_a_dataset_release.md``.
    """

    __tablename__ = "dataset_release"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Human-facing citable tag, e.g. ``2026.07.0``. Unique and immutable.
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[DatasetReleaseStatus] = mapped_column(
        SAEnum(DatasetReleaseStatus, name="dataset_release_status"),
        nullable=False,
        default=DatasetReleaseStatus.draft,
        server_default=DatasetReleaseStatus.draft.value,
    )

    curation_policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("curation_policy.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    # Licensing is split because the two halves genuinely differ: TCKDB code
    # is MIT, while a curated scientific corpus is normally published under a
    # data license (CC-BY-4.0). Conflating them is how attribution gets lost.
    data_license: Mapped[str] = mapped_column(String(64), nullable=False)
    code_license: Mapped[str] = mapped_column(String(64), nullable=False)

    citation_text: Mapped[str] = mapped_column(Text, nullable=False)
    doi: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contact: Mapped[str] = mapped_column(Text, nullable=False)
    changelog_entry: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    withdrawn_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    curation_policy: Mapped["CurationPolicy"] = relationship(
        foreign_keys=[curation_policy_id]
    )
    publisher: Mapped[Optional["AppUser"]] = relationship(
        foreign_keys="DatasetRelease.created_by"
    )

    __table_args__ = (
        UniqueConstraint("tag", name="uq_dataset_release_tag"),
        CheckConstraint("length(btrim(tag)) > 0", name="tag_nonblank"),
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(citation_text)) > 0", name="citation_nonblank"),
        CheckConstraint("length(btrim(contact)) > 0", name="contact_nonblank"),
        CheckConstraint("length(btrim(data_license)) > 0", name="data_license_nonblank"),
        CheckConstraint("length(btrim(code_license)) > 0", name="code_license_nonblank"),
        # A published or withdrawn release must record when it happened;
        # a draft must not claim it has.
        CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status IN ('published', 'withdrawn') AND published_at IS NOT NULL)",
            name="published_at_matches_status",
        ),
        CheckConstraint(
            "(status <> 'withdrawn') "
            "OR (withdrawn_at IS NOT NULL AND length(btrim(coalesce(withdrawn_reason, ''))) > 0)",
            name="withdrawn_has_reason",
        ),
        Index("ix_dataset_release_status", "status"),
    )


class ReleaseSelection(Base, PublicRefMixin):
    """One attributed, append-only selection of a candidate record.

    Answers, for one release: *which* candidate, chosen by *whom*, under
    *which policy version*, and *why*.

    Deliberately **not** ``TimestampMixin`` + ``updated_at``: this row is
    written once. ``created_at`` is declared inline for symmetry with
    :class:`app.db.models.scientific_record_supersession.ScientificRecordSupersession`,
    which is the closest existing analogue.

    The append-only contract is enforced three ways:

    * no application code path issues ``UPDATE``/``DELETE``;
    * a database trigger rejects both (Stage 3 migration);
    * ``supersedes_selection_id`` is ``UNIQUE``, so a selection can be
      replaced at most once and the supersession chain stays linear and
      auditable, exactly as ``scientific_record_supersession`` does.
    """

    __tablename__ = "release_selection"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    dataset_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    curation_policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("curation_policy.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    # Loose typed pointer at the selected candidate — the same shape used by
    # ``record_review`` and ``scientific_record_supersession``. A real FK
    # would require one nullable column per product table and would invite
    # someone to hang selection state off the science itself.
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # The scientific subject the selection is *about* — e.g. the
    # ``species_entry`` whose thermo was chosen, or the ``reaction_entry``
    # whose kinetics was chosen. Without this a consumer cannot ask "what did
    # TCKDB pick for ethanol?" without already knowing the candidate id.
    subject_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    action: Mapped[ReleaseSelectionAction] = mapped_column(
        SAEnum(ReleaseSelectionAction, name="release_selection_action"),
        nullable=False,
        default=ReleaseSelectionAction.select,
        server_default=ReleaseSelectionAction.select.value,
    )
    supersedes_selection_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("release_selection.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    selected_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=text("now()"), nullable=False
    )

    dataset_release: Mapped["DatasetRelease"] = relationship(
        foreign_keys=[dataset_release_id]
    )
    curation_policy: Mapped["CurationPolicy"] = relationship(
        foreign_keys=[curation_policy_id]
    )
    curator: Mapped["AppUser"] = relationship(foreign_keys=[selected_by])
    supersedes: Mapped[Optional["ReleaseSelection"]] = relationship(
        remote_side=[id], foreign_keys=[supersedes_selection_id]
    )

    __table_args__ = (
        CheckConstraint(
            f"record_type IN ({_SELECTABLE_SQL_LIST})",
            name="selectable_record_type",
        ),
        CheckConstraint("length(btrim(rationale)) > 0", name="rationale_nonblank"),
        # ``select`` opens a chain; ``supersede``/``withdraw`` must name the
        # row they replace. This is what makes "superseding appends rather
        # than edits" checkable rather than merely intended.
        CheckConstraint(
            "(action = 'select' AND supersedes_selection_id IS NULL) "
            "OR (action IN ('supersede', 'withdraw') AND supersedes_selection_id IS NOT NULL)",
            name="supersession_matches_action",
        ),
        CheckConstraint(
            "supersedes_selection_id IS NULL OR supersedes_selection_id <> id",
            name="no_self_supersession",
        ),
        # A withdrawal makes no recommendation, so it must repeat the record
        # it retires rather than naming a different one — enforced in the
        # service layer, which can read the superseded row.
        UniqueConstraint(
            "supersedes_selection_id",
            name="uq_release_selection_supersedes_selection_id",
        ),
        Index(
            "ix_release_selection_release_subject",
            "dataset_release_id",
            "subject_type",
            "subject_id",
        ),
        Index(
            "ix_release_selection_record",
            "record_type",
            "record_id",
        ),
    )


class ReleaseManifest(Base, CreatedByMixin, PublicRefMixin):
    """The frozen, checksummed description of one published dataset release.

    Binds the release to everything needed to reproduce it: the Alembic
    revision the data was read under, the backend and wire-schema package
    versions, the curation policy version, the review-policy version, the
    licenses, and the resolved read profile (always ``curated`` — recorded
    explicitly rather than assumed, because the whole point of the profile
    contract is that it is never implicit).

    **The document is frozen, not re-derived.** ``document_json`` holds the
    exact manifest document produced at publication and ``content_sha256`` is
    the SHA-256 over its canonical serialization. Both the document and the
    scalar snapshot columns below are written once, from the release as it
    stood at that instant.

    An earlier design rendered the document from the *live* release row on
    every read. That was wrong in a way that only shows up in the steady
    state: recording the DOI — the last step of publishing — mutates
    ``dataset_release.doi``, which changed the rendered document and broke the
    digest permanently, so `verified: true` was reachable only for releases
    nobody had actually deposited. Withdrawing did the same via ``status``.
    A citable document must not move when the row it describes is later
    annotated.

    Whether the *live* database still agrees with the frozen release is a
    real and separate question, answered by the non-fatal divergence report in
    ``app/services/release/manifest.py`` — never by refusing to serve the
    citation.
    """

    __tablename__ = "release_manifest"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    dataset_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dataset_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    manifest_schema: Mapped[str] = mapped_column(String(64), nullable=False)

    # Always ``curated`` today. Stored rather than assumed so a manifest is
    # self-describing when read outside this codebase.
    profile: Mapped[ReadProfile] = mapped_column(
        SAEnum(ReadProfile, name="read_profile"),
        nullable=False,
        default=ReadProfile.curated,
        server_default=ReadProfile.curated.value,
    )

    # --- version binding ---------------------------------------------------
    alembic_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    backend_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schemas_package_version: Mapped[str] = mapped_column(String(64), nullable=False)
    review_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    curation_policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("curation_policy.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    # Recorded so a reader can see *which* recovery-archive contract was
    # current when the release was cut, and see that it is a different thing.
    recovery_archive_schema: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- release-claim snapshot -------------------------------------------
    # Every field the frozen document asserts about the release is copied
    # here at publication and read back from here afterwards. These columns
    # are what "snapshotted rather than read through the FK" actually means;
    # reading any of them from ``self.dataset_release`` instead is the bug
    # this block exists to prevent.
    # ``release_public_ref`` and ``release_published_at`` are immutable in
    # practice, but they are snapshotted anyway so that rebuilding the frozen
    # document touches **only** this row. "Immutable in practice" is exactly
    # what was assumed about the release row before a DOI was attached to it.
    release_public_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    release_published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    release_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    release_title: Mapped[str] = mapped_column(Text, nullable=False)
    release_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    release_contact: Mapped[str] = mapped_column(Text, nullable=False)
    release_changelog_entry: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    # Status and DOI *as at publication*. The live release may later be
    # withdrawn or have a DOI attached; those are annotations on the release,
    # reported in the release response, and must never move the frozen
    # document.
    release_status_at_publication: Mapped[DatasetReleaseStatus] = mapped_column(
        SAEnum(DatasetReleaseStatus, name="dataset_release_status"),
        nullable=False,
    )
    release_doi_at_publication: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    data_license: Mapped[str] = mapped_column(String(64), nullable=False)
    code_license: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_text: Mapped[str] = mapped_column(Text, nullable=False)

    # --- curation-policy snapshot -----------------------------------------
    # Same reasoning: a policy row is immutable by service-layer rule, but the
    # manifest should not depend on that rule holding forever.
    curation_policy_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    curation_policy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    curation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    curation_policy_description: Mapped[str] = mapped_column(Text, nullable=False)
    curation_policy_criteria_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    # The ``contract`` block as rendered at publication. Stored rather than
    # rebuilt from module constants: the block is a literal (an ``omits`` list,
    # contract tags), and rebuilding it meant any future maintainer edit to
    # that literal made every past release report unverified — the same failure
    # mode as re-rendering the document from the live release row.
    contract_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # --- integrity ---------------------------------------------------------
    # The frozen document, served verbatim to anyone resolving the citation.
    # ``content_sha256`` is its canonical-serialization digest.
    document_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    candidate_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=text("now()"), nullable=False
    )

    dataset_release: Mapped["DatasetRelease"] = relationship(
        foreign_keys=[dataset_release_id]
    )
    curation_policy: Mapped["CurationPolicy"] = relationship(
        foreign_keys=[curation_policy_id]
    )
    artifacts: Mapped[list["ReleaseArtifact"]] = relationship(
        back_populates="release_manifest",
        order_by="ReleaseArtifact.path",
        cascade="save-update, merge",
    )

    __table_args__ = (
        # One frozen manifest per release. Regenerating would make "which
        # manifest does this citation mean?" ambiguous, which is the exact
        # failure mode an immutable manifest exists to prevent.
        UniqueConstraint(
            "dataset_release_id", name="uq_release_manifest_dataset_release_id"
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256_hex"
        ),
        CheckConstraint("selected_record_count >= 0", name="selected_count_nonneg"),
        CheckConstraint("candidate_record_count >= 0", name="candidate_count_nonneg"),
        CheckConstraint(
            "length(btrim(alembic_revision)) > 0", name="alembic_revision_nonblank"
        ),
    )


class ReleaseArtifact(Base):
    """One checksummed file belonging to a :class:`ReleaseManifest`.

    Immutable, and **the bytes are stored**. ``content`` holds exactly what
    was rendered at publication and is what every later download returns.

    An earlier design re-rendered each file from the live database on every
    request and refused (409) to serve it when the digest no longer matched.
    That made a published release self-destruct on the next ordinary write:
    uploading one more thermo record for a released species changes the
    candidate set, so ``candidate_records.ndjson`` legitimately grows and the
    citation stops resolving. On a corpus where review is *expected* to
    progress, the window in which a release remained citable was effectively
    zero — and the failure was indistinguishable from tampering, so the
    operator runbook diagnosed normal growth as corruption.

    A release is a snapshot claim. New science arriving after publication is
    the system working, not a fault; it is reported by the non-fatal
    divergence check and answered by cutting the next release.
    """

    __tablename__ = "release_artifact"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    release_manifest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("release_manifest.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    kind: Mapped[ReleaseArtifactKind] = mapped_column(
        SAEnum(ReleaseArtifactKind, name="release_artifact_kind"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # The frozen bytes. NDJSON compresses well and PostgreSQL TOASTs and
    # compresses large values transparently, so a release of realistic size
    # costs far less than the citation guarantee is worth.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

    release_manifest: Mapped["ReleaseManifest"] = relationship(
        back_populates="artifacts", foreign_keys=[release_manifest_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "release_manifest_id", "path", name="uq_release_artifact_release_manifest_id"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("byte_count >= 0", name="byte_count_nonneg"),
        CheckConstraint("record_count >= 0", name="record_count_nonneg"),
        CheckConstraint("length(btrim(path)) > 0", name="path_nonblank"),
    )


__all__ = [
    "SELECTABLE_RECORD_TYPES",
    "CurationPolicy",
    "DatasetRelease",
    "ReleaseArtifact",
    "ReleaseManifest",
    "ReleaseSelection",
]
