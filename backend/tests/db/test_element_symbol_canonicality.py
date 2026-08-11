"""One spelling of an element symbol, as an invariant rather than a habit.

``b4e7c1d20f83`` made ``geometry_atom.element`` canonical by canonicalising it
in ``parse_xyz`` and backfilling the older rows, and said in its own docstring
that this established no invariant: nothing in the schema required the column
to be canonical, so a restore from an older backup or a bulk import could put
``CL`` back at any time. ``c5a1f8e3d074`` adds the constraint.

What has to be pinned here, and why each one is a way it could go wrong:

1. **The constraint refuses a non-canonical symbol**, shouted or whispered,
   written directly rather than through ``parse_xyz`` -- a write path that does
   not canonicalise is the entire class of writer it exists for.
2. **It accepts everything the ingestion path can produce**, including ``D``
   and ``T``. Case is canonicalised; isotope labelling deliberately is not, and
   a constraint that refused ``D`` would refuse every deuterated geometry in
   the database.
3. **It holds under ``session_replication_role = replica``.** This is the whole
   argument for the shape of the change and is measured rather than assumed. A
   foreign key in PostgreSQL is a system trigger and is suspended under
   ``replica``; a CHECK is not. ``replica`` is what bulk loaders and restore
   paths run under -- the very paths that could reintroduce ``CL`` -- so a rule
   that lapses there would be a rule that lapses exactly where it is needed.
   The sibling file ``tests/api/test_api_database_constraint_codes.py`` relies
   on the same split for its provocations.
4. **``reaction_atom_map_pair``'s two element columns carry their own copy of
   the constraint** rather than inheriting one through their composite foreign
   keys onto ``geometry_atom``. Point 3 is why: under ``replica`` those foreign
   keys hold nothing, so the inheritance argument fails precisely where it
   matters. The table is empty on the deployed database, so this cost nothing
   to add and would have cost a backfill later.
5. **``ck_reaction_atom_map_pair_element_matches`` is plain equality now.** It
   was ``upper(element) = upper(ts_element)`` because nothing held the two ends
   to one spelling. Asserted against ``pg_get_constraintdef`` rather than by
   provocation, because the tightening changes *which* constraint names a
   refusal and not whether the row is refused: ``('C', 'c')`` satisfied the
   ``upper()`` form and violates this one, but point 4's CHECKs would have
   caught it either way. What the tightening buys is that the rule stops being
   case-blind once the reason it was case-blind has gone.

Why the two columns are not collapsed into one -- which point 3 also decides --
is argued in :mod:`app.db.models.reaction_atom_map`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_CANONICALITY_CHECKS = {
    "geometry_atom": ("ck_geometry_atom_element_canonical",),
    "reaction_atom_map_pair": (
        "ck_reaction_atom_map_pair_element_canonical",
        "ck_reaction_atom_map_pair_ts_element_canonical",
    ),
}

#: Spellings of a real element that the one canonical form is not.
_NON_CANONICAL = ("CL", "cl", "c", "h", "nA")

#: Everything ``normalize_element_symbol`` can hand ``geometry_atom``. ``D`` and
#: ``T`` are deposited isotope labelling and are stored as written.
_CANONICAL = ("H", "C", "O", "N", "S", "F", "Cl", "D", "T")

#: Not element symbols at all. ``character(2)`` already says "at most two
#: characters"; the pattern completes the sentence.
_NOT_A_SYMBOL = ("1h", "@c", "C:", "  ", "12")


def _insert_atom(session, geometry_id: int, atom_index: int, element: str) -> None:
    session.execute(
        text(
            "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z)"
            " VALUES (:g, :i, :e, 0, 0, 0)"
        ),
        {"g": geometry_id, "i": atom_index, "e": element},
    )
    session.flush()


@pytest.fixture
def geometry_id(db_session) -> int:
    """A geometry with no atoms, to hang provocations on."""
    return db_session.scalar(
        text(
            "INSERT INTO geometry (natoms, geom_hash, xyz_text)"
            " VALUES (1, md5(random()::text) || md5(random()::text), 'x')"
            " RETURNING id"
        )
    )


@pytest.mark.parametrize("element", _NON_CANONICAL)
def test_a_non_canonical_symbol_is_refused(db_session, geometry_id, element):
    """Written directly, because that is the writer the constraint is for."""
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            _insert_atom(db_session, geometry_id, 1, element)
        assert "ck_geometry_atom_element_canonical" in str(excinfo.value)
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("element", _NOT_A_SYMBOL)
def test_a_value_that_is_not_an_element_symbol_is_refused(
    db_session, geometry_id, element
):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            _insert_atom(db_session, geometry_id, 1, element)
        assert "ck_geometry_atom_element_canonical" in str(excinfo.value)
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("element", _CANONICAL)
def test_every_symbol_the_ingestion_path_produces_is_accepted(
    db_session, geometry_id, element
):
    """Including ``D`` and ``T``.

    ``normalize_element_symbol`` settles capitalisation and deliberately leaves
    hydrogen isotope labelling alone, so ``D`` is what a deuterated deposit
    stores. A constraint that refused it would refuse correct chemistry, which
    ADR 0008 puts out of bounds -- and would refuse it on ingestion, not at the
    edges.
    """
    savepoint = db_session.begin_nested()
    try:
        _insert_atom(db_session, geometry_id, 1, element)
        stored = db_session.scalar(
            text(
                "SELECT element FROM geometry_atom"
                " WHERE geometry_id = :g AND atom_index = 1"
            ),
            {"g": geometry_id},
        )
        assert stored.strip() == element
    finally:
        savepoint.rollback()


def test_the_constraint_holds_under_a_bulk_load_session(db_session, geometry_id):
    """The measured fact that decided the shape of this change.

    ``session_replication_role = replica`` suspends system triggers -- which is
    how PostgreSQL implements a foreign key -- and leaves CHECK constraints
    armed. Both halves are asserted together, because the interesting claim is
    the *difference*: on the path where the foreign key stops holding, the
    CHECK still does.

    If this ever reverses, the argument in
    :mod:`app.db.models.reaction_atom_map` for keeping ``element`` and
    ``ts_element`` as two columns is no longer sound and should be revisited.
    """
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))

        # The foreign key: not enforced. A pair row naming an atom of a
        # geometry that has no atoms at all is accepted.
        db_session.execute(
            text(
                "INSERT INTO reaction_atom_map_pair (atom_map_id, side,"
                " structure_participant_id, geometry_id, atom_index,"
                " transition_state_geometry_id, ts_atom_index, element,"
                " ts_element)"
                " VALUES (1, 'reactant', 1, :g, 1, :g, 1, 'C', 'C')"
            ),
            {"g": geometry_id},
        )
        db_session.flush()

        # The CHECK: still enforced, on the same table in the same session.
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(
                    "INSERT INTO reaction_atom_map_pair (atom_map_id, side,"
                    " structure_participant_id, geometry_id, atom_index,"
                    " transition_state_geometry_id, ts_atom_index, element,"
                    " ts_element)"
                    " VALUES (1, 'reactant', 2, :g, 2, :g, 2, 'CL', 'CL')"
                ),
                {"g": geometry_id},
            )
            db_session.flush()
        assert "ck_reaction_atom_map_pair_element_canonical" in str(excinfo.value)
    finally:
        savepoint.rollback()

    # And on ``geometry_atom`` too, which is the column everything reads.
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))
        with pytest.raises(IntegrityError) as excinfo:
            _insert_atom(db_session, geometry_id, 1, "CL")
        assert "ck_geometry_atom_element_canonical" in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_the_saddle_point_end_cannot_hold_a_non_canonical_spelling(
    db_session, geometry_id
):
    """A shouted saddle-point symbol is refused, with the foreign keys off.

    Which constraint names the refusal is deliberately not asserted, because
    ``ck_reaction_atom_map_pair_ts_element_canonical`` can never be the *sole*
    reason a row fails. Canonical ``element`` plus ``element = ts_element``
    already implies a canonical ``ts_element``, so isolating it would need a
    row where the two columns are equal and only one of them is canonical --
    which is not a row. It is kept anyway: this revision has just demonstrated
    that ``ck_..._element_matches`` is a constraint somebody edits, and the
    saddle-point column's domain should not quietly become unconstrained the
    next time. ``test_the_constraint_exists_and_is_validated`` pins that it is
    really there.

    Also the case that would have slipped through before ``c5a1f8e3d074``:
    ``('C', 'c')`` satisfied ``upper(element) = upper(ts_element)``.
    """
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(
                    "INSERT INTO reaction_atom_map_pair (atom_map_id, side,"
                    " structure_participant_id, geometry_id, atom_index,"
                    " transition_state_geometry_id, ts_atom_index, element,"
                    " ts_element)"
                    " VALUES (1, 'reactant', 1, :g, 1, :g, 1, 'C', 'c')"
                ),
                {"g": geometry_id},
            )
            db_session.flush()
        assert "reaction_atom_map_pair" in str(excinfo.value)
        assert "CheckViolation" in str(excinfo.value)
    finally:
        savepoint.rollback()


@pytest.mark.parametrize(
    ("table", "name"),
    [(table, name) for table, names in _CANONICALITY_CHECKS.items() for name in names],
)
def test_the_constraint_exists_and_is_validated(db_session, table, name):
    """``NOT VALID`` that is never validated proves nothing about stored rows.

    The migration adds each CHECK ``NOT VALID`` and validates it in a second
    statement, so that the exclusive lock covers the catalogue write and not
    the row scan. A revision that added the first statement and dropped the
    second would leave a constraint that binds new writes and says nothing
    about the 46,566 rows already there, and would look identical in every
    other test in this file.
    """
    validated = db_session.scalar(
        text(
            "SELECT convalidated FROM pg_constraint"
            " WHERE conname = :name AND conrelid = cast(:table AS regclass)"
        ),
        {"name": name, "table": table},
    )
    assert validated is True


def test_the_element_rule_is_case_sensitive_now(db_session):
    """``upper(element) = upper(ts_element)`` was tightened to plain equality.

    Asserted against the catalogue rather than by provocation. A row *can* tell
    the two definitions apart -- ``('C', 'c')`` satisfied the ``upper()`` form
    and violates this one -- but it violates
    ``ck_reaction_atom_map_pair_ts_element_canonical`` as well, so which of them
    reports it is PostgreSQL's choice of evaluation order rather than anything
    this schema states. Reading the definition asserts the claim itself: the
    rule is no longer case-blind, now that the reason it was case-blind has
    gone.
    """
    definition = db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_reaction_atom_map_pair_element_matches'"
        )
    )
    assert definition is not None
    assert "upper" not in definition.lower()
    assert "element = ts_element" in definition.replace("(", "").replace(")", "")
