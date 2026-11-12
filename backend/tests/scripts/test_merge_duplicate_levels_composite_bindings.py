"""The merge script and named-composite bindings: a duplicate's binding moves to the holder, or blocks the merge.

Runs inside the pytest transaction. The holder carries the hash the application computes; the duplicate carries a stale
one, so the script groups them and merges the duplicate into the holder.
"""

from __future__ import annotations

from sqlalchemy import text

from app.services.composite_scheme_resolution import ensure_named_method_binding
from tests.scripts.test_merge_duplicate_levels_of_theory import _group, _lot, merge  # noqa: F401


def _holder_and_duplicate(db_session):
    holder = _lot(db_session, "CBS-QB3", "zz-holder", holder=True)
    duplicate = _lot(db_session, "cbsqb3", "zz-dup", holder=False)
    # Same identity under the keyed hash: the duplicate's basis differs only in spelling.
    db_session.execute(text("UPDATE level_of_theory SET basis = 'zz-holder' WHERE id = :i"), {"i": duplicate.id})
    db_session.execute(text("UPDATE level_of_theory SET method = 'CBS-QB3' WHERE id = :i"), {"i": duplicate.id})
    db_session.flush()
    db_session.refresh(duplicate)
    return holder, duplicate


def _bindings(db_session) -> dict[int, int]:
    return dict(db_session.execute(text("SELECT level_of_theory_id, scheme_id FROM level_of_theory_composite")).all())


def _merged_pairs(db_session) -> set[tuple[int, int]]:
    return set(db_session.execute(text("SELECT merged_lot_id, into_lot_id FROM level_of_theory_merge")).all())


def test_merge_script_moves_a_duplicates_binding_to_the_holder(merge, db_session):  # noqa: F811
    holder, duplicate = _holder_and_duplicate(db_session)
    ensure_named_method_binding(db_session, holder)
    ensure_named_method_binding(db_session, duplicate)
    assert holder.id in _bindings(db_session) and duplicate.id in _bindings(db_session)

    merge.commit_plan(db_session, merge.build_plan(db_session))

    assert (duplicate.id, holder.id) in _merged_pairs(db_session)
    bindings = _bindings(db_session)
    assert duplicate.id not in bindings
    assert holder.id in bindings


def test_merge_script_gives_the_holder_the_duplicates_binding_when_only_the_duplicate_is_bound(merge, db_session):  # noqa: F811
    holder, duplicate = _holder_and_duplicate(db_session)
    binding = ensure_named_method_binding(db_session, duplicate)
    scheme_id = binding.scheme_id
    assert holder.id not in _bindings(db_session)

    merge.commit_plan(db_session, merge.build_plan(db_session))

    assert (duplicate.id, holder.id) in _merged_pairs(db_session)
    bindings = _bindings(db_session)
    assert duplicate.id not in bindings
    assert bindings[holder.id] == scheme_id


def test_merge_script_blocks_a_group_whose_levels_are_bound_to_different_schemes(merge, db_session):  # noqa: F811
    holder, duplicate = _holder_and_duplicate(db_session)
    ensure_named_method_binding(db_session, holder)
    ensure_named_method_binding(db_session, duplicate)
    other = db_session.scalar(text("SELECT id FROM composite_scheme WHERE id <> :s LIMIT 1"), {"s": _bindings(db_session)[holder.id]})
    if other is None:
        g4 = _lot(db_session, "G4(MP2)", "zz-g4", holder=True)
        other = ensure_named_method_binding(db_session, g4).scheme_id
    db_session.execute(
        text("UPDATE level_of_theory_composite SET scheme_id = :s WHERE level_of_theory_id = :h"),
        {"s": other, "h": holder.id},
    )
    db_session.flush()
    before = _bindings(db_session)

    plan = merge.build_plan(db_session)
    group = _group(plan, holder)

    assert any("differs from the kept row" in reason for reason in group.blockers())
    merge.commit_plan(db_session, plan)
    assert (duplicate.id, holder.id) not in _merged_pairs(db_session)
    assert _bindings(db_session) == before
