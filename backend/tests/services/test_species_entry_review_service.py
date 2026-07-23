"""Service-layer tests for species-entry review create/list."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.errors import DomainError, NotFoundError
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MoleculeKind,
    SpeciesEntryReviewRole,
    StereoKind,
)
from app.db.models.species import Species, SpeciesEntry
from app.services.species_entry_review import (
    create_species_entry_review,
    list_species_entry_reviews,
)

_REVIEW_ENTRY_COUNTER = 0


def _make_species_entry(session) -> int:
    global _REVIEW_ENTRY_COUNTER
    species = session.scalar(
        select(Species)
        .where(
            Species.kind == MoleculeKind.molecule,
            Species.smiles == "[H]",
            Species.charge == 0,
            Species.multiplicity == 2,
            Species.stereo_kind == StereoKind.achiral,
        )
        .order_by(Species.id)
        .limit(1)
    )
    if species is None:
        species = Species(
            kind=MoleculeKind.molecule,
            smiles="[H]",
            inchi_key="YZCKVEUIGOORGS-UHFFFAOYSA-N",
            charge=0,
            multiplicity=2,
            stereo_kind=StereoKind.achiral,
        )
        session.add(species)
        session.flush()
    _REVIEW_ENTRY_COUNTER += 1
    entry = SpeciesEntry(
        species_id=species.id,
        stereo_label=f"review-fixture-{_REVIEW_ENTRY_COUNTER}",
    )
    session.add(entry)
    session.flush()
    return entry.id


def _make_user(session, username: str = "reviewer-a") -> int:
    user = AppUser(username=username, role=AppUserRole.curator)
    session.add(user)
    session.flush()
    return user.id


class TestCreateSpeciesEntryReview:
    def test_persists_review_row(self, db_session):
        entry_id = _make_species_entry(db_session)
        user_id = _make_user(db_session)

        review = create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_id,
            role=SpeciesEntryReviewRole.curator,
            note="ok",
        )

        assert review.id is not None
        assert review.species_entry_id == entry_id
        assert review.user_id == user_id
        assert review.role is SpeciesEntryReviewRole.curator
        assert review.note == "ok"

    def test_missing_species_entry_raises_not_found(self, db_session):
        user_id = _make_user(db_session)
        with pytest.raises(NotFoundError):
            create_species_entry_review(
                db_session,
                species_entry_id=999_999,
                user_id=user_id,
                role=SpeciesEntryReviewRole.curator,
            )

    def test_duplicate_same_user_and_role_rejected(self, db_session):
        entry_id = _make_species_entry(db_session)
        user_id = _make_user(db_session)
        create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_id,
            role=SpeciesEntryReviewRole.curator,
        )
        with pytest.raises(DomainError):
            create_species_entry_review(
                db_session,
                species_entry_id=entry_id,
                user_id=user_id,
                role=SpeciesEntryReviewRole.curator,
            )

    def test_same_user_different_roles_coexist(self, db_session):
        entry_id = _make_species_entry(db_session)
        user_id = _make_user(db_session)
        for role in (
            SpeciesEntryReviewRole.curator,
            SpeciesEntryReviewRole.reviewer,
            SpeciesEntryReviewRole.validator,
        ):
            create_species_entry_review(
                db_session,
                species_entry_id=entry_id,
                user_id=user_id,
                role=role,
            )
        rows = list_species_entry_reviews(
            db_session, species_entry_id=entry_id
        )
        assert {r.role for r in rows} == {
            SpeciesEntryReviewRole.curator,
            SpeciesEntryReviewRole.reviewer,
            SpeciesEntryReviewRole.validator,
        }

    def test_different_users_same_role_coexist(self, db_session):
        """Append-only across reviewers: two curators can both curate an entry."""
        entry_id = _make_species_entry(db_session)
        user_a = _make_user(db_session, "reviewer-a")
        user_b = _make_user(db_session, "reviewer-b")
        create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_a,
            role=SpeciesEntryReviewRole.curator,
        )
        create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_b,
            role=SpeciesEntryReviewRole.curator,
        )
        rows = list_species_entry_reviews(
            db_session, species_entry_id=entry_id
        )
        assert {r.user_id for r in rows} == {user_a, user_b}


class TestListSpeciesEntryReviews:
    def test_empty_list_for_entry_with_no_reviews(self, db_session):
        entry_id = _make_species_entry(db_session)
        assert list_species_entry_reviews(
            db_session, species_entry_id=entry_id
        ) == []

    def test_missing_species_entry_raises_not_found(self, db_session):
        with pytest.raises(NotFoundError):
            list_species_entry_reviews(
                db_session, species_entry_id=999_999
            )

    def test_newest_first_ordering(self, db_session):
        entry_id = _make_species_entry(db_session)
        user_id = _make_user(db_session)
        first = create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_id,
            role=SpeciesEntryReviewRole.curator,
        )
        second = create_species_entry_review(
            db_session,
            species_entry_id=entry_id,
            user_id=user_id,
            role=SpeciesEntryReviewRole.reviewer,
        )

        rows = list_species_entry_reviews(
            db_session, species_entry_id=entry_id
        )
        # Same second-granularity created_at under the txn clock; the
        # secondary id.desc() ordering guarantees newer-id-first.
        assert [r.id for r in rows] == [second.id, first.id]

    def test_is_scoped_to_requested_entry(self, db_session):
        entry_a = _make_species_entry(db_session)
        # Create a second distinct species + entry
        species_b = Species(
            kind=MoleculeKind.molecule,
            smiles="[4He]",
            inchi_key="SWQJXJOGLNCZEY-UHFFFAOYSA-N",
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(species_b)
        db_session.flush()
        entry_b = SpeciesEntry(species_id=species_b.id)
        db_session.add(entry_b)
        db_session.flush()

        user_id = _make_user(db_session)
        create_species_entry_review(
            db_session,
            species_entry_id=entry_a,
            user_id=user_id,
            role=SpeciesEntryReviewRole.curator,
        )
        create_species_entry_review(
            db_session,
            species_entry_id=entry_b.id,
            user_id=user_id,
            role=SpeciesEntryReviewRole.curator,
        )

        a_rows = list_species_entry_reviews(
            db_session, species_entry_id=entry_a
        )
        b_rows = list_species_entry_reviews(
            db_session, species_entry_id=entry_b.id
        )
        assert len(a_rows) == 1 and a_rows[0].species_entry_id == entry_a
        assert len(b_rows) == 1 and b_rows[0].species_entry_id == entry_b.id
