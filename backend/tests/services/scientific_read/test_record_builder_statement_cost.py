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

The ``include=trust`` cases live next door in
``test_trust_include_statement_cost.py``, one per search surface, because they
measure a different number: the *marginal* cost of one token rather than the
cost of a record. They belong to the same gate — anything that scales with the
page size on a search surface is the same defect — and they are separate only
so that each of the five surfaces can fail while the others pass.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models.common import CalculationType
from app.schemas.reads.scientific_calculation_search import (
    CalculationsSearchRequest,
)
from app.services.scientific_read.calculations_search import search_calculations
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_freq_result,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
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


# ---------------------------------------------------------------------------
# The cost of never missing a custody break
# ---------------------------------------------------------------------------

#: How many artifacts each calculation in the integrity fixture carries.
#: Two values, because the property under test is that the integrity load
#: does not scale with either of them.
_FEW_ARTIFACTS = 1
_MANY_ARTIFACTS = 6


def _integrity_statements_for_page(
    session: Session, *, method: str, limit: int
) -> tuple[int, int]:
    """Return (statements loading artifacts, statements loading their custody)."""
    artifacts = 0
    integrity = 0
    engine = session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal artifacts, integrity
        if "artifact_integrity_event" in statement:
            integrity += 1
        elif "FROM calculation_artifact" in statement:
            artifacts += 1

    try:
        response = search_calculations(
            session,
            CalculationsSearchRequest(
                calculation_type=CalculationType.sp,
                method=method,
                limit=limit,
                include=["artifacts"],
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert len(response.records) == limit, "page must be full to be comparable"
    return artifacts, integrity


def _page_with_artifacts(db_session, *, method: str, calcs: int, artifacts: int):
    lot = make_lot(db_session, method=method, basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, smiles=f"[CH4:{abs(hash(method)) % 90 + 5}]")
    )
    for index in range(calcs):
        calc = make_calculation(
            db_session,
            type=CalculationType.sp,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        for slot in range(artifacts):
            attach_artifact(
                db_session,
                calculation=calc,
                sha256=hashlib.sha256(
                    f"{method}-{index}-{slot}".encode()
                ).hexdigest(),
                filename=f"job-{slot}.log",
            )
    return lot


def test_the_integrity_eager_load_does_not_scale_with_artifact_count(db_session):
    """``lazy="selectin"`` was chosen over annotating eleven call sites.

    The reason is that a recorded custody break must not be invisible to
    whichever read path somebody forgot to annotate -- corruption
    discoverable only by the lucky reader is the failure the whole custody
    record exists to end. The price is one indexed ``sha256 IN (...)`` per
    artifact-loading statement, and *per statement* is the load-bearing
    half of that sentence. A drop to ``lazy="select"`` would turn it into
    one query per artifact row, which on a 50-record page of
    multi-artifact calculations is hundreds of round trips rather than
    one, and nothing else in the suite would notice.

    So this pins the shape, not a wall-clock number: the integrity load
    is a fixed cost per page, independent of how many artifacts the page's
    calculations carry.
    """
    _page_with_artifacts(
        db_session, method="integrity-few", calcs=8, artifacts=_FEW_ARTIFACTS
    )
    _page_with_artifacts(
        db_session, method="integrity-many", calcs=8, artifacts=_MANY_ARTIFACTS
    )

    _, few = _integrity_statements_for_page(
        db_session, method="integrity-few", limit=8
    )
    _, many = _integrity_statements_for_page(
        db_session, method="integrity-many", limit=8
    )

    assert few >= 1, "the integrity relationship must actually be loaded"
    assert many == few, (
        f"{many} integrity statements for {_MANY_ARTIFACTS} artifacts per "
        f"calculation against {few} for {_FEW_ARTIFACTS}: the custody load "
        "is fanning out per artifact row"
    )


def test_the_integrity_load_costs_exactly_one_statement_per_artifact_load(
    db_session,
):
    """One custody statement per artifact-loading statement, and no more.

    That ratio is the whole claim made for ``lazy="selectin"``. Measured on
    this fixture the calculations search issues one artifact load per
    record rather than one per page, so the integrity load costs one more
    statement per record too -- a real per-record cost, bounded by
    ``limit`` and not by the corpus. Pinning the *ratio* rather than the
    absolute number is what makes this assertion survive the fix: batching
    the artifact load across a page takes both numbers down together.
    """
    _page_with_artifacts(
        db_session, method="integrity-small-page", calcs=4, artifacts=2
    )
    _page_with_artifacts(
        db_session, method="integrity-large-page", calcs=20, artifacts=2
    )

    small_artifacts, small_integrity = _integrity_statements_for_page(
        db_session, method="integrity-small-page", limit=4
    )
    large_artifacts, large_integrity = _integrity_statements_for_page(
        db_session, method="integrity-large-page", limit=20
    )

    assert small_artifacts >= 1 and large_artifacts >= 1
    assert small_integrity <= small_artifacts, (
        f"{small_integrity} custody statements against {small_artifacts} "
        "artifact loads on a 4-record page"
    )
    assert large_integrity <= large_artifacts, (
        f"{large_integrity} custody statements against {large_artifacts} "
        "artifact loads on a 20-record page"
    )
    # One per record, and the slope is what a regression would move.
    assert (large_integrity - small_integrity) <= (20 - 4), (
        f"{(large_integrity - small_integrity) / 16} custody statements per "
        "additional record"
    )


# ---------------------------------------------------------------------------
# What ADR 0012's fields cost on the frequency-result projection
# ---------------------------------------------------------------------------

#: Statements that *load* a ``calc_freq_result`` row, per record on an
#: ``include=results`` page. One: the projection loads the row it projects
#: and nothing else.
#:
#: ADR 0012 requires ``n_imag`` to travel with the tolerance it was judged
#: at, how that tolerance was chosen, and the count above it. The first two
#: are columns on the row that was already being loaded and are free. The
#: count is not stored -- it is taken over ``calc_freq_mode`` -- and the
#: obvious way to get it is a second round trip, which this builder runs
#: once per record and would therefore multiply by the page size. It rides
#: along as a correlated aggregate on the same SELECT instead, so the cost
#: of ADR 0012 compliance on this projection is measurably zero statements.
_FREQ_RESULT_STATEMENTS_PER_RECORD = 1

_FREQ_SMALL_PAGE = 5
_FREQ_LARGE_PAGE = 20

#: A column only the projection's SELECT list names. The
#: provenance/available-sections probe also mentions ``calc_freq_result``
#: -- once per record, inside its own combined statement, and it did so
#: before ADR 0012's fields existed -- so counting the table name would
#: measure two unrelated statements as one number and hide a change in
#: either.
_PROJECTION_MARKER = "imaginary_mode_tau_basis"


def _freq_statements_for_page(
    session: Session, *, method: str, limit: int
) -> tuple[int, int]:
    """Return (statements loading calc_freq_result, extra mode statements).

    The second number is the one that would move if the mode count became
    its own query: a statement touching ``calc_freq_mode`` and *not*
    ``calc_freq_result`` is a second round trip, where the correlated
    aggregates appear inside the ``calc_freq_result`` statement itself and
    the ``has_freq_modes`` probe mentions both tables at once.
    """
    freq_result = 0
    freq_mode = 0
    engine = session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal freq_result, freq_mode
        if _PROJECTION_MARKER in statement:
            freq_result += 1
        elif "calc_freq_mode" in statement and "calc_freq_result" not in statement:
            freq_mode += 1

    try:
        response = search_calculations(
            session,
            CalculationsSearchRequest(
                calculation_type=CalculationType.freq,
                method=method,
                limit=limit,
                include=["results"],
            ),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert len(response.records) == limit, "page must be full to be comparable"
    return freq_result, freq_mode


def _freq_page(db_session, *, method: str, calcs: int) -> None:
    """A page of frequency calculations, each with a judged freq result."""
    lot = make_lot(db_session, method=method, basis="def2tzvp")
    entry = make_species_entry(
        db_session,
        make_species(db_session, inchi_key=next_inchi_key(f"FRQ{abs(hash(method)) % 900}")),
    )
    for _ in range(calcs):
        calc = make_calculation(
            db_session,
            type=CalculationType.freq,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        attach_freq_result(
            db_session,
            calculation=calc,
            frequencies_cm1=[-1300.0, -42.0, -13.0, 620.0],
            reaction_coordinate_mode_index=1,
            imaginary_mode_tau_cm1=15.0,
            imaginary_mode_tau_basis="analytic_tight",
            imaginary_mode_structural_flag=True,
        )


def test_the_adr_0012_fields_cost_no_extra_statement_per_record(db_session):
    """The count above tau is aggregated in the statement already there.

    Two claims, and the second is the one that would regress silently.
    The ``calc_freq_result`` slope stays at one statement per record --
    unchanged by the four ADR 0012 columns joining the projection, because
    they were always on the row being loaded. And no statement touches
    ``calc_freq_mode`` on its own: the count above tau is a correlated
    aggregate, not a second trip. A future edit that reaches for
    ``row.modes`` instead would pass every other assertion in the suite
    and double the cost of every frequency record on every search page.
    """
    _freq_page(db_session, method="freq-cost-small", calcs=_FREQ_SMALL_PAGE)
    _freq_page(db_session, method="freq-cost-large", calcs=_FREQ_LARGE_PAGE)

    small_result, small_mode = _freq_statements_for_page(
        db_session, method="freq-cost-small", limit=_FREQ_SMALL_PAGE
    )
    large_result, large_mode = _freq_statements_for_page(
        db_session, method="freq-cost-large", limit=_FREQ_LARGE_PAGE
    )

    assert small_result >= 1, (
        "the frequency-result projection must actually load its row -- "
        "an assertion over zero statements proves nothing"
    )
    slope = (large_result - small_result) / (_FREQ_LARGE_PAGE - _FREQ_SMALL_PAGE)
    assert slope <= _FREQ_RESULT_STATEMENTS_PER_RECORD, (
        f"{slope} calc_freq_result statements per record, expected at most "
        f"{_FREQ_RESULT_STATEMENTS_PER_RECORD} ({small_result} statements for "
        f"{_FREQ_SMALL_PAGE} records, {large_result} for {_FREQ_LARGE_PAGE})"
    )
    assert (small_mode, large_mode) == (0, 0), (
        f"{small_mode} / {large_mode} standalone calc_freq_mode statements: "
        "the count above tau has become a second round trip per record"
    )
