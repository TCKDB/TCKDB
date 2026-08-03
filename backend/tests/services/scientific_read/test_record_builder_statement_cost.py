"""What one more record on a search page costs in SQL statements.

``build_record`` runs once per record, so anything it issues per record is
multiplied by the page size. The calculations search slices its page in three
statements and then fills it in one record at a time; when the filling-in was
fifteen round trips per record, a 50-record page cost about a thousand
statements (``backend/docs/benchmarks/README.md``). That is bounded by
``limit`` rather than by the corpus, so it was never a cliff — but it is the
kind of cost that reappears silently, one convenient ``session.scalar`` at a
time, and no assertion anywhere would notice.

This measures the *slope* — statements per additional record — rather than the
total, because the slope is the property that matters and it does not move
when the fixed overhead of a page changes.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models.common import CalculationType
from app.schemas.reads.scientific_calculation_search import (
    CalculationsSearchRequest,
)
from app.services.scientific_read.calculations_search import search_calculations
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
)

#: Statements ``build_record`` issues for one more record on a page of the
#: fixture below: default (empty) include set, and a calculation carrying no
#: software release, workflow tool, literature or submission link. Measured,
#: and it is exactly three:
#:
#: 1. the owner block (``species_entry`` joined to ``species``);
#: 2. the combined provenance/available-sections probe;
#: 3. the submission-link lookup.
#:
#: A fully-provenanced calculation costs a few more — on the Stage 4
#: benchmark corpus the marginal cost is six, not three — so this is a floor
#: on the real number, not the real number. That is the right thing to pin
#: anyway: the regression this guards against is a *fan-out*, one builder
#: quietly going back to a query per probe, which moves the floor by an order
#: of magnitude and not by one or two. Batching a builder across the page —
#: which means handing the shared record builder prefetched data — is how
#: this goes down; another per-record query is how it goes up.
STATEMENTS_PER_RECORD = 3

_SMALL_PAGE = 10
_LARGE_PAGE = 50


def _statements_for_page(session: Session, *, method: str, limit: int) -> int:
    """Count the SQL statements one search page issues."""
    count = 0
    engine = session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        count += 1

    try:
        response = search_calculations(
            session,
            CalculationsSearchRequest(
                calculation_type=CalculationType.sp,
                method=method,
                limit=limit,
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert len(response.records) == limit, "page must be full to be comparable"
    return count


def test_a_search_page_costs_a_fixed_few_statements_per_record(db_session):
    lot = make_lot(db_session, method="stmt-cost", basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, smiles="[CH4:40]")
    )
    for _ in range(_LARGE_PAGE + 5):
        make_calculation(
            db_session,
            type=CalculationType.sp,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )

    small = _statements_for_page(db_session, method=lot.method, limit=_SMALL_PAGE)
    large = _statements_for_page(db_session, method=lot.method, limit=_LARGE_PAGE)

    # ``<=``, not ``==``: STATEMENTS_PER_RECORD is a ceiling on this fixture's
    # slope, not the universal per-record cost. A calculation carrying more
    # provenance legitimately costs more -- adding a software release alone
    # takes this fixture from 3 to 4 -- so an equality assertion fails on a
    # fixture change that is not a regression, and, worse, fails on a genuine
    # improvement: batching the owner block is the named way this number goes
    # down, and equality would reject it. The regression this guards is a
    # per-record fan-out that moves the slope by an order of magnitude, which
    # a ceiling catches just as well.
    assert (large - small) <= STATEMENTS_PER_RECORD * (
        _LARGE_PAGE - _SMALL_PAGE
    ), (
        f"{(large - small) / (_LARGE_PAGE - _SMALL_PAGE)} statements per "
        f"record, expected at most {STATEMENTS_PER_RECORD} "
        f"({small} statements for {_SMALL_PAGE} records, "
        f"{large} for {_LARGE_PAGE})"
    )
