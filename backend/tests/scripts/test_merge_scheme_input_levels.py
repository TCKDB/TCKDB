"""Merging an input level of a declared composite scheme must not fork the scheme (#658).

A user scheme's ``definition_hash`` is taken over the ``lot_hash`` of each input
level, merges followed. If an input level is merged into another *after* the
scheme was deposited, the same recipe deposited again hashes differently and used
to create a second scheme and a second level of theory. Held here, through the
operator script that performs level merges:

* a level that is a scheme input is mergeable, and its term inputs follow the merge;
* the scheme's hash is re-derived from its stored terms and inputs (merges
  followed), never read back from the stored hash, which would be circular;
* a stale scheme is reunited with the scheme the canonical level yields: one live
  scheme, one live bound level, the old level merged into the new one, and the
  old scheme row kept (a frozen ref still resolves);
* the same recipe deposited again afterwards lands on that one scheme and level;
* the repair is idempotent, and leaves a scheme with no merged input alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import select, text
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.composite_scheme import CompositeScheme, CompositeSchemeTermInput, LevelOfTheoryComposite
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.composite_scheme_resolution import declared_scheme_lot_hash, stored_scheme_definition_hash
from tests import composite_p5_fixtures as f

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "merge_duplicate_levels_of_theory.py"


@pytest.fixture(scope="module")
def merge():
    spec = importlib.util.spec_from_file_location("merge_scheme_input_lots", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _deposit(session) -> LevelOfTheory:
    return resolve_level_of_theory_ref(session, LevelOfTheoryRef(composite_scheme=f.SCHEME_B))


def _deposit_then_split_qz(session):
    """A scheme deposited over the QZ level, then QZ becomes a pre-re-key duplicate.

    Returns ``(declared level, scheme, legacy QZ row, holder QZ row)``. QZ keeps
    its calculations and scheme inputs but carries an old hash; a new row holds the
    identity-keyed hash, which is what the script's plan calls the holder.
    """
    declared = _deposit(session)
    scheme = session.get(LevelOfTheoryComposite, declared.id).scheme
    qz_ref = LevelOfTheoryRef(**f.QZ)
    qz = resolve_level_of_theory_ref(session, qz_ref)
    # Rewrite history: QZ was deposited under an older hash formula (``legacy``), so the
    # scheme and its level were hashed over that. Nothing is merged yet, so the derivation
    # below reads the legacy hash, exactly as the upload path did back then.
    qz.lot_hash = hashlib.sha256(b"legacy-qz-spelling").hexdigest()
    qz.public_ref = "lot_legacyqz0000"  # a real legacy row's ref came from its old hash
    session.flush()
    scheme.definition_hash = stored_scheme_definition_hash(session, scheme)
    declared.lot_hash = declared_scheme_lot_hash(scheme.definition_hash)
    scheme.public_ref = "csch_legacyscheme000000000000"
    declared.public_ref = "lot_legacydeclared0"
    session.flush()
    holder = resolve_level_of_theory_ref(session, qz_ref)
    assert holder.id != qz.id
    return declared, scheme, qz, holder


def _live_declared_levels(session) -> list[int]:
    return list(
        session.scalars(
            text(
                "SELECT c.level_of_theory_id FROM level_of_theory_composite c "
                "WHERE c.binding_source = 'declared' AND c.level_of_theory_id NOT IN "
                "(SELECT merged_lot_id FROM level_of_theory_merge) ORDER BY 1"
            )
        )
    )


def _scheme_count(session) -> int:
    return len(list(session.scalars(select(CompositeScheme.id))))


def test_a_scheme_input_level_can_be_merged_and_its_inputs_follow(merge, db_session):
    declared, scheme, qz, holder = _deposit_then_split_qz(db_session)
    result = merge.commit_plan(db_session, merge.build_plan(db_session))

    assert [g.holder.row_id for g, _ in result.merged if g.holder.row_id == holder.id] == [holder.id]
    assert not [reason for g, reason in result.kept if g.holder and g.holder.row_id == holder.id]
    assert db_session.scalar(select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == qz.id)) == holder.id
    # No scheme input points at a merged row.
    pointing = db_session.scalar(
        text("SELECT count(*) FROM composite_scheme_term_input WHERE level_of_theory_id = :qz"), {"qz": qz.id}
    )
    assert pointing == 0


def test_merging_an_input_level_leaves_one_live_scheme_and_level(merge, db_session):
    declared, scheme, qz, holder = _deposit_then_split_qz(db_session)
    merge.commit_plan(db_session, merge.build_plan(db_session))

    live = _live_declared_levels(db_session)
    assert len(live) == 1
    assert live[0] != declared.id  # the stale level was merged into the re-derived one
    assert (
        db_session.scalar(select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == declared.id))
        == live[0]
    )
    new_scheme = db_session.get(LevelOfTheoryComposite, live[0]).scheme
    assert new_scheme.id != scheme.id
    # The re-derived scheme's hash is what its own stored terms and inputs derive.
    assert stored_scheme_definition_hash(db_session, new_scheme) == new_scheme.definition_hash
    # The old scheme row is kept: a release may have frozen its ref.
    assert db_session.get(CompositeScheme, scheme.id) is not None


def test_depositing_the_recipe_after_the_merge_reuses_the_reunited_scheme(merge, db_session):
    _deposit_then_split_qz(db_session)
    merge.commit_plan(db_session, merge.build_plan(db_session))
    live_before = _live_declared_levels(db_session)
    schemes_before = _scheme_count(db_session)

    again = _deposit(db_session)
    assert [again.id] == live_before
    assert _scheme_count(db_session) == schemes_before
    assert _live_declared_levels(db_session) == live_before


def test_a_fork_made_by_a_deposit_after_the_merge_is_reunited(merge, db_session):
    """The issue's scenario: merge first, redeposit forks, then the script reunites."""
    declared, scheme, qz, holder = _deposit_then_split_qz(db_session)
    db_session.add(LevelOfTheoryMerge(merged_lot_id=qz.id, into_lot_id=holder.id))
    db_session.flush()

    forked = _deposit(db_session)
    assert forked.id != declared.id, "precondition: the stale hash makes a redeposit create a second level"
    assert len(_live_declared_levels(db_session)) == 2

    merge.commit_plan(db_session, merge.build_plan(db_session))

    assert _live_declared_levels(db_session) == [forked.id]
    assert (
        db_session.scalar(select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == declared.id))
        == forked.id
    )
    # The stale scheme is no longer anyone's binding but is not deleted.
    assert db_session.get(CompositeScheme, scheme.id) is not None
    assert db_session.scalar(
        select(LevelOfTheoryComposite.level_of_theory_id).where(LevelOfTheoryComposite.scheme_id == scheme.id)
    ) is None


