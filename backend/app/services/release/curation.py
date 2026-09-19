"""Write paths for curated selections and dataset releases.

Every function here is either an insert or a status transition on the release
row. Nothing in this module writes to a scientific table, and that is enforced
by a test, not just by discipline: a curated selection must never change the
number it selects, or the selection stops being an opinion about the data and
becomes part of it.

``release_selection`` is append-only at three levels — no UPDATE/DELETE in this
module, a database trigger that rejects both, and a UNIQUE constraint on
``supersedes_selection_id`` that keeps supersession chains linear. Superseding
therefore *appends*, and the earlier curator's reasoning stays readable
forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.common import (
    DatasetReleaseStatus,
    RecordReviewStatus,
    ReleaseSelectionAction,
    SubmissionRecordType,
)
from app.db.models.dataset_release import (
    SELECTABLE_RECORD_TYPES,
    CurationPolicy,
    DatasetRelease,
    ReleaseSelection,
)
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.services.release.artifacts import release_record_universe
from app.services.release.record_rights import linked_rights
from app.services.release.records import (
    CANDIDATE_SOURCES,
    RECORD_TABLES,
    public_refs_for,
)
from app.services.rights import licenses_match
from app.services.scientific_read.profile import CURATED_REVIEW_FLOOR


class ReleaseCurationError(ValueError):
    """Raised when a curation request is not coherent.

    The base carries the *malformed request* meaning and reaches a client as
    422: the body said something the curator can correct and resend. The two
    subclasses below exist because that is not true of every refusal on this
    router, and answering 422 for a condition no corrected body repairs
    invites a client to resend the same request forever.
    """


class ReleaseStateConflict(ReleaseCurationError):
    """The request is coherent; the release is not in the state it assumes.

    409, and the status is a property of *this class* rather than of the
    route that catches it. That distinction is not cosmetic: it is exactly
    the confusion :mod:`app.api.code_catalogue` recorded against
    ``curation_policy_version_conflict``, whose entry had to explain that
    reading the raise site said 422 while the one route that could reach it
    wrapped it in 409. Both of those codes now raise this class, so the raise
    site and the wire agree.

    Membership test: *no corrected body makes this request succeed.* A
    curator must publish the release, cut a new one, supersede a different
    row, or accept the DOI already cited. Nothing they can retype helps,
    which is what separates a conflict from a malformed payload.
    """


class ReleaseRecordNotFound(ReleaseCurationError):
    """A public ref the request named resolves to no row -- 404.

    This router already answers 404 for an unknown release, an unknown
    selection and an unknown curation policy. A candidate record was the
    fourth ``unknown_*`` on the same router and the only one answering 422,
    which is an inconsistency inside a single file rather than between two.
    """


def _naive_utcnow() -> datetime:
    """Timezone-naive UTC, matching the ``DateTime(timezone=False)`` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: House default for the **data** license of a release: the scientific corpus
#: is published under CC BY 4.0, which permits any reuse and obliges the reuser
#: to attribute. Attribution is the whole point — a dataset release is the
#: depositor's citable credential, and a license that did not require the
#: citation would quietly discard it. See ``LICENSE-DATA`` at the repository
#: root for what this covers and, importantly, what it does not.
#:
#: The default expresses what the *operator is entitled to license*. It is not
#: consent on a third party's behalf: before a deployment takes deposits from
#: a second contributor, the license has to be agreed at deposit time. See the
#: "Who may license what" section of ``LICENSE-DATA``.
DEFAULT_DATA_LICENSE = "CC-BY-4.0"

#: House default for the **code** license: MIT, matching ``LICENSE``. Kept
#: beside the data default because the pair is the point — the program and the
#: corpus are separate works, reused by different people for different reasons,
#: and conflating them is how attribution gets lost.
DEFAULT_CODE_LICENSE = "MIT"


# ---------------------------------------------------------------------------
# Curation policy
# ---------------------------------------------------------------------------


