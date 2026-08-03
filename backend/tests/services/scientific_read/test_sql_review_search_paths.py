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
the two ``…_above_the_bind_parameter_cap`` tests drive each search past it.

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
from collections import defaultdict
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
    SpeciesEntryStateKind,
    SubmissionRecordType,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reads.scientific_calculation_search import (
    CalculationsSearchRequest,
)
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.schemas.reads.scientific_species import SpeciesSearchRequest
from app.services.scientific_read.calculations_search import (
    _QUALITY_RANK,
    search_calculations,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    visible_statuses,
)
from app.services.scientific_read.species import search_species
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
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

#: The rank a record with no visible entry sorts at in ``search_species``.
_NO_ENTRY_RANK = max(REVIEW_RANK.values()) + 1

#: Explicit ``species_entry.id`` values for the entry-order fixture, chosen
#: far above the sequence so nothing else in the test transaction collides
#: with them.
_ENTRY_ID_BASE = 9_000_000

#: ``(id offset, stereo label)`` pairs for the entry-order fixture, and the
#: reason the fixture is a table rather than a range. Both orders a plan can
#: cheaply produce have to disagree with id order, or an unordered read comes
#: back sorted by luck and the test cannot fail:
#:
#: * they are written to the heap in *this* order, so a sequential or bitmap
#:   scan returns the ids shuffled;
#: * their stereo labels ascend as their ids descend, so an index scan over
#:   ``uq_species_entry_species_id`` — which is ``(species_id, stereo_label,
#:   …)``, and is what the planner actually picks here — returns them
#:   reversed.
_ENTRY_ORDER_FIXTURE = (
    (5, "stereo-c"),
    (2, "stereo-f"),
    (7, "stereo-a"),
    (1, "stereo-g"),
    (6, "stereo-b"),
    (3, "stereo-e"),
    (4, "stereo-d"),
)

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

#: The trust knobs the two endpoints expose, as a full cross product. The
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


def test_species_search_above_the_bind_parameter_cap(db_session):
    """A species search matching more candidates than the cap must work.

    ``inchi_key`` is not unique on ``species`` (only ``(smiles, charge,
    multiplicity)`` is), so one key legitimately fans out across many
    species rows — protomers, tautomer assignments, isotopologue parents.
    """
    inchi_key = next_inchi_key("CAPSPC")
    _bulk_insert_species_with_entries(
        db_session, inchi_key=inchi_key, count=OVER_CAP
    )

    response = search_species(
        db_session, SpeciesSearchRequest(inchi_key=inchi_key, limit=50)
    )

    expected = _reference_species_order(
        db_session,
        inchi_key=inchi_key,
        visible=visible_statuses(
            min_review_status=None,
            include_rejected=False,
            include_deprecated=False,
        ),
    )
    assert response.pagination.total == len(expected) == OVER_CAP
    assert [r.species_id for r in response.records] == expected[:50]
    assert response.review_summary.not_reviewed == OVER_CAP
    # Every species still carries its entry — an inner join would have
    # returned the species with an empty ``entries`` list instead.
    assert all(len(r.entries) == 1 for r in response.records)


# ---------------------------------------------------------------------------
# Equivalence with the Python semantics being replaced
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(
    ("min_review_status", "include_rejected", "include_deprecated"),
    _TRUST_COMBINATIONS,
)
def test_species_search_equals_python_reference(
    db_session, min_review_status, include_rejected, include_deprecated
):
    inchi_key = _mixed_species_corpus(db_session)
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
    )
    expected = _reference_species_order(
        db_session, inchi_key=inchi_key, visible=visible
    )

    response = search_species(
        db_session,
        SpeciesSearchRequest(
            inchi_key=inchi_key,
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            limit=100,
        ),
    )

    assert [r.species_id for r in response.records] == expected
    assert response.pagination.total == len(expected)


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


