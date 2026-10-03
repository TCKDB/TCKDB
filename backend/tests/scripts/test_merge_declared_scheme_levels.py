"""``merge_duplicate_levels_of_theory.py`` and declared-scheme levels (ADR 0021, P5).

A declared level's columns cannot reproduce its ``lot_hash`` (``method`` is the
generated label, everything else ``NULL``), so the script recomputes it **from the
bound scheme**. Held here:

* a declared level is hashed from its scheme: it is alone in its group, and the
  recomputed hash equals the one the upload path wrote (the two replicas agree);
* a *plain* level whose method is the same label text is not grouped with it;
* two rows bound to one scheme (which the unique ``lot_hash`` makes impossible
  through the application, so the second is built by hand) group, and the one
  carrying the scheme's hash is the holder;
* the bound scheme moves to the holder when a duplicate is merged away.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import text
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.composite_scheme import LevelOfTheoryComposite
from app.db.models.level_of_theory import LevelOfTheory
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.composite_scheme_resolution import declared_scheme_lot_hash
from tests import composite_p5_fixtures as f

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "merge_duplicate_levels_of_theory.py"


@pytest.fixture(scope="module")
def merge():
    spec = importlib.util.spec_from_file_location("merge_declared_lots", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _declared(session) -> LevelOfTheory:
    return resolve_level_of_theory_ref(session, LevelOfTheoryRef(composite_scheme=f.SCHEME_B))


def _groups_with(plan, level: LevelOfTheory):
    return [
        g
        for g in plan.groups
        if (g.holder and g.holder.row_id == level.id) or any(d.row.row_id == level.id for d in g.duplicates)
    ]


def test_a_declared_level_is_hashed_from_its_scheme_and_stands_alone(merge, db_session):
    level = _declared(db_session)
    definition_hash = db_session.scalar(
        text(
            "SELECT s.definition_hash FROM level_of_theory_composite c "
            "JOIN composite_scheme s ON s.id = c.scheme_id WHERE c.level_of_theory_id = :l"
        ),
        {"l": level.id},
    ).strip()
    # The script's recomputation is the upload path's own hash.
    assert merge._identity_hash({"id": level.id}, definition_hash) == level.lot_hash
    assert level.lot_hash == declared_scheme_lot_hash(definition_hash)
    assert merge._declared_definition_hashes(db_session, merge.read_schema(db_session))[level.id] == definition_hash
    assert _groups_with(merge.build_plan(db_session), level) == []


def test_a_plain_level_with_the_same_label_is_not_grouped_with_it(merge, db_session):
    declared = _declared(db_session)
    plain = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=declared.method))
    plan = merge.build_plan(db_session)
    assert _groups_with(plan, declared) == [] and _groups_with(plan, plain) == []


def test_without_the_scheme_recomputation_the_label_would_group_wrongly(merge, db_session):
    """What the recomputation prevents: hashing the columns of a declared row names a level that never existed."""
    declared = _declared(db_session)
    from_columns = merge._identity_hash({"method": declared.method, "basis": None})
    assert from_columns != declared.lot_hash


def test_two_rows_bound_to_one_scheme_group_and_the_hash_holder_keeps_the_binding(merge, db_session):
    holder = _declared(db_session)
    scheme_id = db_session.get(LevelOfTheoryComposite, holder.id).scheme_id
    # An orphaned duplicate as an interrupted older run could leave it: same scheme, an old hash.
    duplicate = LevelOfTheory(method=holder.method, lot_hash=hashlib.sha256(b"legacy-declared-row").hexdigest())
    db_session.add(duplicate)
    db_session.flush()
    db_session.add(LevelOfTheoryComposite(level_of_theory_id=duplicate.id, scheme_id=scheme_id, binding_source="declared"))
    db_session.flush()

    plan = merge.build_plan(db_session)
    [group] = _groups_with(plan, holder)
    assert group.holder.row_id == holder.id
    assert [d.row.row_id for d in group.duplicates] == [duplicate.id]

    merge.commit_plan(db_session, plan)
    assert db_session.get(LevelOfTheoryComposite, duplicate.id) is None
    assert db_session.get(LevelOfTheoryComposite, holder.id).scheme_id == scheme_id
    merged_into = db_session.scalar(
        text("SELECT into_lot_id FROM level_of_theory_merge WHERE merged_lot_id = :d"), {"d": duplicate.id}
    )
    assert merged_into == holder.id
