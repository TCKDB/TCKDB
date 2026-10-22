"""The browse order has one definition (docs/specs/browse_order.md) and every copy derives from it.

Display order: review rank, then deposition time (``created_at``) newest first, then a unique tie-breaker. The first
record listed is display order, never a scientific preference. ``under_review`` ranks EQUAL to ``not_reviewed``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.db.models.species import SpeciesEntry
from app.schemas.reads.scientific_common import (
    DISPLAY_ORDER_STATEMENT,
    ORDER_KEY_CATALOG,
    REVIEW_FLOOR_ORDER,
    REVIEW_RANK,
    browse_order,
    status_at_or_above,
)
from app.services.scientific_read.sql_review import join_review, review_rank_expr
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
    unique_smiles,
)

APP = Path(__file__).resolve().parents[3] / "app"


def test_review_rank_ranks_under_review_equal_to_not_reviewed():
    assert REVIEW_RANK[S.approved] == 0
    assert REVIEW_RANK[S.under_review] == REVIEW_RANK[S.not_reviewed] == 1
    # Rejected and deprecated come after them, deprecated first (their relative order is unchanged).
    assert REVIEW_RANK[S.not_reviewed] < REVIEW_RANK[S.deprecated] < REVIEW_RANK[S.rejected]
    assert set(REVIEW_RANK) == set(S)


def test_a_review_floor_still_tells_under_review_from_not_reviewed():
    """Display order ties the two; a visibility floor does not, so equal display rank widens no floor."""
    assert S.not_reviewed not in status_at_or_above(S.under_review)
    assert status_at_or_above(S.under_review) == {S.approved, S.under_review}
    assert REVIEW_FLOOR_ORDER[S.under_review] < REVIEW_FLOOR_ORDER[S.not_reviewed]


def _entries_with(db_session, statuses):
    entries = []
    for status in statuses:
        species = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BO"))
        entry = make_species_entry(db_session, species)
        db_session.flush()
        if status is not S.not_reviewed:
            set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=status)
        entries.append((entry, status))
    return entries


def test_the_sql_rank_expression_agrees_with_the_table(db_session):
    entries = _entries_with(db_session, list(S))
    stmt, review = join_review(select(SpeciesEntry.id), SpeciesEntry.id, SubmissionRecordType.species_entry)
    rows = db_session.execute(stmt.add_columns(review_rank_expr(review)).where(SpeciesEntry.id.in_([e.id for e, _ in entries])))
    by_id = {row[0]: row[1] for row in rows}
    for entry, status in entries:
        assert by_id[entry.id] == REVIEW_RANK[status], status


def test_the_structure_search_rank_is_generated_from_the_table(db_session):
    from app.services.scientific_read import structure_search

    for status in S:
        expr = structure_search._REVIEW_RANK_EXPR.replace(structure_search._REVIEW_STATUS_EXPR, f"'{status.value}'")
        assert db_session.execute(text(f"SELECT {expr}")).scalar_one() == REVIEW_RANK[status], status
    missing = db_session.execute(text(f"SELECT {structure_search._REVIEW_RANK_EXPR.replace(structure_search._REVIEW_STATUS_EXPR, 'NULL')}")).scalar_one()
    assert missing is not None


def test_no_module_restates_the_rank_as_a_hand_copied_case():
    """A second copy of the table is how the two drift. The only place a status maps to a number is the table."""
    pattern = re.compile(r"WHEN\s+'(?:approved|under_review|not_reviewed|deprecated|rejected)'\s+THEN", re.IGNORECASE)
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py") if pattern.search(p.read_text())]
    assert offenders == []


def test_every_order_key_is_explained_and_the_echo_is_structured():
    echo = browse_order("review_rank", "created_at", "id")
    assert [k.key for k in echo.keys] == ["review_rank", "created_at", "id"]
    assert [k.direction for k in echo.keys] == ["asc", "desc", "desc"]
    assert echo.kind == "display_order"
    assert echo.statement == DISPLAY_ORDER_STATEMENT and "not a scientific preference" in echo.statement
    created = next(k for k in echo.keys if k.key == "created_at")
    assert "deposition" in created.meaning
    for name, (_direction, meaning) in ORDER_KEY_CATALOG.items():
        assert meaning, name
    with pytest.raises(KeyError):
        browse_order("not_a_key")


def _thermo_at(db_session, entry, *, age_days, status):
    t = make_thermo_scalar(db_session, species_entry=entry)
    t.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=age_days)
    db_session.flush()
    if status is not S.not_reviewed:
        set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=t.id, status=status)
    return t


def test_analytics_lists_review_rank_then_deposition_time_then_id(db_session):
    """Analytics used ``id DESC`` as a stand-in for newest. Deposition time is ``created_at``; id only breaks ties."""
    from app.schemas.reads.scientific_analytics import ThermoAnalyticsRequest
    from app.services.scientific_read.analytics import search_thermo_analytics

    species = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("AN"))
    entry = make_species_entry(db_session, species)
    db_session.flush()
    # Inserted so that id order contradicts deposition order, and under_review ties not_reviewed.
    older_unreviewed = _thermo_at(db_session, entry, age_days=30, status=S.not_reviewed)
    newest_unreviewed = _thermo_at(db_session, entry, age_days=1, status=S.not_reviewed)
    middle_under_review = _thermo_at(db_session, entry, age_days=10, status=S.under_review)
    approved_oldest = _thermo_at(db_session, entry, age_days=90, status=S.approved)
    db_session.flush()

    response = search_thermo_analytics(db_session, request=ThermoAnalyticsRequest())
    mine = {t.public_ref for t in (older_unreviewed, newest_unreviewed, middle_under_review, approved_oldest)}
    got = [r.thermo_ref for r in response.records if r.thermo_ref in mine]
    assert got == [
        approved_oldest.public_ref,  # approved first, however old
        newest_unreviewed.public_ref,  # then under_review and not_reviewed together, newest deposited first
        middle_under_review.public_ref,
        older_unreviewed.public_ref,
    ]
    assert [k.key for k in response.request.sort.keys] == ["review_rank", "created_at", "id"]


def test_analytics_keyset_traversal_follows_the_same_order(db_session):
    from app.schemas.reads.scientific_analytics import ThermoAnalyticsRequest
    from app.services.scientific_read.analytics import search_thermo_analytics

    species = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("AK"))
    entry = make_species_entry(db_session, species)
    db_session.flush()
    made = [_thermo_at(db_session, entry, age_days=age, status=status) for age, status in
            [(5, S.not_reviewed), (4, S.under_review), (3, S.not_reviewed), (2, S.approved), (1, S.under_review)]]
    mine = {t.public_ref for t in made}
    whole = [r.thermo_ref for r in search_thermo_analytics(db_session, request=ThermoAnalyticsRequest(limit=200)).records
             if r.thermo_ref in mine]

    walked: list[str] = []
    cursor = None
    while True:
        page = search_thermo_analytics(db_session, request=ThermoAnalyticsRequest(limit=2, cursor=cursor))
        walked.extend(r.thermo_ref for r in page.records if r.thermo_ref in mine)
        cursor = page.pagination.next_cursor if hasattr(page.pagination, "next_cursor") else getattr(page, "next_cursor", None)
        if not cursor:
            break
    assert walked == whole
    assert len(walked) == len(made)