def resolve_curation_policy(
    session: Session,
    *,
    name: str,
    version: str,
    description: str,
    criteria: dict | None = None,
    created_by: int | None = None,
) -> CurationPolicy:
    """Look up or create the ``(name, version)`` policy row.

    Identity semantics: the same policy version resolves to the same row for
    every release that cites it. Re-registering an existing ``(name, version)``
    with a *different* body is rejected rather than silently updated — a
    published release states which policy version governed it, and quietly
    rewriting that policy would retroactively change what the release claims.

    :raises ReleaseCurationError: the policy version already exists with a
        different description or criteria.
    """
    existing = session.scalars(
        select(CurationPolicy).where(
            CurationPolicy.name == name, CurationPolicy.version == version
        )
    ).first()
    payload = dict(criteria or {})
    if existing is not None:
        if existing.description != description or existing.criteria_json != payload:
            raise ReleaseStateConflict(
                "curation_policy_version_conflict: policy "
                f"{name!r} version {version!r} already exists with different "
                "content. Publish a new version instead of editing a version "
                "that released datasets already cite."
            )
        return existing

    policy = CurationPolicy(
        name=name,
        version=version,
        description=description,
        criteria_json=payload,
        created_by=created_by,
    )
    session.add(policy)
    session.flush()
    return policy


# ---------------------------------------------------------------------------
# Dataset release
# ---------------------------------------------------------------------------


def create_release(
    session: Session,
    *,
    tag: str,
    title: str,
    curation_policy: CurationPolicy,
    data_license: str = DEFAULT_DATA_LICENSE,
    code_license: str = DEFAULT_CODE_LICENSE,
    citation_text: str,
    contact: str,
    description: str | None = None,
    changelog_entry: str | None = None,
    created_by: int | None = None,
) -> DatasetRelease:
    """Open a new draft release.

    ``data_license`` and ``code_license`` default to the house pair
    (:data:`DEFAULT_DATA_LICENSE`, :data:`DEFAULT_CODE_LICENSE`) so that a
    caller who does not think about licensing still cuts a release the
    community may legally reuse and is obliged to cite. An explicit value
    always wins: an operator publishing under different terms passes them and
    they are stored verbatim.

    Deliberately has no ``doi`` parameter. A DOI is minted by depositing the
    published artifacts somewhere durable, is not retractable, and therefore
    must not be assertable at draft time; see :func:`record_doi` and
    ``backend/docs/deployment/cutting_a_dataset_release.md``.

    A blank license is still refused, and deliberately not by this function:
    the route's request model rejects an empty string, and the column's
    ``data_license_nonblank`` / ``code_license_nonblank`` check constraints
    reject a space-only one (only spaces — PostgreSQL's one-argument
    ``btrim`` does not strip tabs or newlines; see
    ``tests/services/release/test_release_licensing.py`` for that measured
    boundary). The default answers "the caller said nothing"; it must never
    paper over "the caller said *no license*", which is a different and much
    louder claim.

    :raises ReleaseCurationError: ``tag`` is already taken.
    """
    if session.scalars(
        select(DatasetRelease).where(DatasetRelease.tag == tag)
    ).first() is not None:
        raise ReleaseStateConflict(f"release_tag_taken: {tag!r} already exists.")

    release = DatasetRelease(
        tag=tag,
        title=title,
        description=description,
        status=DatasetReleaseStatus.draft,
        curation_policy_id=curation_policy.id,
        data_license=data_license,
        code_license=code_license,
        citation_text=citation_text,
        contact=contact,
        changelog_entry=changelog_entry,
        created_by=created_by,
    )
    session.add(release)
    session.flush()
    return release


def publish_release(session: Session, release: DatasetRelease) -> DatasetRelease:
    """Move a draft release to ``published`` and stamp ``published_at``.

    Re-checks every standing selection against the approval floor. Selections
    are validated when appended, but review is a moving target: a record can be
    deprecated or rejected between being selected and the release going out,
    and publishing it then would put an unapproved value under a citable
    recommendation.

    Re-checks the rights basis too, and over a *wider* set than the
    selections: everything the release will ship. ``candidate_records.ndjson``
    carries every candidate for every covered subject, selected or not, so a
    gate on selections alone would still publish unlicensed bytes -- an
    ordinary upload for a released species between selection and publication
    is enough. See :func:`_assert_standing_selections_still_releasable`.

    :raises ReleaseCurationError: the release is not a draft, a standing
        selection now names a record below the approval floor, or a record the
        release would ship has no compatible rights basis.
    """
    if release.status is not DatasetReleaseStatus.draft:
        raise ReleaseStateConflict(
            f"release_not_draft: cannot publish a release in status "
            f"{release.status.value!r}."
        )
    _assert_standing_selections_still_approved(session, release)
    _assert_standing_selections_still_releasable(session, release)
    release.status = DatasetReleaseStatus.published
    release.published_at = _naive_utcnow()
    session.flush()
    return release


