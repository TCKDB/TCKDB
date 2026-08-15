"""A tolerance basis nobody recognises must not be able to reach a reader.

``calc_freq_result.imaginary_mode_tau_basis`` says which row of ADR 0012's
protocol table set the tolerance a record's imaginary modes were judged
at. It is ``TEXT``, and until ``e2a7c9d4b615`` nothing but convention kept
it to the five rows that exist: ``app/services/calculation_resolution.py``
writes ``TauBasis(...).value`` and always has, but a second write path, a
bulk loader, or a restore from a hand-edited dump could put anything
there.

Why that matters more than a typo usually does. The column is *read* as
``str`` on purpose -- an unrecognised basis is displayed rather than made
to refuse the whole record, so that a reader of a record written by a
newer TCKDB is shown what it says. That openness is only safe if a value
this build does not recognise really does mean "newer writer". A typo
wearing the same costume turns a deliberate forward-compatibility choice
into silent corruption, and the reader has no way to tell which they are
looking at.

What is pinned here:

1. **Every ``TauBasis`` value is accepted**, so the constraint cannot
   refuse a record the upload path legitimately produces. NULL too: the
   column is NULL on every record deposited before ADR 0012, and nothing
   was backfilled.
2. **Nothing else is**, including the near misses -- wrong case, hyphen
   for underscore, empty string.
3. **It holds under ``session_replication_role = replica``**, which is
   what bulk loaders and restore paths run under. This is the reason the
   change is a CHECK and not a foreign key onto a vocabulary table; the
   same measurement is made for element symbols in
   ``test_element_symbol_canonicality.py``.
4. **The database's vocabulary and ``TauBasis`` are the same set.** This
   is the drift guard: adding a ``TauBasis`` member without migrating the
   constraint fails here, rather than at the first upload that resolves
   to it -- which would be a 500 on a scientifically valid deposit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tckdb_schemas.stationary_point import TauBasis

from alembic import command
from app.db.models.common import IMAGINARY_MODE_TAU_BASIS_VALUES, CalculationType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS_HEAD = "d4e9b1c7a253"
_CURRENT_HEAD = "e2a7c9d4b615"
_CONSTRAINT = "ck_calc_freq_result_imaginary_mode_tau_basis_known"

#: Values that are not rows of ADR 0012's protocol table. The first three
#: are the realistic typos; ``""`` is the one a form-driven writer
#: produces; ``analytic`` is a ``HessianMethod`` value, which is the
#: adjacent vocabulary most likely to be written here by mistake.
_NOT_A_BASIS = (
    "analytic-tight",
    "ANALYTIC_TIGHT",
    "analytic_tigth",
    "",
    "analytic",
    "protocol not recorded",
)


@pytest.fixture
def calculation_id(db_session) -> int:
    """A bare ``freq`` calculation to hang provocations on."""
    lot = make_lot(db_session, method="taubasis", basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("TAU"))
    )
    calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        lot_id=lot.id,
        species_entry_id=entry.id,
    )
    return calc.id


def _values_in(definition: str) -> set[str]:
    """Every single-quoted literal in a ``pg_get_constraintdef`` string.

    PostgreSQL rewrites ``IN (...)`` to ``= ANY (ARRAY[...])`` and stamps
    each literal with ``::text``, and which of those two shapes it prints
    is not the point of the assertion. Pulling the quoted literals out
    directly reads either.
    """
    return set(re.findall(r"'([^']*)'::text", definition)) or set(
        re.findall(r"'([^']*)'", definition)
    )


def _insert_freq_result(db_session, calculation_id: int, basis: str | None) -> None:
    db_session.execute(
        text(
            "INSERT INTO calc_freq_result"
            " (calculation_id, n_imag, imaginary_mode_tau_cm1,"
            " imaginary_mode_tau_basis)"
            " VALUES (:c, 1, 15.0, :b)"
        ),
        {"c": calculation_id, "b": basis},
    )
    db_session.flush()


@pytest.mark.parametrize("basis", [member.value for member in TauBasis] + [None])
def test_every_basis_the_upload_path_can_resolve_is_accepted(
    db_session, calculation_id, basis
):
    """The five rows of the protocol table, and "never judged" as NULL.

    Parametrised over ``TauBasis`` itself rather than a hand-written list,
    so a member added to the enum shows up here as a failure to accept
    it rather than as a list nobody updated.
    """
    savepoint = db_session.begin_nested()
    try:
        _insert_freq_result(db_session, calculation_id, basis)
        stored = db_session.scalar(
            text(
                "SELECT imaginary_mode_tau_basis FROM calc_freq_result"
                " WHERE calculation_id = :c"
            ),
            {"c": calculation_id},
        )
        assert stored == basis
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("basis", _NOT_A_BASIS)
def test_a_value_that_is_not_a_protocol_table_row_is_refused(
    db_session, calculation_id, basis
):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            _insert_freq_result(db_session, calculation_id, basis)
        assert _CONSTRAINT in str(excinfo.value), (
            f"{basis!r} was refused by something other than {_CONSTRAINT}: "
            f"{excinfo.value}"
        )
    finally:
        savepoint.rollback()


def test_the_constraint_holds_under_a_bulk_load_session(db_session, calculation_id):
    """The measured fact that decided CHECK over a vocabulary table.

    Both halves together, because the claim is the *difference*: with
    system triggers suspended the foreign key onto ``calculation`` stops
    holding, and the CHECK on the same row in the same session does not.
    """
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))

        # The foreign key: not enforced. A result row for a calculation
        # that does not exist is accepted.
        db_session.execute(
            text(
                "INSERT INTO calc_freq_result (calculation_id, n_imag)"
                " VALUES (-1, 0)"
            )
        )
        db_session.flush()

        # The CHECK: still enforced, same table, same session.
        with pytest.raises(IntegrityError) as excinfo:
            _insert_freq_result(db_session, calculation_id, "analytic-tight")
        assert _CONSTRAINT in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_the_database_vocabulary_is_exactly_taubasis(db_session):
    """The drift guard, read off the live constraint definition.

    Asserted against ``pg_get_constraintdef`` rather than by provocation:
    provocation shows that the values tested are accepted or refused, and
    what has to be pinned is that the *set* matches -- a sixth value
    quietly present in the constraint and in no test would otherwise be
    invisible.
    """
    definition = db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = :name"
            " AND conrelid = 'calc_freq_result'::regclass"
        ),
        {"name": _CONSTRAINT},
    )
    assert definition is not None, f"{_CONSTRAINT} is not on calc_freq_result"

    quoted = _values_in(definition)
    expected = {member.value for member in TauBasis}
    assert quoted == expected, (
        f"the database accepts {sorted(quoted)} but TauBasis is "
        f"{sorted(expected)}; constraint definition: {definition}"
    )
    assert set(IMAGINARY_MODE_TAU_BASIS_VALUES) == expected, (
        "app.db.models.common.IMAGINARY_MODE_TAU_BASIS_VALUES has drifted "
        f"from TauBasis: {sorted(IMAGINARY_MODE_TAU_BASIS_VALUES)} vs "
        f"{sorted(expected)}"
    )


def test_the_constraint_is_validated(db_session):
    """A ``NOT VALID`` CHECK says nothing about the rows already stored.

    ``e2a7c9d4b615`` adds it validated in one statement, having first
    refused to run at all if the column held anything it could not
    classify. A future revision that swapped in ``NOT VALID`` to dodge a
    table scan, and forgot the ``VALIDATE``, would look identical to
    every other test in this file.
    """
    convalidated = db_session.scalar(
        text(
            "SELECT convalidated FROM pg_constraint"
            " WHERE conname = :name"
            " AND conrelid = 'calc_freq_result'::regclass"
        ),
        {"name": _CONSTRAINT},
    )
    assert convalidated is True


# ---------------------------------------------------------------------------
# What the migration does when it meets a value it cannot classify
# ---------------------------------------------------------------------------


def test_the_migration_refuses_rather_than_guesses(db_engine, monkeypatch):
    """``e2a7c9d4b615`` stops, naming the values, and changes nothing.

    The alternatives were all worse and all quiet. Coercing an
    unrecognised basis to ``protocol_not_recorded`` asserts the record was
    judged at the conservative tolerance, which is a claim about its
    science that only whatever wrote it can make; deleting the value
    throws away the only trace of that writer; adding the constraint
    ``NOT VALID`` leaves a rule that binds new writes and says nothing
    about the rows already there.

    Measured against a planted row rather than argued, because the guard
    is a branch that never runs on a clean database -- which is every
    database this migration is expected to meet, and exactly why it would
    otherwise never be exercised at all.
    """
    db_name = db_engine.url.database
    url = db_engine.url.render_as_string(hide_password=False)
    db_engine.dispose()
    for key, value in (
        ("DB_NAME", db_name),
        ("DB_USER", "tckdb"),
        ("DB_PASSWORD", "tckdb"),
        ("DB_HOST", "127.0.0.1"),
        ("DB_PORT", "5432"),
    ):
        monkeypatch.setenv(key, value)
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    engine = create_engine(url)
    planted = "DELETE FROM calc_freq_result WHERE calculation_id IN (-901, -902)"

    try:
        command.downgrade(config, _PREVIOUS_HEAD)

        # One unclassifiable value and one legitimate one. The foreign key
        # onto ``calculation`` is suspended rather than satisfied: what is
        # under test is the migration's reaction to the *column*, and a
        # bulk-load session is precisely the writer that could have put an
        # unrecognised value there.
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            connection.execute(
                text(
                    "INSERT INTO calc_freq_result (calculation_id, n_imag,"
                    " imaginary_mode_tau_basis) VALUES"
                    " (-901, 1, 'nonsense-basis'), (-902, 1, 'analytic_tight')"
                )
            )

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(config, _CURRENT_HEAD)
        message = str(excinfo.value)
        assert "nonsense-basis" in message, (
            "the migration must name what it refused to classify; it said: "
            f"{message}"
        )
        assert "analytic_tight" not in message, (
            f"a legitimate value was reported as unclassifiable: {message}"
        )

        # Nothing changed on the way out: the row is still there, still
        # saying what it said, and no constraint was installed.
        with engine.connect() as connection:
            still_there = connection.scalar(
                text(
                    "SELECT imaginary_mode_tau_basis FROM calc_freq_result"
                    " WHERE calculation_id = -901"
                )
            )
            assert still_there == "nonsense-basis"
            assert _constraint_count(connection) == 0

        # Repaired the way an operator would -- by deciding what the row
        # meant -- the same migration goes through.
        _purge_planted_rows(engine, planted)
        command.upgrade(config, _CURRENT_HEAD)
        with engine.connect() as connection:
            assert _constraint_count(connection) == 1
    finally:
        # Leave the database at head whatever happened, so a failure here
        # fails this test and not every test that follows it.
        #
        # ``head``, not ``_CURRENT_HEAD``. This test downgrades the *per-run
        # database every other test in the process is using*, and
        # ``_CURRENT_HEAD`` is the revision under test, which stops being head
        # the moment anything lands on top of it. It did, one day later:
        # ``b7e4d1a9c026`` renames a unique index and six CHECK constraints, so
        # stopping at ``e2a7c9d4b615`` handed every later test a
        # ``statmech_torsion`` whose unique index was still called
        # ``uq_statmech_torsion_statmech_id``. That surfaced as three failures
        # in ``test_statmech_torsion_index_uniqueness.py`` -- a file with
        # nothing to do with tau bases -- claiming the wrong constraint had
        # refused a duplicate, when the only thing wrong was its name.
        _purge_planted_rows(engine, planted)
        command.upgrade(config, "head")
        engine.dispose()


def _purge_planted_rows(engine, statement: str) -> None:
    """Remove the planted rows, triggers and all.

    ``session_replication_role`` again, on the way out as well as in:
    ``calc_freq_result`` is under the accepted-science immutability guard,
    which refuses to *delete* a row whose parent calculation does not
    exist just as firmly as it refused to insert one. The suite's
    committed-row leak check fails this test if anything survives, which
    is how a botched cleanup announces itself here rather than as an
    order-dependent failure somewhere else.
    """
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text(statement))


def _constraint_count(connection) -> int:
    return connection.scalar(
        text(
            "SELECT count(*) FROM pg_constraint WHERE conname = :name"
            " AND conrelid = 'calc_freq_result'::regclass"
        ),
        {"name": _CONSTRAINT},
    )
