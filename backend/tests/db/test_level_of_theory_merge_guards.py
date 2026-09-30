"""The database refuses what the merge script never builds (#591 items 1 and 2).

``level_of_theory_merge`` states a one-hop rule (``into_lot_id`` is never
itself merged) and its neighbour says no calculation points at a merged row.
Both used to be enforced only by ``merge_duplicate_levels_of_theory.py``, so a
hand-written row, a later script or a restored dump could build a chain
A -> B -> C (a search by A's ref then finds nothing) or put a calculation on
a merged row. Revision ``e88231299733`` adds two triggers.

Runs inside the pytest transaction. Every refusal is raised by the trigger, so
each test also checks the message, and every refusal has a sibling that
succeeds, so a trigger that refused everything would not pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)

_MIGRATION = revision_under_test("e88231299733")


def _lot(session, name: str) -> LevelOfTheory:
    row = LevelOfTheory(
        method=f"guard-{name}", basis="def2-tzvp", lot_hash=f"{name:0<64}"[:64]
    )
    session.add(row)
    session.flush()
    return row


def _entry(session):
    return make_species_entry(
        session,
        make_species(session, smiles=unique_smiles(), inchi_key=next_inchi_key("LMG")),
    )


def _merge(session, merged: LevelOfTheory, into: LevelOfTheory) -> None:
    session.add(LevelOfTheoryMerge(merged_lot_id=merged.id, into_lot_id=into.id))
    session.flush()


def _refused(session, message: str, action) -> None:
    with pytest.raises(DBAPIError) as caught:
        with session.begin_nested():
            action()
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"
    assert message in str(caught.value.orig)


# ---------------------------------------------------------------------------
# One hop: no chain, no loop
# ---------------------------------------------------------------------------


def test_a_single_merge_is_accepted(db_session):
    a, b = _lot(db_session, "a1"), _lot(db_session, "b1")
    _merge(db_session, a, b)
    assert db_session.get(LevelOfTheoryMerge, a.id).into_lot_id == b.id


def test_two_merges_into_one_row_are_accepted(db_session):
    a, b, c = _lot(db_session, "a2"), _lot(db_session, "b2"), _lot(db_session, "c2")
    _merge(db_session, a, c)
    _merge(db_session, b, c)


def test_merging_into_a_row_that_is_itself_merged_is_refused(db_session):
    a, b, c = _lot(db_session, "a3"), _lot(db_session, "b3"), _lot(db_session, "c3")
    _merge(db_session, b, c)
    _refused(
        db_session,
        "cannot be merged into one that is itself merged",
        lambda: _merge(db_session, a, b),
    )


def test_merging_a_row_that_others_point_at_is_refused(db_session):
    """The chain built from the other end: A -> B exists, then B -> C."""
    a, b, c = _lot(db_session, "a4"), _lot(db_session, "b4"), _lot(db_session, "c4")
    _merge(db_session, a, b)
    _refused(
        db_session,
        "other merges point at cannot itself be merged",
        lambda: _merge(db_session, b, c),
    )


def test_a_loop_is_refused(db_session):
    a, b = _lot(db_session, "a5"), _lot(db_session, "b5")
    _merge(db_session, a, b)
    _refused(db_session, "merged", lambda: _merge(db_session, b, a))


def test_re_aiming_a_merge_at_a_new_holder_is_accepted(db_session):
    """What the merge script does: repoint, then record the new merge."""
    dup, holder, earlier = _lot(db_session, "d6"), _lot(db_session, "h6"), _lot(db_session, "e6")
    _merge(db_session, earlier, dup)
    db_session.execute(
        text("UPDATE level_of_theory_merge SET into_lot_id = :h WHERE into_lot_id = :d"),
        {"h": holder.id, "d": dup.id},
    )
    _merge(db_session, dup, holder)
    assert db_session.get(LevelOfTheoryMerge, earlier.id).into_lot_id == holder.id


def test_an_update_that_makes_a_chain_is_refused(db_session):
    a, b, c = _lot(db_session, "a7"), _lot(db_session, "b7"), _lot(db_session, "c7")
    _merge(db_session, a, b)
    _merge(db_session, c, b)
    # Re-aiming c's merge at a, which is merged, would make c -> a -> b.
    _refused(
        db_session,
        "cannot be merged into one that is itself merged",
        lambda: db_session.execute(
            text("UPDATE level_of_theory_merge SET into_lot_id = :a WHERE merged_lot_id = :c"),
            {"a": a.id, "c": c.id},
        ),
    )


# ---------------------------------------------------------------------------
# No calculation on a merged row
# ---------------------------------------------------------------------------


def test_a_calculation_on_a_kept_row_is_accepted(db_session):
    a, b = _lot(db_session, "a8"), _lot(db_session, "b8")
    _merge(db_session, a, b)
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id, lot_id=b.id)
    assert calc.lot_id == b.id


def test_a_new_calculation_on_a_merged_row_is_refused(db_session):
    a, b = _lot(db_session, "a9"), _lot(db_session, "b9")
    _merge(db_session, a, b)
    entry = _entry(db_session)
    _refused(
        db_session,
        "cannot use a level of theory that was merged into another",
        lambda: make_calculation(db_session, species_entry_id=entry.id, lot_id=a.id),
    )


def test_moving_a_calculation_onto_a_merged_row_is_refused(db_session):
    a, b = _lot(db_session, "aa"), _lot(db_session, "bb")
    other = _lot(db_session, "cc")
    _merge(db_session, a, b)
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id, lot_id=other.id)
    _refused(
        db_session,
        "cannot use a level of theory that was merged into another",
        lambda: db_session.execute(
            text("UPDATE calculation SET lot_id = :a WHERE id = :c"),
            {"a": a.id, "c": calc.id},
        ),
    )


def test_merging_a_row_calculations_still_use_is_refused(db_session):
    a, b = _lot(db_session, "ad"), _lot(db_session, "bd")
    make_calculation(db_session, species_entry_id=_entry(db_session).id, lot_id=a.id)
    _refused(
        db_session,
        "calculations still use cannot be merged",
        lambda: _merge(db_session, a, b),
    )


def test_a_calculation_already_on_a_merged_row_can_still_be_edited(db_session):
    """The guard fires when ``lot_id`` changes, not on every update.

    A database that already holds such a row (from before the trigger) must
    still let it be approved or corrected. The state is built with the merge
    guard off, inside this transaction, and restored on rollback.
    """
    a, b = _lot(db_session, "ae"), _lot(db_session, "be")
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id, lot_id=a.id)
    db_session.execute(text("ALTER TABLE level_of_theory_merge DISABLE TRIGGER trg_lot_merge_guard"))
    _merge(db_session, a, b)
    db_session.execute(text("ALTER TABLE level_of_theory_merge ENABLE TRIGGER trg_lot_merge_guard"))

    db_session.execute(
        text("UPDATE calculation SET quality = quality WHERE id = :c"), {"c": calc.id}
    )
    db_session.expire_all()
    assert db_session.get(Calculation, calc.id).lot_id == a.id
    # ... but it cannot be pointed at the merged row again from elsewhere.
    other = _lot(db_session, "af")
    calc2 = make_calculation(db_session, species_entry_id=_entry(db_session).id, lot_id=other.id)
    _refused(
        db_session,
        "cannot use a level of theory that was merged into another",
        lambda: db_session.execute(
            text("UPDATE calculation SET lot_id = :a WHERE id = :c"),
            {"a": a.id, "c": calc2.id},
        ),
    )


# ---------------------------------------------------------------------------
# The revision itself
# ---------------------------------------------------------------------------


def _guards(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname IN "
                    "('trg_lot_merge_guard', 'trg_calculation_lot_not_merged')"
                )
            ).all()
        )


@pytest.fixture
def harness():
    created = _Harness("lot_merge_guard")
    yield created
    created.close()


def test_the_revision_installs_and_removes_both_triggers_and_reports_state(harness):
    harness.run("upgrade", _MIGRATION.parent)
    assert _guards(harness.engine) == set()
    # A chain and a stranded calculation, built before the guards exist.
    with harness.engine.begin() as conn:
        ids = [
            conn.scalar(
                text(
                    "INSERT INTO level_of_theory (method, lot_hash) VALUES (:m, :h) RETURNING id"
                ),
                {"m": f"m{n}", "h": f"{n:0<64}"[:64]},
            )
            for n in range(1, 4)
        ]
        species_id = conn.scalar(
            text(
                "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                "VALUES (CAST('molecule' AS molecule_kind), 'O', "
                "'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, CAST('unspecified' AS stereo_kind)) "
                "RETURNING id"
            )
        )
        entry_id = conn.scalar(
            text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"),
            {"s": species_id},
        )
        conn.execute(
            text("INSERT INTO calculation (type, species_entry_id, lot_id) "
                 "VALUES (CAST('sp' AS calc_type), :e, :l)"),
            {"e": entry_id, "l": ids[0]},
        )
        for merged, into in ((ids[0], ids[1]), (ids[1], ids[2])):
            conn.execute(
                text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :b)"),
                {"a": merged, "b": into},
            )

    completed = harness.run("upgrade", _MIGRATION.revision)
    assert _guards(harness.engine) == {"trg_lot_merge_guard", "trg_calculation_lot_not_merged"}
    # Existing rows are reported, not rejected.
    assert "1 existing merge chain link(s), 1 existing calculation(s) on a merged row" in completed.stdout
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM level_of_theory_merge")) == 2

    downgraded = harness.run("downgrade", _MIGRATION.parent)
    assert _guards(harness.engine) == set()
    assert "The table holds 2 merge(s)" in downgraded.stdout
    harness.run("upgrade", _MIGRATION.revision)
    assert len(_guards(harness.engine)) == 2