def _assert_standing_selections_still_approved(
    session: Session, release: DatasetRelease
) -> None:
    """Every selection that will ship must still be approved at publish time."""
    rows = list(
        session.scalars(
            select(ReleaseSelection)
            .where(ReleaseSelection.dataset_release_id == release.id)
            .order_by(ReleaseSelection.id)
        )
    )
    superseded_ids = {
        row.supersedes_selection_id for row in rows if row.supersedes_selection_id
    }
    standing = [
        row
        for row in rows
        if row.id not in superseded_ids
        and row.action is not ReleaseSelectionAction.withdraw
    ]
    for row in standing:
        try:
            _assert_record_is_approved(
                session, record_type=row.record_type, record_id=row.record_id
            )
        except ReleaseCurationError as exc:
            raise ReleaseCurationError(
                f"selection_no_longer_approved: selection {row.public_ref} can no "
                f"longer be published -- {exc}"
            ) from exc


def _assert_standing_selections_still_releasable(
    session: Session, release: DatasetRelease
) -> None:
    """Everything the release will ship must have a compatible rights basis.

    Two sets, two code families, one reader:

    * the **standing selections** -- refused as ``rights_basis_missing`` /
      ``rights_basis_incompatible``, the same codes the selection was checked
      against when appended (an attestation can be superseded to a different
      license in between);
    * the **rest of the candidate set** -- every other record that
      ``candidate_records.ndjson`` would carry, refused as
      ``candidate_rights_basis_missing`` / ``candidate_rights_basis_incompatible``
      so a curator can tell "the record you chose" from "a record you did not
      choose but the release ships anyway".

    The set is taken from :func:`release_record_universe`, the same function
    the renderer uses, so the gate covers exactly what the file contains.
    **Refuse, never filter**: dropping an unattested candidate from the file
    would falsify the release's claim that every candidate is retrievable.
    """
    universe = release_record_universe(session, release)
    standing = {(row.record_type, row.record_id) for row in universe.state.active}
    _assert_records_have_rights_basis(
        session, pairs=standing, data_license=release.data_license, candidate=False
    )
    _assert_records_have_rights_basis(
        session,
        pairs=universe.candidate_pairs - standing,
        data_license=release.data_license,
        candidate=True,
    )


def withdraw_release(
    session: Session, release: DatasetRelease, *, reason: str
) -> DatasetRelease:
    """Retract a published release, keeping the row and its manifest.

    A withdrawn release is *not* deleted: a citation must never dangle. The
    status is what tells a reader not to rely on it.

    :raises ReleaseCurationError: the release is not published, or no reason
        was given.
    """
    if release.status is not DatasetReleaseStatus.published:
        raise ReleaseStateConflict(
            f"release_not_published: cannot withdraw a release in status "
            f"{release.status.value!r}."
        )
    if not reason.strip():
        raise ReleaseCurationError("withdraw_reason_required: a reason is required.")
    release.status = DatasetReleaseStatus.withdrawn
    release.withdrawn_at = _naive_utcnow()
    release.withdrawn_reason = reason
    session.flush()
    return release


def record_doi(session: Session, release: DatasetRelease, *, doi: str) -> DatasetRelease:
    """Attach a DOI minted out-of-band for an already-published release.

    Kept separate from :func:`create_release` and :func:`publish_release` on
    purpose: TCKDB does not mint DOIs, it records one after a deposit has
    actually been made. Overwriting an existing DOI is refused — that would
    silently repoint a citation.

    :raises ReleaseCurationError: the release is not published, or already has
        a different DOI.
    """
    if release.status is not DatasetReleaseStatus.published:
        raise ReleaseStateConflict(
            "release_not_published: only a published release can carry a DOI."
        )
    if release.doi is not None and release.doi != doi:
        raise ReleaseStateConflict(
            "doi_already_recorded: this release already cites a different DOI."
        )
    release.doi = doi
    session.flush()
    return release


