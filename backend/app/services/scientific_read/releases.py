"""Read service for the citable dataset-release surface.

Serves what a citation needs: the release, its frozen manifest with per-file
checksums, its attributed selection ledger, and a live verification of both.

Reads here deliberately do **not** apply the curated review floor. A release is
already the curated contract by construction, and hiding a withdrawn release or
a superseded selection would defeat the point of publishing the audit trail.
"""

from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app.db.models.app_user import AppUser
from app.db.models.common import DatasetReleaseStatus, ReleaseSelectionAction
from app.db.models.dataset_release import (
    CurationPolicy,
    DatasetRelease,
    ReleaseSelection,
)
from app.schemas.reads.scientific_common import Pagination
from app.schemas.reads.scientific_release import (
    CurationPolicySummary,
    CuratorSummary,
    DatasetReleaseSummary,
    ReleaseArtifactSummary,
    ReleaseDivergenceSummary,
    ReleaseManifestSummary,
    ReleaseSelectionRecord,
    ReleaseVerificationSummary,
    ReleaseVersionBinding,
    RequestEcho,
    ScientificReleaseDetailResponse,
    ScientificReleaseListResponse,
    ScientificReleaseManifestResponse,
    ScientificReleaseSelectionsResponse,
)
from app.services.release.artifacts import load_selection_state
from app.services.release.manifest import (
    live_divergence,
    load_manifest,
    recorded_manifest_document,
    verify_release,
)
from app.services.release.records import encode_scalar, public_refs_for
from app.services.scientific_read.common import validate_pagination


class UnknownReleaseError(ValueError):
    """Raised when a release handle resolves to nothing."""


class ManifestNotFrozenError(ValueError):
    """Raised when a release has no immutable manifest yet."""


def resolve_release(session: Session, handle: str) -> DatasetRelease:
    """Resolve a ``rel_...`` public ref or a release tag to a release row.

    Accepting the tag matters: a paper cites ``2026.07.0``, not an opaque
    handle, and a reader who has to translate one into the other before they
    can fetch anything has been handed a chore, not a citation.

    :raises UnknownReleaseError: no release matches.
    """
    release = session.scalars(
        select(DatasetRelease).where(
            (DatasetRelease.public_ref == handle) | (DatasetRelease.tag == handle)
        )
    ).first()
    if release is None:
        raise UnknownReleaseError(f"unknown_release: no release matches {handle!r}.")
    return release


def _policy_summary(
    policy: CurationPolicy, *, with_body: bool = True
) -> CurationPolicySummary:
    return CurationPolicySummary(
        curation_policy_ref=policy.public_ref,
        name=policy.name,
        version=policy.version,
        description=policy.description if with_body else None,
        criteria=dict(policy.criteria_json or {}) if with_body else None,
    )


def _release_summary(
    session: Session, release: DatasetRelease, *, with_body: bool = True
) -> DatasetReleaseSummary:
    return DatasetReleaseSummary(
        release_ref=release.public_ref,
        tag=release.tag,
        title=release.title,
        description=release.description,
        status=release.status,
        published_at=encode_scalar(release.published_at),
        withdrawn_at=encode_scalar(release.withdrawn_at),
        withdrawn_reason=release.withdrawn_reason,
        doi=release.doi,
        data_license=release.data_license,
        code_license=release.code_license,
        citation_text=release.citation_text,
        contact=release.contact,
        changelog_entry=release.changelog_entry,
        curation_policy=_policy_summary(release.curation_policy, with_body=with_body),
        has_manifest=load_manifest(session, release) is not None,
    )


