"""Freezing, serving and checking a dataset release manifest.

The manifest is the document a citation resolves to. It states what the release
is, which policy and curator versions produced it, which code and schema
revision it was cut under, what it may be used for, and the SHA-256 of every
file it ships.

Frozen at publication, and only then
------------------------------------
Publication is the **only** write. The document is serialized into
``release_manifest.document_json`` and every claim it makes about the release
is copied into snapshot columns; each artifact's bytes go into
``release_artifact.content``. Everything served afterwards comes from those
rows. Nothing that happens later in the corpus — a new upload, a review
progressing, a DOI being attached, a withdrawal — can change what a citation
resolves to.

This replaced a design that re-derived both the document and the artifacts from
the live database on every read, and returned 409 when the result no longer
hashed to the recorded value. That was wrong twice over:

* one ordinary upload for a released species grew the candidate set, so a
  published release stopped being downloadable — on a corpus where review is
  expected to progress, the citable window was effectively zero; and
* recording the DOI, the last step of publishing, mutated ``dataset_release``
  and broke the digest permanently, so ``verified: true`` was reachable only
  for releases nobody had deposited.

Both are *normal* events. A release is a snapshot claim; new science arriving
afterwards is the system working.

Two different questions, two different answers
----------------------------------------------
:func:`verify_release` asks **"is the frozen release intact?"** — do the stored
bytes still hash to the recorded digests. It should always be true, and a
failure means tampering. It never depends on the live corpus.

:func:`live_divergence` asks **"does the current database still agree with what
this release published?"** — genuinely useful, routinely non-empty, and
explicitly **not** an error. It never blocks a download.

Three formats, three contracts
------------------------------
This is deliberately not the other two things TCKDB can emit:

===================================  ==============================  =========
Format                               Contract                        Lossless?
===================================  ==============================  =========
``tckdb.dataset_release.v1`` (here)  citable, immutable, curated;     no — it
                                     ships selections *and* the       is a
                                     candidates and review history    scientific
                                     they were chosen from            release,
                                                                      not a DB
``tckdb.archive.v1``                 operator disaster recovery;      yes
                                     restores a database
``tckdb.export.v0``                  convenience projection           no, and
                                     (NDJSON/CHEMKIN/ML)              says so
===================================  ==============================  =========

Conflating them is the exact problem this stage was opened to fix, so the
distinction is stated in naming, in this table, in every manifest's
``contract`` block, and in
``backend/docs/specs/dataset_release_and_profiles.md``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import (
    DatasetReleaseStatus,
    ProfileRecommendation,
    ReadProfile,
)
from app.db.models.dataset_release import (
    DatasetRelease,
    ReleaseArtifact,
    ReleaseManifest,
)
from app.services.release import versions
from app.services.release.artifacts import (
    RenderedArtifact,
    canonical_json,
    render_artifacts,
)
from app.services.release.records import encode_scalar


class ReleaseStateError(RuntimeError):
    """Raised when a release is not in a state that permits the operation."""


@dataclass(frozen=True)
class VerificationReport:
    """Integrity of the *frozen* release. Independent of the live corpus."""

    release_ref: str
    tag: str
    manifest_ref: str
    content_sha256_recorded: str
    content_sha256_recomputed: str
    artifacts_checked: int
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class DivergenceReport:
    """Whether the live database still matches what the release published.

    Divergence is **normal and expected**: uploads continue, review advances,
    and the release deliberately does not move with them. It is reported so a
    reader can see how stale a citation is, and it is never an error.
    """

    release_ref: str
    tag: str
    diverged: bool
    differences: list[str] = field(default_factory=list)
    note: str = (
        "A release is a snapshot. Divergence means the corpus has moved on "
        "since publication — normally new uploads or review progressing — not "
        "that the release is damaged. The published bytes are unchanged and "
        "remain downloadable; cut a new release to publish the newer state."
    )


def _contract_block(*, ships: list[str], recovery_archive_schema: str) -> dict[str, Any]:
    """State plainly what this format is, and what it is not.

    ``ships`` and ``recovery_archive_schema`` are passed in rather than read
    from live module constants so that bumping a contract tag or adding an
    artifact kind cannot retroactively change a published document's digest.
    """
    return {
        "kind": "curated_dataset_release",
        "immutable": True,
        "citable": True,
        # A release is a scientific publication of selected values plus their
        # evidence. It is not a database backup and must never be used as one.
        "lossless_database_backup": False,
        "reingestible": False,
        "distinct_from": {
            "recovery_archive": recovery_archive_schema,
            "convenience_projection": "tckdb.export.v0",
        },
        "ships": sorted(ships),
        "omits": [
            # Stated precisely, because a depositor is entitled to know what
            # is *not* in the file they are about to attach a DOI to.
            "raw_artifact_bytes",
            "submission_and_actor_credentials",
            "records_outside_the_release_selection_subjects",
            "internal_database_primary_keys_public_refs_are_used_instead",
            "calculation_result_payloads_beyond_the_cited_level_of_theory_and_software",
            "geometries_fetch_via_the_geometry_endpoint_by_ref",
            "unselected_records_for_subjects_this_release_does_not_cover",
        ],
    }


@dataclass(frozen=True)
class ManifestSnapshot:
    """Every value the frozen document asserts, in one place.

    Built once from live objects at publication, and rebuilt from the manifest
    row's snapshot columns afterwards. Because both paths feed the *same*
    renderer, the served document and the stored columns cannot disagree — a
    property a test asserts directly.
    """

    release_ref: str
    tag: str
    title: str
    description: str | None
    status: str
    published_at: str | None
    doi: str | None
    data_license: str
    code_license: str
    citation_text: str
    contact: str
    changelog_entry: str | None
    policy_ref: str
    policy_name: str
    policy_version: str
    policy_description: str
    policy_criteria: dict[str, Any]
    manifest_schema: str
    recovery_archive_schema: str
    contract: dict[str, Any]
    versions: dict[str, str]
    selected_record_count: int
    candidate_record_count: int
    artifacts: list[dict[str, Any]]


def render_manifest_document(snapshot: ManifestSnapshot) -> dict[str, Any]:
    """Build the canonical manifest document from a snapshot (no digest)."""
    return {
        "schema": snapshot.manifest_schema,
        # A release is by construction the curated contract. Recorded rather
        # than assumed so the document is self-describing outside this codebase.
        "profile": ReadProfile.curated.value,
        "recommendation": ProfileRecommendation.tckdb_curated_release.value,
        # Taken from the snapshot, never rebuilt from module constants: the
        # block is a literal, and rebuilding it made every past release report
        # unverified as soon as anyone edited that literal.
        "contract": snapshot.contract,
        "release": {
            "release_ref": snapshot.release_ref,
            "tag": snapshot.tag,
            "title": snapshot.title,
            "description": snapshot.description,
            # Status and DOI *as at publication*. Later annotations are
            # reported on the release resource, never inside the frozen
            # document — moving the document is how the digest used to break.
            "status_at_publication": snapshot.status,
            "published_at": snapshot.published_at,
            "doi_at_publication": snapshot.doi,
            "data_license": snapshot.data_license,
            "code_license": snapshot.code_license,
            "citation_text": snapshot.citation_text,
            "contact": snapshot.contact,
            "changelog_entry": snapshot.changelog_entry,
        },
        "curation_policy": {
            "curation_policy_ref": snapshot.policy_ref,
            "name": snapshot.policy_name,
            "version": snapshot.policy_version,
            "description": snapshot.policy_description,
            "criteria": snapshot.policy_criteria,
        },
        "versions": dict(snapshot.versions),
        "counts": {
            "selected_records": snapshot.selected_record_count,
            "candidate_records": snapshot.candidate_record_count,
        },
        "artifacts": sorted(snapshot.artifacts, key=lambda a: a["path"]),
    }


def manifest_digest(document: dict[str, Any]) -> str:
    """SHA-256 over the canonical serialization of a manifest document."""
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _artifact_entries(artifacts: list[RenderedArtifact]) -> list[dict[str, Any]]:
    return [
        {
            "path": artifact.path,
            "kind": artifact.kind.value,
            "media_type": artifact.media_type,
            "sha256": artifact.sha256,
            "byte_count": artifact.byte_count,
            "record_count": artifact.record_count,
        }
        for artifact in sorted(artifacts, key=lambda a: a.path)
    ]


def freeze_manifest(
    session: Session,
    release: DatasetRelease,
    *,
    created_by: int | None = None,
) -> ReleaseManifest:
    """Render, checksum and persist the immutable manifest for ``release``.

    The release must already be ``published``, and must actually select
    something: a citable release containing nothing is a DOI attached to an
    empty file.

    :raises ReleaseStateError: the release is not published, selects nothing,
        or already has a manifest (there is exactly one per release: a second
        would make "which manifest does this citation mean?" ambiguous).
    """
    if release.status is not DatasetReleaseStatus.published:
        raise ReleaseStateError(
            "release_not_published: a manifest may only be frozen for a "
            "published release."
        )
    existing = session.scalars(
        select(ReleaseManifest).where(ReleaseManifest.dataset_release_id == release.id)
    ).first()
    if existing is not None:
        raise ReleaseStateError(
            "manifest_already_frozen: this release already has an immutable "
            "manifest; a release has exactly one."
        )

    rendered = render_artifacts(session, release)
    by_kind = {artifact.kind: artifact for artifact in rendered}
    selected_count = _count(by_kind, "selected_records")
    candidate_count = _count(by_kind, "candidate_records")

    if selected_count == 0:
        raise ReleaseStateError(
            "release_selects_nothing: a release with no standing selection "
            "recommends nothing and must not be published. Append at least one "
            "selection, or leave the release as a draft."
        )

    binding = {
        "alembic_revision": versions.alembic_revision(session),
        "backend_version": versions.backend_version(),
        "schemas_package_version": versions.schemas_package_version(),
        "review_policy_version": versions.REVIEW_POLICY_VERSION,
        "recovery_archive_schema": versions.RECOVERY_ARCHIVE_SCHEMA,
    }
    policy = release.curation_policy
    artifact_entries = _artifact_entries(rendered)
    contract = _contract_block(
        ships=[entry["path"] for entry in artifact_entries],
        recovery_archive_schema=binding["recovery_archive_schema"],
    )
    snapshot = ManifestSnapshot(
        release_ref=release.public_ref,
        tag=release.tag,
        title=release.title,
        description=release.description,
        status=release.status.value,
        published_at=encode_scalar(release.published_at),
        doi=release.doi,
        data_license=release.data_license,
        code_license=release.code_license,
        citation_text=release.citation_text,
        contact=release.contact,
        changelog_entry=release.changelog_entry,
        policy_ref=policy.public_ref,
        policy_name=policy.name,
        policy_version=policy.version,
        policy_description=policy.description,
        policy_criteria=encode_scalar(dict(policy.criteria_json or {})),
        manifest_schema=versions.MANIFEST_SCHEMA,
        recovery_archive_schema=binding["recovery_archive_schema"],
        contract=contract,
        versions=binding,
        selected_record_count=selected_count,
        candidate_record_count=candidate_count,
        artifacts=artifact_entries,
    )
    document = render_manifest_document(snapshot)

    manifest = ReleaseManifest(
        dataset_release_id=release.id,
        manifest_schema=snapshot.manifest_schema,
        profile=ReadProfile.curated,
        alembic_revision=binding["alembic_revision"],
        backend_version=binding["backend_version"],
        schemas_package_version=binding["schemas_package_version"],
        review_policy_version=binding["review_policy_version"],
        curation_policy_id=release.curation_policy_id,
        recovery_archive_schema=binding["recovery_archive_schema"],
        release_public_ref=snapshot.release_ref,
        release_published_at=release.published_at,
        release_tag=snapshot.tag,
        release_title=snapshot.title,
        release_description=snapshot.description,
        release_contact=snapshot.contact,
        release_changelog_entry=snapshot.changelog_entry,
        release_status_at_publication=release.status,
        release_doi_at_publication=snapshot.doi,
        data_license=snapshot.data_license,
        code_license=snapshot.code_license,
        citation_text=snapshot.citation_text,
        curation_policy_ref=snapshot.policy_ref,
        curation_policy_name=snapshot.policy_name,
        curation_policy_version=snapshot.policy_version,
        curation_policy_description=snapshot.policy_description,
        curation_policy_criteria_json=snapshot.policy_criteria,
        contract_json=contract,
        document_json=document,
        content_sha256=manifest_digest(document),
        selected_record_count=selected_count,
        candidate_record_count=candidate_count,
        created_by=created_by,
    )
    session.add(manifest)
    session.flush()

    for artifact in sorted(rendered, key=lambda a: a.path):
        session.add(
            ReleaseArtifact(
                release_manifest_id=manifest.id,
                kind=artifact.kind,
                path=artifact.path,
                media_type=artifact.media_type,
                content=artifact.content,
                sha256=artifact.sha256,
                byte_count=artifact.byte_count,
                record_count=artifact.record_count,
            )
        )
    session.flush()
    return manifest


def _count(by_kind: dict[Any, RenderedArtifact], kind_value: str) -> int:
    for kind, artifact in by_kind.items():
        if kind.value == kind_value:
            return artifact.record_count
    return 0


def load_manifest(session: Session, release: DatasetRelease) -> ReleaseManifest | None:
    """The frozen manifest for ``release``, if one has been cut."""
    return session.scalars(
        select(ReleaseManifest).where(ReleaseManifest.dataset_release_id == release.id)
    ).first()


def snapshot_from_manifest(manifest: ReleaseManifest) -> ManifestSnapshot:
    """Rebuild the publication-time snapshot from the manifest's own columns.

    Reads **only** ``manifest`` and its immutable ``artifacts`` — nothing from
    ``dataset_release``, ``curation_policy``, or live module constants. That is
    the whole point: those may all legitimately move on, and the frozen
    document must not move with them. A test asserts the property by mutating
    every one of them and re-rendering.
    """
    return ManifestSnapshot(
        release_ref=manifest.release_public_ref,
        tag=manifest.release_tag,
        title=manifest.release_title,
        description=manifest.release_description,
        status=manifest.release_status_at_publication.value,
        published_at=encode_scalar(manifest.release_published_at),
        doi=manifest.release_doi_at_publication,
        data_license=manifest.data_license,
        code_license=manifest.code_license,
        citation_text=manifest.citation_text,
        contact=manifest.release_contact,
        changelog_entry=manifest.release_changelog_entry,
        policy_ref=manifest.curation_policy_ref,
        policy_name=manifest.curation_policy_name,
        policy_version=manifest.curation_policy_version,
        policy_description=manifest.curation_policy_description,
        policy_criteria=dict(manifest.curation_policy_criteria_json or {}),
        manifest_schema=manifest.manifest_schema,
        recovery_archive_schema=manifest.recovery_archive_schema,
        contract=dict(manifest.contract_json),
        versions={
            "alembic_revision": manifest.alembic_revision,
            "backend_version": manifest.backend_version,
            "schemas_package_version": manifest.schemas_package_version,
            "review_policy_version": manifest.review_policy_version,
            "recovery_archive_schema": manifest.recovery_archive_schema,
        },
        selected_record_count=manifest.selected_record_count,
        candidate_record_count=manifest.candidate_record_count,
        artifacts=[
            {
                "path": row.path,
                "kind": row.kind.value,
                "media_type": row.media_type,
                "sha256": row.sha256,
                "byte_count": row.byte_count,
                "record_count": row.record_count,
            }
            for row in sorted(manifest.artifacts, key=lambda a: a.path)
        ],
    )


def recorded_manifest_document(
    session: Session, manifest: ReleaseManifest
) -> dict[str, Any]:
    """The frozen document, exactly as published.

    ``session`` is unused and kept for call-site compatibility: the document is
    served from ``document_json`` and depends on nothing else.
    """
    del session
    return dict(manifest.document_json)


def verify_release(session: Session, release: DatasetRelease) -> VerificationReport:
    """Check that the *frozen* release is intact.

    Re-hashes the stored artifact bytes and the stored manifest document
    against the digests recorded at publication, and confirms the document can
    still be rebuilt from the manifest's own snapshot columns. It reads nothing
    from the live corpus, so it does not — and must not — fail because new
    science arrived. For that question see :func:`live_divergence`.

    :raises ReleaseStateError: the release has no frozen manifest.
    """
    manifest = _require_manifest(release, session)

    problems: list[str] = []
    for row in sorted(manifest.artifacts, key=lambda a: a.path):
        digest = hashlib.sha256(row.content).hexdigest()
        if digest != row.sha256:
            problems.append(
                f"{row.path}: stored bytes hash to {digest}, recorded {row.sha256}"
            )
        if len(row.content) != row.byte_count:
            problems.append(
                f"{row.path}: stored bytes are {len(row.content)}, "
                f"recorded {row.byte_count}"
            )

    document = dict(manifest.document_json)
    recomputed = manifest_digest(document)
    if recomputed != manifest.content_sha256:
        problems.append(
            "manifest: stored document hashes to "
            f"{recomputed}, recorded {manifest.content_sha256}"
        )

    # The snapshot columns and the stored document must still describe the same
    # release. Divergence here would mean one of them was tampered with.
    rebuilt = render_manifest_document(snapshot_from_manifest(manifest))
    if canonical_json(rebuilt) != canonical_json(document):
        problems.append(
            "manifest: the stored document no longer matches the manifest's "
            "own snapshot columns"
        )

    return VerificationReport(
        release_ref=release.public_ref,
        tag=release.tag,
        manifest_ref=manifest.public_ref,
        content_sha256_recorded=manifest.content_sha256,
        content_sha256_recomputed=recomputed,
        artifacts_checked=len(manifest.artifacts),
        problems=problems,
    )


def live_divergence(session: Session, release: DatasetRelease) -> DivergenceReport:
    """Report how far the live database has moved since publication.

    Advisory. A non-empty result is the normal steady state of a live database
    and never prevents a citation from resolving.

    :raises ReleaseStateError: the release has no frozen manifest.
    """
    manifest = _require_manifest(release, session)

    differences: list[str] = []
    try:
        rendered = {a.path: a for a in render_artifacts(session, release)}
    except Exception as exc:  # pragma: no cover - defensive
        return DivergenceReport(
            release_ref=release.public_ref,
            tag=release.tag,
            diverged=True,
            differences=[f"live rendering failed: {exc}"],
        )

    for row in sorted(manifest.artifacts, key=lambda a: a.path):
        fresh = rendered.get(row.path)
        if fresh is None:
            differences.append(f"{row.path}: no longer renderable from live data")
            continue
        if fresh.sha256 != row.sha256:
            differences.append(
                f"{row.path}: live content differs "
                f"(published {row.record_count} records, live {fresh.record_count})"
            )

    live_release_changed = []
    if release.doi != manifest.release_doi_at_publication:
        live_release_changed.append("doi")
    if release.status is not manifest.release_status_at_publication:
        live_release_changed.append("status")
    if live_release_changed:
        differences.append(
            "release metadata annotated since publication: "
            + ", ".join(sorted(live_release_changed))
        )

    return DivergenceReport(
        release_ref=release.public_ref,
        tag=release.tag,
        diverged=bool(differences),
        differences=differences,
    )


def _require_manifest(
    release: DatasetRelease, session: Session
) -> ReleaseManifest:
    manifest = load_manifest(session, release)
    if manifest is None:
        raise ReleaseStateError(
            "manifest_not_frozen: this release has no immutable manifest to check."
        )
    return manifest


__all__ = [
    "DivergenceReport",
    "ManifestSnapshot",
    "ReleaseStateError",
    "VerificationReport",
    "freeze_manifest",
    "live_divergence",
    "load_manifest",
    "manifest_digest",
    "recorded_manifest_document",
    "render_manifest_document",
    "snapshot_from_manifest",
    "verify_release",
]