def test_the_repair_is_idempotent(merge, db_session):
    _deposit_then_split_qz(db_session)
    merge.commit_plan(db_session, merge.build_plan(db_session))
    live = _live_declared_levels(db_session)
    schemes = _scheme_count(db_session)

    second = merge.commit_plan(db_session, merge.build_plan(db_session))
    assert second.merged == []
    assert _live_declared_levels(db_session) == live
    assert _scheme_count(db_session) == schemes


def test_a_scheme_with_no_merged_input_is_left_alone(merge, db_session):
    declared = _deposit(db_session)
    scheme = db_session.get(LevelOfTheoryComposite, declared.id).scheme
    stored = scheme.definition_hash
    assert stored_scheme_definition_hash(db_session, scheme) == stored

    merge.commit_plan(db_session, merge.build_plan(db_session))
    assert _live_declared_levels(db_session) == [declared.id]
    assert db_session.get(CompositeScheme, scheme.id).definition_hash == stored


def test_the_derived_hash_reads_the_stored_inputs_not_the_stored_hash(merge, db_session):
    """The circularity the issue names: corrupting the stored hash must not change the derived one."""
    declared = _deposit(db_session)
    scheme = db_session.get(LevelOfTheoryComposite, declared.id).scheme
    honest = stored_scheme_definition_hash(db_session, scheme)
    scheme.definition_hash = hashlib.sha256(b"not the real one").hexdigest()
    db_session.flush()
    assert stored_scheme_definition_hash(db_session, scheme) == honest
    assert merge._declared_definition_hashes(db_session, merge.read_schema(db_session))[declared.id] == honest


def test_two_merged_inputs_derive_through_the_holder(merge, db_session):
    """Every input is followed through its merge, not just the first."""
    declared = _deposit(db_session)
    scheme = db_session.get(LevelOfTheoryComposite, declared.id).scheme
    inputs = list(db_session.scalars(select(CompositeSchemeTermInput)))
    levels = {i.level_of_theory_id for i in inputs}
    assert len(levels) == 2  # TZ and QZ
    before = stored_scheme_definition_hash(db_session, scheme)
    holders = {}
    for lid in levels:
        row = db_session.get(LevelOfTheory, lid)
        holders[lid] = LevelOfTheory(method=row.method, basis=row.basis, lot_hash=hashlib.sha256(f"h{lid}".encode()).hexdigest())
        db_session.add(holders[lid])
    db_session.flush()
    for lid, holder in holders.items():
        db_session.add(LevelOfTheoryMerge(merged_lot_id=lid, into_lot_id=holder.id))
    db_session.flush()
    assert stored_scheme_definition_hash(db_session, scheme) != before