def _manifest_summary(
    session: Session, release: DatasetRelease
) -> ReleaseManifestSummary | None:
    manifest = load_manifest(session, release)
    if manifest is None:
        return None
    base = f"/api/v1/scientific/releases/{release.tag}/artifacts"
    return ReleaseManifestSummary(
        manifest_ref=manifest.public_ref,
        manifest_schema=manifest.manifest_schema,
        profile=manifest.profile,
        content_sha256=manifest.content_sha256,
        generated_at=encode_scalar(manifest.generated_at),
        selected_record_count=manifest.selected_record_count,
        candidate_record_count=manifest.candidate_record_count,
        versions=ReleaseVersionBinding(
            alembic_revision=manifest.alembic_revision,
            backend_version=manifest.backend_version,
            schemas_package_version=manifest.schemas_package_version,
            review_policy_version=manifest.review_policy_version,
            recovery_archive_schema=manifest.recovery_archive_schema,
        ),
        artifacts=[
            ReleaseArtifactSummary(
                path=row.path,
                kind=row.kind,
                media_type=row.media_type,
                sha256=row.sha256,
                byte_count=row.byte_count,
                record_count=row.record_count,
                download_url=f"{base}/{row.path}",
            )
            for row in sorted(manifest.artifacts, key=lambda a: a.path)
        ],
        document=recorded_manifest_document(session, manifest),
    )