@pytest.mark.parametrize("offset", [0, 1, 3, 7, 100])
def test_species_search_paginates_the_same_ordering(db_session, offset):
    inchi_key = _mixed_species_corpus(db_session)
    expected = _reference_species_order(
        db_session,
        inchi_key=inchi_key,
        visible=visible_statuses(
            min_review_status=None,
            include_rejected=True,
            include_deprecated=True,
        ),
    )

    response = search_species(
        db_session,
        SpeciesSearchRequest(
            inchi_key=inchi_key,
            include_rejected=True,
            include_deprecated=True,
            offset=offset,
            limit=4,
        ),
    )

    assert [r.species_id for r in response.records] == expected[
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


def test_species_with_no_review_row_are_kept_and_ranked_not_reviewed(
    db_session,
):
    inchi_key = next_inchi_key("NOJOIN")
    approved_sp = make_species(db_session, smiles="[CH4:3]", inchi_key=inchi_key)
    approved_entry = make_species_entry(db_session, approved_sp)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=approved_entry.id,
        status=RecordReviewStatus.approved,
    )
    no_row_sp = make_species(db_session, smiles="[CH4:4]", inchi_key=inchi_key)
    no_row_entry = make_species_entry(db_session, no_row_sp)
    entryless_sp = make_species(
        db_session, smiles="[CH4:5]", inchi_key=inchi_key
    )

    response = search_species(
        db_session, SpeciesSearchRequest(inchi_key=inchi_key)
    )

    assert [r.species_id for r in response.records] == [
        approved_sp.id,
        no_row_sp.id,
        entryless_sp.id,  # no entries at all — sorts last, still returned
    ]
    assert [e.species_entry_id for e in response.records[1].entries] == [
        no_row_entry.id
    ]
    assert (
        response.records[1].entries[0].review.status
        is RecordReviewStatus.not_reviewed
    )
    assert response.records[2].entries == []
    assert response.review_summary.not_reviewed == 1
    assert response.review_summary.approved == 1


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


