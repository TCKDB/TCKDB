"""One hindered rotor per ``(statmech record, torsion index)``, in the database.

A duplicate ``statmech_torsion.torsion_index`` is not the kind of defect
that announces itself. Nothing rejects the record, nothing logs, and no
reader sees an error: the second row is simply a second rotor, and the
partition function it feeds counts one internal rotation twice. Every
downstream number -- entropy, heat capacity, the rate constant that used
them -- is wrong by a factor nobody can spot from the outputs.

All three upload paths refuse duplicates at validation time
(``computed_species_upload``, ``computed_reaction_upload``,
``conformer_upload``). That covers writers that arrive as a request, and
it is the whole of the coverage that a reading of the *schema* would find
if it went looking for a ``UniqueConstraint`` on the model, because there
isn't one -- there is a unique ``Index``, which is the same enforcement
wearing a different declaration. Its name until ``b7e4d1a9c026`` was
``uq_statmech_torsion_statmech_id``, mentioning only the first of its two
columns, which reads as a rule about ``statmech_id`` alone. That is how
the invariant came to be reported as unenforced below the request layer.

This file settles it by provocation rather than by reading either the
name or the declaration:

1. The database refuses the duplicate.
2. It refuses it under ``session_replication_role = replica`` -- the mode
   bulk loaders, importers and ``pg_restore`` run in, which suspends
   triggers and foreign keys. Those are exactly the writers that do not
   pass through an upload schema, and the reason a unique index is worth
   more here than any amount of validation.
3. The legitimate shapes still go in, so the rule is not over-broad.
4. The index still spans both columns and is still unique, read out of
   ``pg_index``. A future migration that narrowed it to ``statmech_id``,
   or dropped ``unique``, would keep every provocation above passing on a
   table that happened to have no second torsion in it; this reads the
   catalog instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.statmech import StatmechTorsion
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
)

_INDEX = "uq_statmech_torsion_statmech_id_torsion_index"


@pytest.fixture
def statmech_ids(db_session) -> tuple[int, int]:
    """Two independent statmech records, to separate "same" from "any"."""
    ids = []
    for tag in ("TORA", "TORB"):
        entry = make_species_entry(
            db_session, make_species(db_session, inchi_key=next_inchi_key(tag))
        )
        ids.append(make_statmech(db_session, species_entry=entry).id)
    return ids[0], ids[1]


def _add_torsion(db_session, statmech_id: int, torsion_index: int) -> None:
    db_session.add(
        StatmechTorsion(
            statmech_id=statmech_id,
            torsion_index=torsion_index,
            dimension=1,
        )
    )
    db_session.flush()


def test_a_repeated_torsion_index_is_refused(db_session, statmech_ids):
    """The rotor cannot be counted twice, whatever wrote it."""
    statmech_id, _ = statmech_ids
    savepoint = db_session.begin_nested()
    try:
        _add_torsion(db_session, statmech_id, 1)
        with pytest.raises(IntegrityError) as excinfo:
            _add_torsion(db_session, statmech_id, 1)
        assert _INDEX in str(excinfo.value), (
            "the duplicate was refused by something other than "
            f"{_INDEX}: {excinfo.value}"
        )
    finally:
        savepoint.rollback()


def test_the_shapes_that_are_not_duplicates_are_accepted(db_session, statmech_ids):
    """Two rotors on one record, and rotor 1 on each of two records.

    Both are ordinary. Asserted so that a constraint tightened by mistake
    -- unique on ``statmech_id``, or on ``torsion_index`` -- fails here
    rather than at the first two-rotor species someone uploads.
    """
    first, second = statmech_ids
    savepoint = db_session.begin_nested()
    try:
        _add_torsion(db_session, first, 1)
        _add_torsion(db_session, first, 2)
        _add_torsion(db_session, second, 1)
        stored = db_session.scalar(
            text(
                "SELECT count(*) FROM statmech_torsion"
                " WHERE statmech_id IN (:a, :b)"
            ),
            {"a": first, "b": second},
        )
        assert stored == 3
    finally:
        savepoint.rollback()


def test_the_index_holds_under_a_bulk_load_session(db_session, statmech_ids):
    """The measurement that answers "is validation the only thing stopping it".

    Both halves together, because the claim is the *difference*. With
    system triggers suspended the foreign key onto ``statmech`` stops
    holding -- a torsion for a record that does not exist is accepted --
    and the unique index on the same table in the same session does not
    stop holding. An importer or a restore is therefore not able to
    produce the duplicate, which is the specific thing that was in doubt.
    """
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))

        # The foreign key: not enforced.
        db_session.execute(
            text(
                "INSERT INTO statmech_torsion"
                " (statmech_id, torsion_index, dimension)"
                " VALUES (-1, 1, 1)"
            )
        )
        db_session.flush()

        # The unique index: still enforced, same table, same session.
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(
                    "INSERT INTO statmech_torsion"
                    " (statmech_id, torsion_index, dimension)"
                    " VALUES (-1, 1, 1)"
                )
            )
            db_session.flush()
        assert _INDEX in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_the_index_is_unique_and_spans_both_columns(db_session):
    """Read off ``pg_index``, not off the name or the model declaration.

    The name is what misled a reader once already, and the model
    declaration is not what the database enforces -- the catalog is.
    """
    row = db_session.execute(
        text(
            "SELECT i.indisunique,"
            "       array_agg(a.attname ORDER BY k.ord) AS cols"
            "  FROM pg_index i"
            "  JOIN pg_class c ON c.oid = i.indexrelid"
            "  JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)"
            "       ON TRUE"
            "  JOIN pg_attribute a"
            "       ON a.attrelid = i.indrelid AND a.attnum = k.attnum"
            " WHERE c.relname = :name"
            " GROUP BY i.indisunique"
        ),
        {"name": _INDEX},
    ).one_or_none()

    assert row is not None, f"{_INDEX} is not on statmech_torsion"
    indisunique, columns = row
    assert indisunique is True, f"{_INDEX} exists but is not unique"
    assert list(columns) == ["statmech_id", "torsion_index"], (
        f"{_INDEX} covers {list(columns)}; a rotor is unique within a "
        "statmech record, so it must cover exactly statmech_id and "
        "torsion_index"
    )
