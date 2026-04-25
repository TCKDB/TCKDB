"""Service helpers for creating and listing species-entry review rows.

Reviews are append-only curation records attached to a single
``species_entry``. The reviewer's identity is taken from the authenticated
caller in the API layer; callers do not supply a reviewer id. Each
``(species_entry, user, role)`` combination is unique at the DB level — the
service raises :class:`DomainError` on duplicates so the API can return 400
instead of leaking a 409 through the generic integrity handler.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import SpeciesEntryReviewRole
from app.db.models.species import SpeciesEntry, SpeciesEntryReview


def create_species_entry_review(
    session: Session,
    *,
    species_entry_id: int,
    user_id: int,
    role: SpeciesEntryReviewRole,
    note: str | None = None,
) -> SpeciesEntryReview:
    """Persist a new review row under a species entry.

    :param session: Active SQLAlchemy session.
    :param species_entry_id: Target species entry (from the URL path).
    :param user_id: Reviewer id, taken from the authenticated caller.
    :param role: The review role (e.g. ``curator``).
    :param note: Optional free-text explanation.
    :returns: The persisted ``SpeciesEntryReview``.
    :raises NotFoundError: If the species entry does not exist.
    :raises DomainError: If a review by this ``(user, role)`` already exists
        for the target species entry.
    """
    if session.get(SpeciesEntry, species_entry_id) is None:
        raise NotFoundError(f"SpeciesEntry {species_entry_id} not found")

    existing = session.scalar(
        select(SpeciesEntryReview).where(
            SpeciesEntryReview.species_entry_id == species_entry_id,
            SpeciesEntryReview.user_id == user_id,
            SpeciesEntryReview.role == role,
        )
    )
    if existing is not None:
        raise DomainError(
            f"A '{role.value}' review by the current user already exists for "
            f"species entry {species_entry_id}"
        )

    review = SpeciesEntryReview(
        species_entry_id=species_entry_id,
        user_id=user_id,
        role=role,
        note=note,
    )
    session.add(review)
    session.flush()
    return review


def list_species_entry_reviews(
    session: Session,
    *,
    species_entry_id: int,
) -> list[SpeciesEntryReview]:
    """Return reviews attached to a species entry, newest first.

    :raises NotFoundError: If the species entry does not exist, so callers
        can distinguish "no reviews yet" from "unknown entry".
    """
    if session.get(SpeciesEntry, species_entry_id) is None:
        raise NotFoundError(f"SpeciesEntry {species_entry_id} not found")

    rows = session.scalars(
        select(SpeciesEntryReview)
        .where(SpeciesEntryReview.species_entry_id == species_entry_id)
        .order_by(
            SpeciesEntryReview.created_at.desc(),
            SpeciesEntryReview.id.desc(),
        )
    ).all()
    return list(rows)