# ---------------------------------------------------------------------------
# Selections
# ---------------------------------------------------------------------------


def _assert_record_exists(
    session: Session, *, record_type: SubmissionRecordType, record_id: int
) -> None:
    table = Base.metadata.tables[RECORD_TABLES[record_type]]
    found = session.execute(
        select(table.c.id).where(table.c.id == record_id)
    ).first()
    if found is None:
        raise ReleaseRecordNotFound(
            f"unknown_record: no {record_type.value} record matches the request."
        )


def _assert_subject_matches(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    subject_type: SubmissionRecordType,
    subject_id: int,
) -> None:
    """The selected candidate must actually belong to the stated subject.

    Without this a release could claim "the TCKDB thermo for ethanol" while
    pointing at a record attached to something else entirely — a mistake no
    downstream consumer could detect.
    """
    model, fk_attr, expected_subject_type = CANDIDATE_SOURCES[record_type]
    if subject_type is not expected_subject_type:
        raise ReleaseCurationError(
            f"subject_type_mismatch: a {record_type.value} selection is about a "
            f"{expected_subject_type.value}, not a {subject_type.value}."
        )
    actual = session.scalars(
        select(getattr(model, fk_attr)).where(model.id == record_id)
    ).first()
    if actual != subject_id:
        raise ReleaseCurationError(
            "record_subject_mismatch: the selected record does not belong to "
            "the stated subject."
        )


#: Public-ref prefix → selectable record type. Mirrors
#: ``app.services.public_refs.PREFIXES``; a drift-guard test keeps them in step.
SELECTABLE_REF_PREFIXES: dict[str, SubmissionRecordType] = {
    "thm": SubmissionRecordType.thermo,
    "sm": SubmissionRecordType.statmech,
    "trn": SubmissionRecordType.transport,
    "kin": SubmissionRecordType.kinetics,
    "nsolve": SubmissionRecordType.network_solve,
    "tse": SubmissionRecordType.transition_state_entry,
}


@dataclass(frozen=True)
class ResolvedCandidate:
    """A candidate record addressed by public ref, with its subject derived."""

    record_type: SubmissionRecordType
    record_id: int
    subject_type: SubmissionRecordType
    subject_id: int


def resolve_selectable_record(session: Session, record_ref: str) -> ResolvedCandidate:
    """Resolve a candidate's public ref and derive the subject it belongs to.

    Curators address candidates by public ref, never by database id — the same
    rule the rest of the API follows. The *subject* is derived from the record
    rather than supplied, so "the TCKDB thermo for ethanol" cannot be recorded
    against a record that belongs to something else.

    :raises ReleaseCurationError: the prefix is not a selectable record type,
        or no record matches the ref.
    """
    prefix = record_ref.split("_", 1)[0] if "_" in record_ref else ""
    record_type = SELECTABLE_REF_PREFIXES.get(prefix)
    if record_type is None:
        raise ReleaseCurationError(
            f"record_ref_not_selectable: {record_ref!r} does not name a "
            "selectable record. Selectable prefixes: "
            + ", ".join(sorted(SELECTABLE_REF_PREFIXES))
            + "."
        )

    table = Base.metadata.tables[RECORD_TABLES[record_type]]
    row = session.execute(
        select(table.c.id).where(table.c.public_ref == record_ref)
    ).first()
    if row is None:
        raise ReleaseRecordNotFound(f"unknown_record: no record matches {record_ref!r}.")
    record_id = row[0]

    model, fk_attr, subject_type = CANDIDATE_SOURCES[record_type]
    subject_id = session.scalars(
        select(getattr(model, fk_attr)).where(model.id == record_id)
    ).first()
    if subject_id is None:
        raise ReleaseCurationError(
            f"record_has_no_subject: {record_ref!r} is not attached to a "
            f"{subject_type.value}, so a release cannot recommend it *for* anything."
        )
    return ResolvedCandidate(
        record_type=record_type,
        record_id=record_id,
        subject_type=subject_type,
        subject_id=subject_id,
    )


