"""HTTP API for per-record review/trust state.

Read endpoints are visible to any authenticated user; the manual PATCH
endpoint is curator/admin-gated and routes through
``app/services/record_review.py`` so the transition policy and
self-approval guard apply uniformly with every other writer of this
table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    PaginationParams,
    get_current_user,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.entities.record_review import (
    RecordReviewRead,
    RecordReviewSetStatusRequest,
    ReviewQueuePageRead,
    ReviewQueueReactionEquation,
    ReviewQueueReactionParticipant,
    ReviewQueueSubjectChemistry,
    ReviewQueueSubjectRead,
)
from app.services.record_containers import (
    RecordContainer,
    resolve_record_container,
    resolve_record_containers,
)
from app.services.record_refs import (
    resolve_record_public_ref,
    resolve_record_public_refs,
)
from app.services.record_review import (
    get_record_review,
    list_record_reviews,
    set_record_review_status,
)
from app.services.review_queue import list_review_queue

router = APIRouter()


def _read(
    row: RecordReview,
    record_public_ref: str | None,
    container: RecordContainer | None,
) -> RecordReviewRead:
    """Pure mapper: the ref and the container are supplied, never looked up here.

    Keeping the lookups out is what lets the list route resolve a whole page
    in a handful of grouped queries instead of a few per row. The same split
    is why ``_to_curator_task_response`` exists in ``admin.py``.

    ``container`` carries its type and its ref as one value, so this mapper
    cannot emit half a pair -- a ``container_ref`` with no ``container_type``
    is not addressable by a client, and the schema promises the two are null
    together.
    """
    return RecordReviewRead.model_validate(row).model_copy(
        update={
            "record_public_ref": record_public_ref,
            "container_type": container.container_type if container else None,
            "container_ref": container.container_ref if container else None,
        }
    )


def _read_many(session: Session, rows: list[RecordReview]) -> list[RecordReviewRead]:
    """Map a page, resolving every row's ref and container in grouped passes.

    Both resolvers are bulk and neither is called inside the loop: a page of
    50 costs one query per distinct record type for the refs, plus one per
    distinct record type and one per distinct container type for the
    containers. Moving either call into the comprehension below would restore
    the per-row round trips both services exist to avoid, and every assertion
    about the *content* of this route's responses would still pass -- which
    is why ``test_a_longer_page_does_not_cost_more_queries`` counts
    statements instead. Measured: moving the container resolve into the
    comprehension below takes a ten-row page from 6 queries to 22, and that
    test is the only one that notices.
    """
    keys = [(row.record_type, row.record_id) for row in rows]
    refs = resolve_record_public_refs(session, keys)
    containers = resolve_record_containers(session, keys)
    return [
        _read(
            row,
            refs.get((row.record_type, row.record_id)),
            containers.get((row.record_type, row.record_id)),
        )
        for row in rows
    ]


def _read_one(session: Session, row: RecordReview) -> RecordReviewRead:
    """The single-row form: resolve this one record's ref and container, then map."""
    return _read(
        row,
        resolve_record_public_ref(session, record_type=row.record_type, record_id=row.record_id),
        resolve_record_container(session, record_type=row.record_type, record_id=row.record_id),
    )


@router.get("", response_model=list[RecordReviewRead])
def list_reviews(
    record_type: SubmissionRecordType | None = Query(default=None),
    status: RecordReviewStatus | None = Query(default=None),
    submission_id: int | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    session: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[RecordReviewRead]:
    """List review rows newest-first, filterable by type/status/submission."""
    rows = list_record_reviews(
        session,
        record_type=record_type,
        status=status,
        submission_id=submission_id,
        limit=pagination.limit,
        offset=pagination.skip,
    )
    return _read_many(session, rows)


@router.get("/queue", response_model=ReviewQueuePageRead)
def list_review_queue_page(
    status: RecordReviewStatus | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    session: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> ReviewQueuePageRead:
    """The review queue grouped by subject: one block per record a curator
    judges as one unit, every review row nested under it.

    ``skip``/``limit`` (``pagination``) count SUBJECTS, not records -- a
    page never splits one subject's records across a boundary. See
    :func:`app.services.review_queue.list_review_queue` for how subjects
    are resolved and why this costs what it costs.
    """
    result = list_review_queue(
        session,
        status=status,
        limit=pagination.limit,
        offset=pagination.skip,
    )
    subjects = [
        ReviewQueueSubjectRead(
            subject_type=subject.subject_type,
            subject_ref=subject.subject_ref,
            chemistry=ReviewQueueSubjectChemistry(
                formula=subject.chemistry.formula,
                multiplicity=subject.chemistry.multiplicity,
                species_entry_kind=subject.chemistry.species_entry_kind,
                electronic_state_kind=subject.chemistry.electronic_state_kind,
                electronic_state_label=subject.chemistry.electronic_state_label,
                term_symbol=subject.chemistry.term_symbol,
                stereo_label=subject.chemistry.stereo_label,
                isotope_key=subject.chemistry.isotope_key,
                unmapped_smiles=subject.chemistry.unmapped_smiles,
            ),
            reaction=(
                ReviewQueueReactionEquation(
                    reversible=subject.reaction.reversible,
                    reactants=[
                        ReviewQueueReactionParticipant(
                            species_entry_ref=p.species_entry_ref,
                            species_entry_label=p.species_entry_label,
                            smiles=p.smiles,
                            formula=p.formula,
                            stoichiometry=p.stoichiometry,
                            participant_index=p.participant_index,
                        )
                        for p in subject.reaction.reactants
                    ],
                    products=[
                        ReviewQueueReactionParticipant(
                            species_entry_ref=p.species_entry_ref,
                            species_entry_label=p.species_entry_label,
                            smiles=p.smiles,
                            formula=p.formula,
                            stoichiometry=p.stoichiometry,
                            participant_index=p.participant_index,
                        )
                        for p in subject.reaction.products
                    ],
                )
                if subject.reaction is not None
                else None
            ),
            records=[
                _read(
                    row,
                    result.refs.get((row.record_type, row.record_id)),
                    result.containers.get((row.record_type, row.record_id)),
                )
                for row in subject.rows
            ],
        )
        for subject in result.subjects
    ]
    return ReviewQueuePageRead(
        subjects=subjects,
        subject_total=result.subject_total,
        record_total=result.record_total,
        offset=pagination.skip,
        limit=pagination.limit,
    )


@router.get(
    "/{record_type}/{record_id}",
    response_model=RecordReviewRead,
)
def read_review(
    record_type: SubmissionRecordType,
    record_id: int,
    session: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> RecordReviewRead:
    """Return the current review row for ``(record_type, record_id)``.

    404 if no review row has ever been written for that pair (clients
    should treat that as ``not_reviewed`` if and only if they have
    independently confirmed the underlying record exists).
    """
    row = get_record_review(session, record_type=record_type, record_id=record_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No review row found for this record.",
        )
    return _read_one(session, row)


@router.patch(
    "/{record_type}/{record_id}",
    response_model=RecordReviewRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def set_status(
    record_type: SubmissionRecordType,
    record_id: int,
    body: RecordReviewSetStatusRequest,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(require_curator_or_admin),
) -> RecordReviewRead:
    """Curator/admin: manually transition a record's review status.

    The transition policy and self-approval guard live in the service
    layer; route-side authorisation is the curator/admin role check
    only.
    """
    row = set_record_review_status(
        session,
        record_type=record_type,
        record_id=record_id,
        status=body.status,
        actor=actor,
        submission_id=body.submission_id,
        note=body.note,
    )
    return _read_one(session, row)