def test_species_entries_within_a_record_are_ordered_by_id(db_session):
    """The ``entries`` list of a species record is ordered, not plan-ordered.

    Before the entry load was scoped to the page it had no ``ORDER BY`` at
    all, so the order of ``record.entries`` was whatever the plan happened to
    produce. It is now ``species_entry.id`` ascending. That is a contract a
    client can page against and diff against, and it is exactly the kind of
    ``ORDER BY`` that looks redundant to a later reader — nothing else in the
    suite notices if it goes, because on a fixture built by
    :func:`make_species_entry` the heap order and the id order agree.

    So this fixture makes them disagree, on both of the orders a plan can
    produce for free — see :data:`_ENTRY_ORDER_FIXTURE`.
    """
    inchi_key = next_inchi_key("ENTORD")
    species = make_species(db_session, smiles="[CH4:20]", inchi_key=inchi_key)

    for offset, stereo_label in _ENTRY_ORDER_FIXTURE:
        db_session.execute(
            text(
                "INSERT INTO species_entry (id, species_id, kind, "
                "electronic_state_kind, stereo_label) "
                "VALUES (:id, :species_id, 'minimum', 'ground', :label)"
            ),
            {
                # Explicit ids, far above the sequence so nothing else in
                # the transaction can collide with them.
                "id": _ENTRY_ID_BASE + offset,
                "species_id": species.id,
                # ``uq_species_entry_species_id`` treats NULLs as equal, so
                # the entries of one species must differ somewhere. The
                # stereo label is the cheapest axis and is not filtered on.
                "label": stereo_label,
            },
        )

    physical_order = list(
        db_session.scalars(
            select(SpeciesEntry.id).where(SpeciesEntry.species_id == species.id)
        )
    )
    assert physical_order != sorted(physical_order), (
        "fixture no longer discriminates: the database returns these entries "
        "in id order with no ORDER BY, so this test could not fail"
    )

    response = search_species(
        db_session, SpeciesSearchRequest(inchi_key=inchi_key)
    )

    (record,) = response.records
    assert [e.species_entry_id for e in record.entries] == sorted(
        _ENTRY_ID_BASE + offset for offset, _ in _ENTRY_ORDER_FIXTURE
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


def _rows_in_chunks(session, stmt_for, ids):
    """Run ``stmt_for(chunk)`` over ``ids`` in cap-safe chunks."""
    ids = list(ids)
    rows: list = []
    for start in range(0, len(ids), 10_000):
        rows.extend(session.execute(stmt_for(ids[start : start + 10_000])).all())
    return rows


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


def _reference_species_order(
    session, *, inchi_key: str, visible: set[RecordReviewStatus]
) -> list[int]:
    """Order species exactly as the Python path did.

    A species is ranked by its *best visible* entry; one with no visible
    entry keeps its place in the result and sorts after every species that
    has one.
    """
    species_rows = session.execute(
        select(Species.id, Species.created_at).where(
            Species.inchi_key == inchi_key
        )
    ).all()
    species_ids = [row.id for row in species_rows]
    entry_rows = _rows_in_chunks(
        session,
        lambda chunk: select(
            SpeciesEntry.id, SpeciesEntry.species_id
        ).where(SpeciesEntry.species_id.in_(chunk)),
        species_ids,
    )
    badges = _badges_in_chunks(
        session,
        record_type=SubmissionRecordType.species_entry,
        ids=[row.id for row in entry_rows],
    )

    visible_entries: dict[int, list[int]] = defaultdict(list)
    for row in entry_rows:
        if badges[row.id].status in visible:
            visible_entries[row.species_id].append(row.id)

    keyed = []
    for row in species_rows:
        entries = visible_entries.get(row.id, [])
        best_rank = min(
            (REVIEW_RANK[badges[eid].status] for eid in entries),
            default=_NO_ENTRY_RANK,
        )
        keyed.append(
            (
                best_rank,
                -(1 if entries else 0),
                -row.created_at.timestamp(),
                -row.id,
                row.id,
            )
        )
    keyed.sort()
    return [item[-1] for item in keyed]


# ---------------------------------------------------------------------------
# Corpus builders
# ---------------------------------------------------------------------------


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


def _mixed_species_corpus(session) -> str:
    """Species sharing an InChIKey, one per (review flavour + entryless).

    Includes species with two entries of different statuses, so the
    "rank by best visible entry" rule is exercised rather than assumed.
    """
    inchi_key = next_inchi_key("MIXSPC")
    base = datetime(2026, 3, 1, 12, 0, 0)
    smiles_counter = itertools.count(100)

    def _new_species(day_offset: int) -> Species:
        species = make_species(
            session,
            smiles=f"[CH4:{next(smiles_counter)}]",
            inchi_key=inchi_key,
        )
        species.created_at = base + timedelta(days=day_offset)
        session.flush()
        return species

    for index, flavour in enumerate(_REVIEW_FLAVOURS):
        species = _new_species(index % 3)
        entry = make_species_entry(session, species)
        if flavour is not None:
            set_review(
                session,
                record_type=SubmissionRecordType.species_entry,
                record_id=entry.id,
                status=flavour,
            )

    # A species whose best entry is approved but which also carries a
    # rejected one — the two must not be conflated.
    pair = _new_species(1)
    first = make_species_entry(session, pair)
    second = make_species_entry(
        session, pair, electronic_state_kind=SpeciesEntryStateKind.excited
    )
    set_review(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_id=first.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_id=second.id,
        status=RecordReviewStatus.rejected,
    )

    # A species with no entries at all.
    _new_species(2)
    return inchi_key


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


def _bulk_insert_species_with_entries(session, *, inchi_key: str, count: int):
    """Insert ``count`` species sharing ``inchi_key``, one entry each.

    The SMILES are atom-mapped methanes: distinct (so ``uq_species_identity``
    holds), and RDKit-parseable, so the ``mol_formula`` expression index on
    ``species`` builds without emitting one warning per row.
    """
    session.execute(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "SELECT 'molecule', '[CH4:' || (g + 1000000) || ']', :inchi_key, "
            "0, 1, 'achiral' FROM generate_series(1, :count) g"
        ),
        {"inchi_key": inchi_key, "count": count},
    )
    session.execute(
        text(
            "INSERT INTO species_entry "
            "(species_id, kind, electronic_state_kind) "
            "SELECT id, 'minimum', 'ground' FROM species "
            "WHERE inchi_key = :inchi_key"
        ),
        {"inchi_key": inchi_key},
    )
    _analyze(session, "species", "species_entry", "record_review")
