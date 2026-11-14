"""A tolerance basis nobody recognises must not be able to reach a reader.

``calc_freq_result.imaginary_mode_tau_basis`` says which row of the imaginary-mode
protocol table set the tolerance a record's imaginary modes were judged
at. It is ``TEXT``, and a CHECK constraint keeps it to the rows that exist
(eight, including the ``assumed_*`` bases):
``app/services/calculation_resolution.py`` writes ``TauBasis(...).value``
and always has, but a second write path, a bulk loader, or a restore from
a hand-edited dump could put anything there.

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
   column is NULL on every record deposited before the imaginary-mode protocol table, and nothing
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

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tckdb_schemas.stationary_point import TauBasis

from app.db.models.common import IMAGINARY_MODE_TAU_BASIS_VALUES, CalculationType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_CONSTRAINT = "ck_calc_freq_result_imaginary_mode_tau_basis_known"

#: Values that are not rows of the imaginary-mode protocol table. The first three
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
    """Every row of the imaginary-mode protocol table (recorded and assumed), plus NULL.

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
    """The fact that decides CHECK over a vocabulary table.

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

    The constraint is validated in one statement. A ``NOT VALID`` to dodge a
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


_ASSUMED_BASES = (
    "assumed_analytic_default",
    "assumed_finite_difference_gradient",
    "assumed_finite_difference_energy",
)


def test_every_assumed_basis_is_accepted(db_session):
    """Every ``assumed_*`` token is a legal write.

    The parametrised acceptance test above already proves this against
    every live ``TauBasis`` member; this test pins the same fact against
    the literal tokens the amendment introduced, so a future rename of
    ``TauBasis`` members would show up here as well as there.
    """
    lot = make_lot(db_session, method="taubasiswiden", basis="def2tzvp")
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("TAUW"))
    )
    calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        lot_id=lot.id,
        species_entry_id=entry.id,
    )
    for basis in _ASSUMED_BASES:
        savepoint = db_session.begin_nested()
        try:
            _insert_freq_result(db_session, calc.id, basis)
        finally:
            savepoint.rollback()
