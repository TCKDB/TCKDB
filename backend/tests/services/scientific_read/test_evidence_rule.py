"""The evidence rule helper: curated withholds rejected/deprecated linked evidence, and shows everything else.

Exploratory withholds nothing as evidence. A record with no review row reads as ``not_reviewed`` and is visible.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus, SubmissionRecordType
from app.services.scientific_read.common import (
    WITHHELD_EVIDENCE_STATUSES,
    evidence_visible_statuses,
    evidence_withheld_under_profile,
    ids_withheld_as_evidence,
    linked_evidence_predicate,
)
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    release_backed_profile,
    reset_current_read_profile,
    set_current_read_profile,
)

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
S = RecordReviewStatus
CALC = SubmissionRecordType.calculation
WITHHELD = {S.rejected, S.deprecated}


@pytest.mark.parametrize("status", list(S))
def test_curated_withholds_only_rejected_and_deprecated(status):
    assert evidence_withheld_under_profile(status, profile=CURATED) is (status in WITHHELD)


@pytest.mark.parametrize("status", list(S))
def test_exploratory_withholds_nothing_as_evidence(status):
    assert evidence_withheld_under_profile(status, profile=EXPLORATORY) is False


def test_visible_statuses_per_profile():
    assert evidence_visible_statuses(profile=CURATED) == {S.not_reviewed, S.under_review, S.approved}
    assert evidence_visible_statuses(profile=EXPLORATORY) == frozenset(S)
    assert WITHHELD_EVIDENCE_STATUSES == WITHHELD


def test_release_backed_profile_follows_the_curated_rule():
    class Release:
        public_ref = "rel_x"

    backed = release_backed_profile(Release())
    assert evidence_withheld_under_profile(S.rejected, profile=backed) is True
    assert evidence_withheld_under_profile(S.not_reviewed, profile=backed) is False


def test_default_profile_is_the_requests():
    assert evidence_withheld_under_profile(S.rejected) is False  # no request: exploratory
    token = set_current_read_profile(CURATED)
    try:
        assert evidence_withheld_under_profile(S.rejected) is True
        assert evidence_withheld_under_profile(S.under_review) is False
    finally:
        reset_current_read_profile(token)


@pytest.fixture
def seeded(db_session):
    """Calculation ids at each status, plus one with no review row, as ``{label: id}``."""
    from ._factories import make_calculation, make_species, make_species_entry, set_review

    entry = make_species_entry(db_session, make_species(db_session))
    ids: dict[str, int] = {}
    for status in S:
        calc = make_calculation(db_session, species_entry_id=entry.id)
        set_review(db_session, record_type=CALC, record_id=calc.id, status=status)
        ids[status.value] = calc.id
    ids["none"] = make_calculation(db_session, species_entry_id=entry.id).id
    return ids


def test_sql_predicate_selects_exactly_the_visible_ids(db_session, seeded):
    pred = linked_evidence_predicate(Calculation.id, CALC, profile=CURATED)
    got = set(db_session.scalars(select(Calculation.id).where(Calculation.id.in_(seeded.values())).where(pred)))
    assert got == {seeded[k] for k in ("approved", "under_review", "not_reviewed", "none")}


def test_sql_predicate_is_none_under_exploratory(seeded):
    assert linked_evidence_predicate(Calculation.id, CALC, profile=EXPLORATORY) is None


def test_ids_withheld_exactly_the_rejected_and_deprecated(db_session, seeded):
    got = ids_withheld_as_evidence(db_session, record_type=CALC, record_ids=seeded.values(), profile=CURATED)
    assert got == {seeded["rejected"], seeded["deprecated"]}


def test_ids_withheld_is_scoped_to_the_record_type(db_session, seeded):
    got = ids_withheld_as_evidence(
        db_session, record_type=SubmissionRecordType.thermo, record_ids=seeded.values(), profile=CURATED
    )
    assert got == set()


def test_ids_withheld_under_exploratory_and_empty_issue_no_query(db_session, seeded):
    assert ids_withheld_as_evidence(db_session, record_type=CALC, record_ids=seeded.values(), profile=EXPLORATORY) == set()
    assert ids_withheld_as_evidence(db_session, record_type=CALC, record_ids=[], profile=CURATED) == set()