def _assert_record_is_approved(
    session: Session, *, record_type: SubmissionRecordType, record_id: int
) -> None:
    """A release may only recommend a record a human reviewer has approved.

    Two independent reasons, either of which is sufficient:

    1. **Integrity.** The accepted-science trigger freezes a product row only
       once it has been approved. An unapproved record can still be edited in
       place, so a release that selects one can have the value under its own
       recommendation changed after publication — silently, and after the
       manifest was checksummed. Restricting selections to approved records is
       what makes "a released value cannot drift by edit" actually true rather
       than accidentally true.
    2. **Coherence.** ``profile=curated`` refuses to *show* a record below the
       approval floor. A published release recommending one would have the API
       simultaneously hiding a record and endorsing it.

    :raises ReleaseCurationError: no review row exists, or its status is below
        :data:`app.services.scientific_read.profile.CURATED_REVIEW_FLOOR`.
    """
    review = session.scalars(
        select(RecordReview).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == record_id,
        )
    ).first()
    status = review.status if review is not None else RecordReviewStatus.not_reviewed
    if REVIEW_RANK[status] > REVIEW_RANK[CURATED_REVIEW_FLOOR]:
        raise ReleaseCurationError(
            "record_not_approved: a release may only select a record at or "
            f"above {CURATED_REVIEW_FLOOR.value!r} review status; this one is "
            f"{status.value!r}. Review the record first, or select a different "
            "candidate."
        )


def _assert_record_has_rights_basis(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    data_license: str,
) -> None:
    """A release may only ship a record somebody agreed to license under its terms.

    The record is followed to every submission that links it, and each of
    those must carry a *standing* rights attestation (latest, not superseded)
    whose ``license_id`` matches ``data_license`` exactly, case-insensitively.
    Every linked submission must pass -- a record that arrived twice, once
    with agreement and once without, is refused, because the release cannot
    say which deposit it is republishing.

    A record with no submission link at all is refused as missing. There is
    nothing to attest against: the historical path is a curator creating a
    ``migration`` submission, linking the record, and attesting that.

    :raises ReleaseCurationError: ``rights_basis_missing`` or
        ``rights_basis_incompatible``.
    """
    _assert_records_have_rights_basis(
        session,
        pairs={(record_type, record_id)},
        data_license=data_license,
        candidate=False,
    )


def _assert_records_have_rights_basis(
    session: Session,
    *,
    pairs: set[tuple[SubmissionRecordType, int]],
    data_license: str,
    candidate: bool,
) -> None:
    """Batch form of :func:`_assert_record_has_rights_basis`.

    ``candidate=True`` reports under the ``candidate_*`` codes -- same test,
    different question: not "may the record you chose ship?" but "may the
    record you did *not* choose, which ships beside it, ship?".
    """
    if not pairs:
        return
    rights = linked_rights(session, pairs=pairs)
    refs: dict[tuple[SubmissionRecordType, int], str | None] = {}
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, []).append(record_id)
    for record_type, ids in by_type.items():
        found = public_refs_for(session, record_type=record_type, record_ids=sorted(ids))
        for record_id in ids:
            refs[(record_type, record_id)] = found.get(record_id)

    for pair in sorted(pairs, key=lambda p: (p[0].value, refs.get(p) or "", p[1])):
        record_type, _record_id = pair
        record = refs.get(pair) or f"a {record_type.value} record"
        links = rights.get(pair, [])
        if not links:
            if candidate:
                raise ReleaseCurationError(
                    f"candidate_rights_basis_missing: {record} would ship as a "
                    "candidate but is linked to no submission, so nobody has "
                    "agreed to license it. Attach it to a submission and record "
                    "a rights attestation before publishing."
                )
            raise ReleaseCurationError(
                f"rights_basis_missing: {record} is linked to no submission, so "
                "nobody has agreed to license it. A release may only ship a "
                "record whose deposit carries a rights attestation."
            )
        for link in links:
            if link.attestation is None:
                if candidate:
                    raise ReleaseCurationError(
                        f"candidate_rights_basis_missing: {record} would ship as "
                        f"a candidate but its deposit {link.submission_ref} has no "
                        "rights attestation. Record one before publishing."
                    )
                raise ReleaseCurationError(
                    f"rights_basis_missing: deposit {link.submission_ref} of "
                    f"{record} carries no rights attestation, so nobody has "
                    "agreed to license it. Record one -- the depositor's "
                    "agreement, or a curator's basis -- before selecting it."
                )
            if not licenses_match(link.attestation.license_id, data_license):
                attested = link.attestation.license_id
                if candidate:
                    raise ReleaseCurationError(
                        f"candidate_rights_basis_incompatible: {record} would ship "
                        f"as a candidate but its deposit {link.submission_ref} is "
                        f"attested under {attested!r}, not the release's "
                        f"{data_license!r}."
                    )
                raise ReleaseCurationError(
                    f"rights_basis_incompatible: deposit {link.submission_ref} of "
                    f"{record} is attested under {attested!r}, and this release "
                    f"publishes under {data_license!r}. Compatibility is an exact "
                    "match of license identifiers; record a new attestation "
                    "naming the release's license, or cut the release under the "
                    "attested one."
                )


