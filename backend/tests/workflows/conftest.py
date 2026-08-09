"""Keep the workflow suite from writing anything the next test can see.

Every test here persists a lot: species, entries, calculations, geometries,
transition states, and the scientific products hanging off them. They share
one database for the whole pytest process, and many of them locate "the" row
they just made with an unqualified query — ``session.scalar(select(Calculation)
.where(Calculation.type == irc))`` and friends. That only works while the
database contains nothing but the current test's own writes.

For a long time it did not. The tree used the session-scoped ``db_engine``
directly with ``with Session(db_engine) as session, session.begin():``, which
commits on block exit, so each test inherited everything its predecessors had
written. Running ``test_network_pdep_upload.py`` before
``test_computed_reaction_upload.py`` left fifteen committed ``irc``
calculations behind and five computed-reaction tests then asserted against the
wrong one — deterministically, with no random seed involved.

The tree now takes ``db_conn`` (per-test transaction, rolled back at teardown;
see ``tests/conftest.py``). The fixture below is the enforcement: it fails the
individual test that commits, rather than letting the damage surface as an
inexplicable failure in whatever file happens to run next.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text

#: Tables a workflow upload writes to. Not exhaustive by design — it is a
#: tripwire, not an audit. Any commit big enough to confuse a sibling test
#: lands in at least one of these.
_WATCHED_TABLES = (
    "species",
    "species_entry",
    "chem_reaction",
    "reaction_entry",
    "transition_state",
    "transition_state_entry",
    "calculation",
    "geometry",
    "conformer_group",
    "conformer_observation",
    "statmech",
    "thermo",
    "kinetics",
    "transport",
    "network",
)

_COUNT_SQL = text(
    " UNION ALL ".join(
        f"SELECT '{table}' AS t, count(*) AS n FROM public.{table}"
        for table in _WATCHED_TABLES
    )
)


@pytest.fixture(scope="session")
def _committed_row_probe(db_engine) -> Iterator:
    """A connection that always reads the latest *committed* state.

    ``AUTOCOMMIT`` matters: a pooled connection holding an open transaction
    would keep returning its first snapshot and the tripwire would never fire.
    """
    connection = db_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        yield connection
    finally:
        connection.close()


def _counts(connection) -> dict[str, int]:
    return dict(connection.execute(_COUNT_SQL).all())


@pytest.fixture(autouse=True)
def _refuse_committed_workflow_rows(_committed_row_probe, request) -> Iterator[None]:
    """Fail the test that commits, not the test that trips over the residue.

    Autouse, so it is set up before the test's own fixtures and therefore torn
    down after them — the counts below are read once ``db_conn`` has rolled
    back, so only a genuine commit shows up.

    The baseline is re-synced after a failure so exactly one test is blamed
    for one leak, instead of every test after it inheriting the accusation.
    """
    before = getattr(request.session, "_tckdb_workflow_row_baseline", None)
    if before is None:
        before = _counts(_committed_row_probe)
        request.session._tckdb_workflow_row_baseline = before

    yield

    after = _counts(_committed_row_probe)
    if after == before:
        return

    request.session._tckdb_workflow_row_baseline = after
    grew = {
        table: (before[table], after[table])
        for table in after
        if after[table] != before[table]
    }
    raise AssertionError(
        f"{request.node.nodeid} committed rows into the shared test database: "
        + ", ".join(f"{t} {was}->{now}" for t, (was, now) in sorted(grew.items()))
        + ". Workflow tests must persist through the `db_conn` fixture, whose "
        "transaction is rolled back at teardown — committed rows are visible "
        "to every later test in the process and are what made this suite "
        "order-dependent. If a test genuinely needs two concurrent "
        "transactions, take `db_engine` instead and delete its rows in a "
        "fixture `finally`."
    )
