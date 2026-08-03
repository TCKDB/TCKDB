"""Review visibility/ranking must be SQL-side on the two hot search paths.

Two properties are tested here, and both are needed: one of them is a
correctness bug and the other is what a wrong fix for it looks like.

**The cap.** The Python path bulk-loads review badges with
``RecordReview.record_id.in_(candidate_ids)``, which SQLAlchemy renders as
one bind parameter per candidate. PostgreSQL's *wire protocol* — not any
server setting — caps a single statement at 65,535 parameters, so a search
whose candidate set crosses that boundary does not merely slow down: psycopg
refuses to send the statement and the API answers ``503
database_unavailable``. :func:`test_bind_parameter_cap_is_the_wire_protocol_limit`
pins that boundary as a measured fact rather than a remembered constant, and
the ``…_above_the_bind_parameter_cap`` test drives the search past it.

**Equivalence.** Moving the filter into SQL is easy to get subtly wrong in
ways no small fixture notices — an inner join silently drops every record
with no ``record_review`` row (on this corpus, most of them), and a missing
tiebreak makes the page order depend on the plan. So the same corpus is
answered twice: once by the service and once by a reference implementation
that reproduces the pre-SQL Python semantics directly, over every trust
combination the endpoints expose. The two must agree on the *sequence* of
records, not merely the set.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_calculation_search import (
    CalculationsSearchRequest,
)
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.services.scientific_read.calculations_search import (
    _QUALITY_RANK,
    search_calculations,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    visible_statuses,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    set_review,
)

#: Measured in :func:`test_bind_parameter_cap_is_the_wire_protocol_limit`.
#: The number of bind parameters PostgreSQL's extended-query protocol
#: accepts in one statement.
MAX_BIND_PARAMETERS = 65_535

#: ``fetch_review_badges`` spends one parameter on ``record_type`` and one
#: per candidate id, so this many candidates is the last set it can load.
MAX_BADGE_IDS = MAX_BIND_PARAMETERS - 1

#: Comfortably past the cap without being gratuitous — the corpus builders
#: below insert this many rows per search path.
OVER_CAP = MAX_BADGE_IDS + 65

#: Every review flavour a record can be in, including "no ``record_review``
#: row at all" — the one an inner join would drop.
_REVIEW_FLAVOURS: tuple[RecordReviewStatus | None, ...] = (
    RecordReviewStatus.approved,
    RecordReviewStatus.under_review,
    RecordReviewStatus.not_reviewed,
    RecordReviewStatus.deprecated,
    RecordReviewStatus.rejected,
    None,
)

#: The trust knobs the endpoint exposes, as a full cross product. The
#: sort is server-fixed (client ``sort=`` is rejected), so the axes that can
#: change which records come back, and in what order, are exactly these.
_TRUST_COMBINATIONS = tuple(
    itertools.product(
        (None, *_REVIEW_FLAVOURS[:5]),  # min_review_status
        (False, True),  # include_rejected
        (False, True),  # include_deprecated
    )
)


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


def test_bind_parameter_cap_is_the_wire_protocol_limit(db_session):
    """Establish the real boundary, and that it is the wire protocol's.

    ``max_locks_per_transaction`` and friends are server settings that can be
    raised; this one cannot. It is a 16-bit field in the Bind message, so no
    amount of tuning moves it, which is why the fix has to be "do not send
    the ids" rather than "send them differently".
    """
    # Not a server GUC — there is no setting to read, and asking for one is
    # the mistake this assertion exists to rule out.
    assert (
        db_session.execute(
            text(
                "SELECT count(*) FROM pg_settings "
                "WHERE name LIKE '%%parameter%%' AND setting = '65535'"
            )
        ).scalar()
        == 0
    )

    # One parameter for ``record_type`` plus one per id: the last size that
    # fits exactly fills the message.
    badges = fetch_review_badges(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_ids=range(1, MAX_BADGE_IDS + 1),
    )
    assert len(badges) == MAX_BADGE_IDS

    with pytest.raises(OperationalError) as excinfo:
        fetch_review_badges(
            db_session,
            record_type=SubmissionRecordType.calculation,
            record_ids=range(1, MAX_BADGE_IDS + 2),
        )
    assert "number of parameters must be between 0 and 65535" in str(
        excinfo.value
    )


def test_calculations_search_above_the_bind_parameter_cap(db_session):
    """A calculation search matching more candidates than the cap must work.

    ``calculation_type=sp`` matches 119,701 rows on the Stage 4 benchmark
    corpus. Whatever the page size, the candidate set must never become bind
    parameters.
    """
    species_entry = make_species_entry(
        db_session, make_species(db_session, smiles="[CH4:1]")
    )
    _bulk_insert_calculations(
        db_session, species_entry_id=species_entry.id, count=OVER_CAP
    )

    response = search_calculations(
        db_session,
        CalculationsSearchRequest(
            calculation_type=CalculationType.sp, limit=50
        ),
    )

    expected = _reference_calculation_order(
        db_session,
        calculation_type=CalculationType.sp,
        visible=visible_statuses(
            min_review_status=None,
            include_rejected=False,
            include_deprecated=False,
        ),
    )
    assert response.pagination.total == len(expected) >= OVER_CAP
    assert [r.calculation.calculation_id for r in response.records] == expected[:50]
    # Nothing is reviewed, and nothing was dropped for lacking a review row.
    assert response.review_summary.not_reviewed == len(expected)
    assert response.review_summary.total == len(expected)


@pytest.mark.parametrize(
    ("min_review_status", "include_rejected", "include_deprecated"),
    _TRUST_COMBINATIONS,
)
def test_calculations_search_equals_python_reference(
    db_session, min_review_status, include_rejected, include_deprecated
):
    lot = _mixed_calculation_corpus(db_session)
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    expected = _reference_calculation_order(
        db_session,
        calculation_type=CalculationType.sp,
        visible=visible,
        method=lot.method,
    )

    response = search_calculations(
        db_session,
        CalculationsSearchRequest(
            calculation_type=CalculationType.sp,
            method=lot.method,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            limit=100,
        ),
    )

    assert [r.calculation.calculation_id for r in response.records] == expected
    assert response.pagination.total == len(expected)
    assert response.review_summary.total == len(expected)


@pytest.mark.parametrize("offset", [0, 1, 3, 7, 100])
def test_calculations_search_paginates_the_same_ordering(db_session, offset):
    lot = _mixed_calculation_corpus(db_session)
    expected = _reference_calculation_order(
        db_session,
        calculation_type=CalculationType.sp,
        visible=visible_statuses(
            min_review_status=None,
            include_rejected=True,
            include_deprecated=True,
        ),
        method=lot.method,
    )

    response = search_calculations(
        db_session,
        CalculationsSearchRequest(
            calculation_type=CalculationType.sp,
            method=lot.method,
            include_rejected=True,
            include_deprecated=True,
            offset=offset,
            limit=4,
        ),
    )

    assert [r.calculation.calculation_id for r in response.records] == expected[
        offset : offset + 4
    ]
    assert response.pagination.total == len(expected)


def test_calculations_with_no_review_row_are_kept_and_ranked_not_reviewed(
    db_session,
):
    """The inner-join trap, stated as its own assertion.

    A record with no ``record_review`` row is ``not_reviewed``: visible by
    default, ranked between ``under_review`` and ``deprecated``, and counted
    in the summary. It must not disappear, and it must not sort last.
    """
    lot = make_lot(db_session, method="sqlreview-nojoin", basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, smiles="[CH4:2]")
    )
    approved = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
    )
    no_row = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    deprecated = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=deprecated.id,
        status=RecordReviewStatus.deprecated,
    )

    response = search_calculations(
        db_session,
        CalculationsSearchRequest(
            calculation_type=CalculationType.sp,
            method=lot.method,
            include_deprecated=True,
        ),
    )

    assert [r.calculation.calculation_id for r in response.records] == [
        approved.id,
        no_row.id,
        deprecated.id,
    ]
    assert response.review_summary.not_reviewed == 1
    assert (
        response.records[1].calculation.review.status is RecordReviewStatus.not_reviewed
    )


def test_calculations_search_tiebreak_is_id_descending(db_session):
    """Equal rank, equal quality, equal ``created_at`` — ``id`` decides.

    Every row inserted in one transaction shares ``now()``, so this is the
    common case rather than a contrived one, and it is the axis a SQL
    ``ORDER BY`` will silently leave to the plan if the tiebreak is dropped.
    """
    lot = make_lot(db_session, method="sqlreview-tiebreak", basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, smiles="[CH4:6]")
    )
    ids = [
        make_calculation(
            db_session,
            type=CalculationType.sp,
            species_entry_id=entry.id,
            lot_id=lot.id,
        ).id
        for _ in range(6)
    ]
    created = db_session.scalars(
        select(Calculation.created_at).where(Calculation.id.in_(ids))
    ).all()
    assert len(set(created)) == 1, "fixture must produce a genuine tie"

    response = search_calculations(
        db_session,
        CalculationsSearchRequest(
            calculation_type=CalculationType.sp, method=lot.method
        ),
    )

    assert [r.calculation.calculation_id for r in response.records] == sorted(
        ids, reverse=True
    )


# ---------------------------------------------------------------------------
# Reference implementations — the Python semantics being replaced
# ---------------------------------------------------------------------------


def _badges_in_chunks(session, *, record_type: SubmissionRecordType, ids):
    """``fetch_review_badges`` over an id list of any size.

    The reference has to answer the same over-cap corpus the service does,
    so it cannot itself issue a single over-cap statement. Chunking changes
    nothing about the semantics being compared — the badge for a record does
    not depend on which other records were asked for — it only lets the
    oracle survive the defect it exists to detect.
    """
    ids = list(ids)
    badges: dict[int, object] = {}
    for start in range(0, len(ids), 10_000):
        badges.update(
            fetch_review_badges(
                session,
                record_type=record_type,
                record_ids=ids[start : start + 10_000],
            )
        )
    return badges


def _reference_calculation_order(
    session,
    *,
    calculation_type: CalculationType,
    visible: set[RecordReviewStatus],
    method: str | None = None,
) -> list[int]:
    """Filter and order calculations exactly as the Python path did.

    Deliberately a transcription of the pre-SQL implementation rather than a
    tidier equivalent: its value is that it shares no code with the SQL one.
    """
    stmt = select(
        Calculation.id, Calculation.created_at, Calculation.quality
    ).where(Calculation.type == calculation_type)
    if method is not None:
        from app.db.models.level_of_theory import LevelOfTheory

        stmt = stmt.join(
            LevelOfTheory, LevelOfTheory.id == Calculation.lot_id
        ).where(LevelOfTheory.method == method)
    stmt = stmt.where(Calculation.quality != CalculationQuality.rejected)

    rows = session.execute(stmt).all()
    badges = _badges_in_chunks(
        session,
        record_type=SubmissionRecordType.calculation,
        ids=[row.id for row in rows],
    )
    survivors = [row for row in rows if badges[row.id].status in visible]
    survivors.sort(
        key=lambda row: (
            REVIEW_RANK[badges[row.id].status],
            _QUALITY_RANK[row.quality],
            -row.created_at.timestamp(),
            -row.id,
        )
    )
    return [row.id for row in survivors]


def _mixed_calculation_corpus(session):
    """One ``sp`` calculation per (review flavour x quality), plus ties.

    ``created_at`` is spread over three distinct values so the ordering is
    exercised on that axis too, with duplicates so the ``id`` tiebreak still
    has to do work.
    """
    lot = make_lot(session, method="sqlreview-mixed", basis="def2tzvp")
    entry = make_species_entry(
        session, make_species(session, smiles="[CH4:10]")
    )
    base = datetime(2026, 3, 1, 12, 0, 0)
    qualities = (CalculationQuality.curated, CalculationQuality.raw)

    for index, (flavour, quality) in enumerate(
        itertools.product(_REVIEW_FLAVOURS, qualities)
    ):
        calc = make_calculation(
            session,
            type=CalculationType.sp,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        calc.quality = quality
        calc.created_at = base + timedelta(days=index % 3)
        session.flush()
        if flavour is not None:
            set_review(
                session,
                record_type=SubmissionRecordType.calculation,
                record_id=calc.id,
                status=flavour,
            )
    return lot


def _analyze(session, *tables: str) -> None:
    """Give the planner statistics for the rows this test just inserted.

    Rows written inside the test's own uncommitted transaction are invisible
    to autovacuum, so without this the planner sees empty tables, estimates
    one row everywhere, and picks nested loops that turn a 65,599-row join
    into a 65,599 x 65,599 one. That is a property of the fixture, not of the
    code under test, and every deployed database has statistics.

    It cannot mask what these tests are for: the bind-parameter overflow
    happens in psycopg while encoding the Bind message, before the statement
    reaches the planner at all, so no amount of statistics makes the Python
    path pass.
    """
    for table in tables:
        session.execute(text(f"ANALYZE {table}"))


def _bulk_insert_calculations(session, *, species_entry_id: int, count: int):
    """Insert ``count`` ``sp`` calculations in one server-side statement.

    ``INSERT … SELECT generate_series`` rather than an ORM loop: the point of
    the test is the candidate *count*, and it must not cost more than a
    second to reach it.
    """
    session.execute(
        text(
            "INSERT INTO calculation (type, species_entry_id) "
            "SELECT 'sp', :species_entry_id FROM generate_series(1, :count)"
        ),
        {"species_entry_id": species_entry_id, "count": count},
    )
    _analyze(session, "calculation", "record_review")