def add_selection(
    session: Session,
    *,
    release: DatasetRelease,
    record_type: SubmissionRecordType,
    record_id: int,
    subject_type: SubmissionRecordType,
    subject_id: int,
    rationale: str,
    selected_by: int,
    curation_policy: CurationPolicy | None = None,
) -> ReleaseSelection:
    """Append a first selection of ``record_id`` for ``subject_id``.

    :raises ReleaseCurationError: the release is not a draft, the record type
        is not selectable, the record is below the approval floor, no
        compatible rights basis stands for its deposit, the record or subject
        does not exist or does not match, or the subject already has a
        standing selection in this release (use :func:`supersede_selection` —
        changing a decision appends).
    """
    _assert_draft(release)
    if record_type not in SELECTABLE_RECORD_TYPES:
        raise ReleaseCurationError(
            f"record_type_not_selectable: {record_type.value} cannot be named by "
            "a release selection."
        )
    if not rationale.strip():
        raise ReleaseCurationError("rationale_required: a selection needs a reason.")

    _assert_record_exists(session, record_type=record_type, record_id=record_id)
    _assert_record_is_approved(
        session, record_type=record_type, record_id=record_id
    )
    _assert_record_has_rights_basis(
        session,
        record_type=record_type,
        record_id=record_id,
        data_license=release.data_license,
    )
    _assert_subject_matches(
        session,
        record_type=record_type,
        record_id=record_id,
        subject_type=subject_type,
        subject_id=subject_id,
    )

    standing = current_selection(
        session,
        release=release,
        subject_type=subject_type,
        subject_id=subject_id,
        record_type=record_type,
    )
    if standing is not None:
        raise ReleaseStateConflict(
            "selection_already_stands: this subject already has a standing "
            "selection in this release. Supersede it rather than adding a "
            "second one -- selections are append-only, not additive."
        )

    selection = ReleaseSelection(
        dataset_release_id=release.id,
        curation_policy_id=(curation_policy or release.curation_policy).id,
        record_type=record_type,
        record_id=record_id,
        subject_type=subject_type,
        subject_id=subject_id,
        action=ReleaseSelectionAction.select,
        rationale=rationale,
        selected_by=selected_by,
    )
    session.add(selection)
    session.flush()
    return selection


def supersede_selection(
    session: Session,
    *,
    superseded: ReleaseSelection,
    record_id: int,
    rationale: str,
    selected_by: int,
    curation_policy: CurationPolicy | None = None,
) -> ReleaseSelection:
    """Append a replacement for a standing selection.

    The superseded row is not touched. Its rationale, its curator and its
    timestamp stay exactly as written — which is the whole reason changing a
    decision is an insert.

    :raises ReleaseCurationError: the release is not a draft, the row has
        already been superseded, the replacement is below the approval floor,
        or it does not belong to the same subject.
    """
    release = superseded.dataset_release
    _assert_draft(release)
    _assert_not_already_superseded(session, superseded)
    if not rationale.strip():
        raise ReleaseCurationError("rationale_required: a supersession needs a reason.")
    if record_id == superseded.record_id:
        raise ReleaseCurationError(
            "supersedes_same_record: superseding with the same record records no "
            "decision. Withdraw it, or select a different candidate."
        )

    _assert_record_exists(
        session, record_type=superseded.record_type, record_id=record_id
    )
    _assert_record_is_approved(
        session, record_type=superseded.record_type, record_id=record_id
    )
    _assert_record_has_rights_basis(
        session,
        record_type=superseded.record_type,
        record_id=record_id,
        data_license=release.data_license,
    )
    _assert_subject_matches(
        session,
        record_type=superseded.record_type,
        record_id=record_id,
        subject_type=superseded.subject_type,
        subject_id=superseded.subject_id,
    )

    selection = ReleaseSelection(
        dataset_release_id=release.id,
        curation_policy_id=(
            curation_policy.id if curation_policy else superseded.curation_policy_id
        ),
        record_type=superseded.record_type,
        record_id=record_id,
        subject_type=superseded.subject_type,
        subject_id=superseded.subject_id,
        action=ReleaseSelectionAction.supersede,
        supersedes_selection_id=superseded.id,
        rationale=rationale,
        selected_by=selected_by,
    )
    session.add(selection)
    session.flush()
    return selection