def _refs_by_type(session: Session, pairs: list[tuple]) -> dict[tuple, str]:
    """Resolve ``(record_type, record_id)`` pairs to public refs, batched by type."""
    by_type: dict[object, set[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, set()).add(record_id)
    out: dict[tuple, str] = {}
    for record_type, ids in by_type.items():
        found = public_refs_for(
            session, record_type=record_type, record_ids=sorted(ids)
        )
        for record_id, ref in found.items():
            out[(record_type, record_id)] = ref
    return out


def list_releases(
    session: Session,
    *,
    status: DatasetReleaseStatus | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificReleaseListResponse:
    """List releases, newest first. Withdrawn releases are listed, not hidden.

    :raises ValueError: 422 when ``offset``/``limit`` are outside the hosted
        pagination bounds.
    """
    offset, limit = validate_pagination(offset, limit)
    stmt = select(DatasetRelease)
    if status is not None:
        stmt = stmt.where(DatasetRelease.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = list(
        session.scalars(
            stmt.order_by(DatasetRelease.id.desc()).offset(offset).limit(limit)
        )
    )
    return ScientificReleaseListResponse(
        request=RequestEcho(
            filter={"status": status.value if status else None},
        ),
        records=[_release_summary(session, row, with_body=False) for row in page],
        pagination=Pagination(
            offset=offset,
            limit=limit,
            returned=len(page),
            total=total,
            post_collapse_total=total,
        ),
    )


def get_release(session: Session, handle: str) -> ScientificReleaseDetailResponse:
    """Release detail, including the frozen manifest when one exists."""
    release = resolve_release(session, handle)
    return ScientificReleaseDetailResponse(
        request=RequestEcho(filter={"release": handle}),
        record=_release_summary(session, release),
        manifest=_manifest_summary(session, release),
    )


def get_release_manifest(
    session: Session, handle: str
) -> ScientificReleaseManifestResponse:
    """The frozen manifest, its integrity check, and a live-divergence report.

    Two separate questions, deliberately reported separately:

    ``verification``     is the *frozen* release intact — do the stored bytes
                         still hash to the digests recorded at publication.
                         Independent of the live corpus, so it does not fail
                         because new science arrived. A failure means tampering.
    ``live_divergence``  has the database moved on since publication. Routinely
                         true on a live instance and **not an error**; it tells
                         a reader how stale the citation is.

    Conflating them is what made a published release stop resolving after one
    ordinary upload.

    :raises ManifestNotFrozenError: the release has no manifest yet.
    """
    release = resolve_release(session, handle)
    summary = _manifest_summary(session, release)
    if summary is None:
        raise ManifestNotFrozenError(
            f"manifest_not_frozen: release {release.tag!r} has no immutable "
            "manifest. Only published releases are citable."
        )
    report = verify_release(session, release)
    divergence = live_divergence(session, release)
    return ScientificReleaseManifestResponse(
        request=RequestEcho(filter={"release": handle}),
        release_ref=release.public_ref,
        tag=release.tag,
        manifest=summary,
        verification=ReleaseVerificationSummary(
            verified=report.ok,
            content_sha256_recorded=report.content_sha256_recorded,
            content_sha256_recomputed=report.content_sha256_recomputed,
            artifacts_checked=report.artifacts_checked,
            problems=report.problems,
        ),
        live_divergence=ReleaseDivergenceSummary(
            diverged=divergence.diverged,
            differences=divergence.differences,
            note=divergence.note,
        ),
    )


def _selection_record(
    session: Session,
    row: ReleaseSelection,
    *,
    standing: set[int],
    by_id: dict[int, ReleaseSelection],
) -> ReleaseSelectionRecord:
    """Project one selection row. Shared by the ledger and the single read."""
    superseded = by_id.get(row.supersedes_selection_id or -1)
    policy = session.get(CurationPolicy, row.curation_policy_id)
    curator = session.get(AppUser, row.selected_by)
    record_refs = _refs_by_type(session, [(row.record_type, row.record_id)])
    subject_refs = _refs_by_type(session, [(row.subject_type, row.subject_id)])
    return ReleaseSelectionRecord(
        selection_ref=row.public_ref,
        action=row.action,
        stands=row.id in standing
        and row.action is not ReleaseSelectionAction.withdraw,
        record_type=row.record_type.value,
        record_ref=record_refs.get((row.record_type, row.record_id)),
        subject_type=row.subject_type.value,
        subject_ref=subject_refs.get((row.subject_type, row.subject_id)),
        supersedes_selection_ref=(
            superseded.public_ref if superseded is not None else None
        ),
        rationale=row.rationale,
        created_at=encode_scalar(row.created_at),
        curator=(
            CuratorSummary(
                username=curator.username,
                full_name=curator.full_name,
                orcid=curator.orcid,
                affiliation=curator.affiliation,
            )
            if curator is not None
            else None
        ),
        curation_policy=(
            _policy_summary(policy, with_body=False) if policy is not None else None
        ),
    )


def get_release_selection(
    session: Session, handle: str, selection_ref: str
) -> ReleaseSelectionRecord | None:
    """One selection row of a release, by public ref.

    A direct lookup, deliberately: reading a single row back by paging the
    ledger and scanning caps a release at the page size, which is how the
    write path used to fail on the 201st selection.
    """
    release = resolve_release(session, handle)
    row = session.scalars(
        select(ReleaseSelection).where(
            ReleaseSelection.dataset_release_id == release.id,
            ReleaseSelection.public_ref == selection_ref,
        )
    ).first()
    if row is None:
        return None

    state = load_selection_state(session, release)
    standing = {item.id for item in state.active}
    by_id = {item.id: item for item in state.all_selections}
    return _selection_record(
        session,
        row,
        standing=standing,
        by_id=by_id,
    )


def get_release_selections(
    session: Session,
    handle: str,
    *,
    include_superseded: bool = True,
    offset: int = 0,
    limit: int = 50,
) -> ScientificReleaseSelectionsResponse:
    """The release's attributed selection ledger.

    Superseded rows are included by default. The ledger is the evidence that a
    recommendation was reasoned about and revised — filtering it down to only
    what currently stands would hand back the same opaque answer the selection
    layer exists to replace.

    Paging happens in SQL. A mature release publishes thousands of selections,
    and this endpoint exists precisely to be read a page at a time; reading the
    whole ledger to hand back fifty rows made every page cost the same as the
    largest one. ``stands`` is the reason that was ever tempting — it is a
    property of the supersession chain, not of the row — but "nothing
    supersedes me" is a correlated ``NOT EXISTS``, so the whole-set question
    is answerable per row without the whole set in memory.

    :raises ValueError: 422 when ``offset``/``limit`` are outside the hosted
        pagination bounds.
    """
    release = resolve_release(session, handle)
    offset, limit = validate_pagination(offset, limit)

    superseder = aliased(ReleaseSelection)
    stands = and_(
        ~(
            select(1)
            .where(
                superseder.dataset_release_id == release.id,
                superseder.supersedes_selection_id == ReleaseSelection.id,
            )
            .exists()
        ),
        ReleaseSelection.action != ReleaseSelectionAction.withdraw,
    )

    stmt = select(ReleaseSelection).where(
        ReleaseSelection.dataset_release_id == release.id
    )
    if not include_superseded:
        stmt = stmt.where(stands)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page_rows = list(
        session.execute(
            stmt.add_columns(stands.label("stands"))
            .order_by(ReleaseSelection.id)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    rows = [row for row, _stands in page_rows]
    stands_by_id = {row.id: bool(flag) for row, flag in page_rows}

    # The row a page row supersedes may live outside the page, so resolve those
    # refs by id rather than assuming the page is self-contained.
    parent_ids = {
        row.supersedes_selection_id
        for row in rows
        if row.supersedes_selection_id is not None
    }
    by_id: dict[int, str] = (
        dict(
            session.execute(
                select(ReleaseSelection.id, ReleaseSelection.public_ref).where(
                    ReleaseSelection.dataset_release_id == release.id,
                    ReleaseSelection.id.in_(parent_ids),
                )
            ).all()
        )
        if parent_ids
        else {}
    )

    policies = {
        policy.id: policy
        for policy in session.scalars(
            select(CurationPolicy).where(
                CurationPolicy.id.in_({row.curation_policy_id for row in rows} or {0})
            )
        )
    }
    curators = {
        user.id: user
        for user in session.scalars(
            select(AppUser).where(AppUser.id.in_({row.selected_by for row in rows} or {0}))
        )
    }

    # Resolve refs one query per record type, not one per row: a per-row lookup
    # would make the ledger endpoint quadratic in the thing it exists to
    # publish. Scoped to the page, because that is all this response renders.
    record_refs = _refs_by_type(session, [(r.record_type, r.record_id) for r in rows])
    subject_refs = _refs_by_type(session, [(r.subject_type, r.subject_id) for r in rows])

    records = [
        ReleaseSelectionRecord(
            selection_ref=row.public_ref,
            action=row.action,
            stands=stands_by_id[row.id],
            record_type=row.record_type.value,
            record_ref=record_refs.get((row.record_type, row.record_id)),
            subject_type=row.subject_type.value,
            subject_ref=subject_refs.get((row.subject_type, row.subject_id)),
            supersedes_selection_ref=by_id.get(row.supersedes_selection_id),
            rationale=row.rationale,
            created_at=encode_scalar(row.created_at),
            curator=(
                CuratorSummary(
                    username=curators[row.selected_by].username,
                    full_name=curators[row.selected_by].full_name,
                    orcid=curators[row.selected_by].orcid,
                    affiliation=curators[row.selected_by].affiliation,
                )
                if row.selected_by in curators
                else None
            ),
            curation_policy=(
                _policy_summary(policies[row.curation_policy_id], with_body=False)
                if row.curation_policy_id in policies
                else None
            ),
        )
        for row in rows
    ]

    return ScientificReleaseSelectionsResponse(
        request=RequestEcho(
            filter={"release": handle, "include_superseded": include_superseded}
        ),
        release_ref=release.public_ref,
        tag=release.tag,
        records=records,
        pagination=Pagination(
            offset=offset,
            limit=limit,
            returned=len(rows),
            total=total,
            post_collapse_total=total,
        ),
    )


__all__ = [
    "ManifestNotFrozenError",
    "UnknownReleaseError",
    "get_release",
    "get_release_manifest",
    "get_release_selection",
    "get_release_selections",
    "list_releases",
    "resolve_release",
]