def withdraw_selection(
    session: Session,
    *,
    superseded: ReleaseSelection,
    rationale: str,
    selected_by: int,
) -> ReleaseSelection:
    """Append a withdrawal: the release now recommends nothing for this subject.

    :raises ReleaseCurationError: the release is not a draft or the row has
        already been superseded.
    """
    release = superseded.dataset_release
    _assert_draft(release)
    _assert_not_already_superseded(session, superseded)
    if not rationale.strip():
        raise ReleaseCurationError("rationale_required: a withdrawal needs a reason.")

    selection = ReleaseSelection(
        dataset_release_id=release.id,
        curation_policy_id=superseded.curation_policy_id,
        record_type=superseded.record_type,
        # A withdrawal repeats the record it retires. Naming a different one
        # would read as a selection while acting as a removal.
        record_id=superseded.record_id,
        subject_type=superseded.subject_type,
        subject_id=superseded.subject_id,
        action=ReleaseSelectionAction.withdraw,
        supersedes_selection_id=superseded.id,
        rationale=rationale,
        selected_by=selected_by,
    )
    session.add(selection)
    session.flush()
    return selection


def current_selection(
    session: Session,
    *,
    release: DatasetRelease,
    subject_type: SubmissionRecordType,
    subject_id: int,
    record_type: SubmissionRecordType,
) -> ReleaseSelection | None:
    """The standing selection for a subject, or ``None`` if there is none.

    Computed from the append-only chain — head of chain, not withdrawn — rather
    than read off a mutable flag.
    """
    rows = list(
        session.scalars(
            select(ReleaseSelection)
            .where(
                ReleaseSelection.dataset_release_id == release.id,
                ReleaseSelection.subject_type == subject_type,
                ReleaseSelection.subject_id == subject_id,
                ReleaseSelection.record_type == record_type,
            )
            .order_by(ReleaseSelection.id)
        )
    )
    superseded_ids = {
        row.supersedes_selection_id for row in rows if row.supersedes_selection_id
    }
    for row in reversed(rows):
        if row.id in superseded_ids:
            continue
        if row.action is ReleaseSelectionAction.withdraw:
            return None
        return row
    return None


def _assert_draft(release: DatasetRelease) -> None:
    if release.status is not DatasetReleaseStatus.draft:
        raise ReleaseStateConflict(
            "release_not_draft: selections may only be appended while a release "
            "is a draft. A published release is frozen, checksummed and cited."
        )


def _assert_not_already_superseded(
    session: Session, selection: ReleaseSelection
) -> None:
    replacement = session.scalars(
        select(ReleaseSelection).where(
            ReleaseSelection.supersedes_selection_id == selection.id
        )
    ).first()
    if replacement is not None:
        raise ReleaseStateConflict(
            "selection_already_superseded: this selection has already been "
            "replaced; supersede the row that currently stands."
        )


__all__ = [
    "SELECTABLE_REF_PREFIXES",
    "ReleaseCurationError",
    "ReleaseRecordNotFound",
    "ReleaseStateConflict",
    "ResolvedCandidate",
    "add_selection",
    "create_release",
    "current_selection",
    "publish_release",
    "record_doi",
    "resolve_curation_policy",
    "resolve_selectable_record",
    "supersede_selection",
    "withdraw_release",
    "withdraw_selection",
]
