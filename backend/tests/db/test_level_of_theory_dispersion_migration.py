"""Disposable-database contract for ``f3b8d5a1c702`` (#630).

The revision re-keys ``level_of_theory.lot_hash`` so dispersion-column synonyms
and a dispersion folded into the method hash by identity key. Seeded at its
parent (``d0a7c3b91e4f``) with rows spelled the ways producers spell them:

=====================  =====================================================
seed row               what it pins
=====================  =====================================================
``route``,             ``b3lyp`` with ``EmpiricalDispersion=GD3BJ`` and with
``gd3bj``,             ``gd3bj`` (ARC's Gaussian route), and ``b3lyp-d3(bj)``
``folded``             folded in: three older spellings of one level
``column``             ``b3lyp`` + ``d3bj``: already the key form, holds it
                       although its id is the largest
``p_a`` / ``p_h`` /    ``pbe`` + ``gd3bj`` (a stale duplicate of ``p_h`` at the
``p_b``                parent), ``pbe`` + ``GD3BJ`` and ``pbe-d3bj``: nobody
                       holds the key; of the rows that held their previous
                       formula's hash the smallest id takes it, so a stale
                       smaller id does not
``alias`` /            ``b3lyp-d3bj`` merged into a holder by an earlier merge
``alias_holder``       script run: the alias has the smaller id and must NOT
                       take the key
``zero_a`` /           ``b3lyp`` + ``gd3`` and ``b3lyp-d3zero``: zero damping
``zero_b``
``bare_d3``            ``b3lyp`` + ``d3``: never joined
``refit`` /            ``wb97x-d3bj`` (a refit functional) beside ``wb97x``
``wb97x_col``          + ``d3bj``: never joined
``contradict``         ``b3lyp-d3bj`` with ``dispersion=d3zero``: never split
=====================  =====================================================

Every row keeps its ``public_ref`` and its verbatim names; no calculation
moves. The downgrade restores every hash exactly, with or without the merge
script in between.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.public_refs import make_content_ref
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("f3b8d5a1c702")
_MIGRATION_FILE = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("f3b8d5a1c702_*.py")
)

_BASIS = "def2-tzvp"

#: Insertion order is id order. ``hash``: ``"prior"`` = it holds the parent's
#: key; ``"stale"`` = it was a duplicate the parent left un-re-hashed.
_SEED: dict[str, dict] = {
    "route": {"method": "b3lyp", "dispersion": "EmpiricalDispersion=GD3BJ", "hash": "prior"},
    "gd3bj": {"method": "b3lyp", "dispersion": "gd3bj", "hash": "prior"},
    "folded": {"method": "b3lyp-d3(bj)", "hash": "prior"},
    "p_a": {"method": "pbe", "dispersion": "gd3bj", "hash": "stale"},
    "p_h": {"method": "pbe", "dispersion": "GD3BJ", "hash": "prior"},
    "p_b": {"method": "pbe-d3bj", "hash": "prior"},
    "alias": {"method": "b3lyp-d3bj", "basis": "def2-svp", "hash": "stale"},
    "alias_holder": {"method": "b3lyp", "dispersion": "d3bj", "basis": "def2-svp", "hash": "prior"},
    "zero_a": {"method": "b3lyp", "dispersion": "gd3", "hash": "prior"},
    "zero_b": {"method": "b3lyp-d3zero", "hash": "prior"},
    "bare_d3": {"method": "b3lyp", "dispersion": "d3", "hash": "prior"},
    "refit": {"method": "wb97x-d3bj", "hash": "prior"},
    "wb97x_col": {"method": "wb97x", "dispersion": "d3bj", "hash": "prior"},
    "contradict": {"method": "b3lyp-d3bj", "dispersion": "d3zero", "hash": "prior"},
    "column": {"method": "b3lyp", "dispersion": "d3bj", "hash": "prior"},
}
_DEFAULTS = {
    "basis": _BASIS, "aux_basis": None, "cabs_basis": None, "dispersion": None, "solvent": None,
    "solvent_model": None, "keywords": None, "spin_treatment": None,
}


def _fields(key: str) -> dict:
    return {**_DEFAULTS, **{k: v for k, v in _SEED[key].items() if k != "hash"}}


def _mig():
    spec = importlib.util.spec_from_file_location("_mig_f3b8_db", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parent_hash(key: str) -> str:
    """The hash the parent revision gives this row (its own formula)."""
    return _mig()._lot_hash(SimpleNamespace(_mapping=_fields(key)), split=False)


def _stale(key: str) -> str:
    return hashlib.sha256(f"stale:{key}".encode()).hexdigest()


def _start_hash(key: str) -> str:
    return _stale(key) if _SEED[key]["hash"] == "stale" else _parent_hash(key)


def _keyed_hash(key: str) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(**_fields(key)))


@pytest.fixture
def harness():
    created = _Harness("lot_disp_key")
    yield created
    created.close()


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
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
    for key in _SEED:
        fields = _fields(key)
        old = _start_hash(key)
        ids[key] = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, dispersion, solvent, solvent_model, "
                "keywords, lot_hash, public_ref) "
                "VALUES (:method, :basis, :dispersion, :solvent, :solvent_model, :keywords, :h, :r) "
                "RETURNING id"
            ),
            {
                **{k: fields[k] for k in ("method", "basis", "dispersion", "solvent", "solvent_model", "keywords")},
                "h": old,
                "r": make_content_ref("lot", f"{key}:{old}"),
            },
        )
        ids[f"calc_{key}"] = conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, lot_id) "
                "VALUES (CAST('sp' AS calc_type), :e, :l) RETURNING id"
            ),
            {"e": entry_id, "l": ids[key]},
        )
    # An earlier merge: the alias is kept, resolving to its holder, and its
    # calculation moved to the holder first, as the script does.
    conn.execute(
        text("UPDATE calculation SET lot_id = :h WHERE lot_id = :a"),
        {"h": ids["alias_holder"], "a": ids["alias"]},
    )
    conn.execute(
        text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :h)"),
        {"a": ids["alias"], "h": ids["alias_holder"]},
    )
    return ids


def _snapshot(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, public_ref, lot_hash, method, basis, dispersion, solvent, "
                "solvent_model, keywords FROM level_of_theory"
            )
        ).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def _calc_lots(engine) -> dict[int, int]:
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT id, lot_id FROM calculation")).all())


def test_upgrade_rekeys_without_touching_refs_names_or_calculations(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    before = _snapshot(harness.engine)
    calcs_before = _calc_lots(harness.engine)

    completed = harness.run("upgrade", _MIGRATION.revision)
    after = _snapshot(harness.engine)

    assert set(after) == set(before)
    for row_id, row in before.items():
        assert (after[row_id][0], *after[row_id][2:]) == (row[0], *row[2:])
    assert _calc_lots(harness.engine) == calcs_before

    def hash_of(key):
        return after[ids[key]][1]

    def old(key):
        return before[ids[key]][1]

    # The four spellings of #630 are one key; the column-form row has the
    # largest id yet already holds it, so it keeps it and the others do not move.
    group = ("route", "gd3bj", "folded", "column")
    assert len({_keyed_hash(k) for k in group}) == 1
    assert max(ids[k] for k in group) == ids["column"]
    assert hash_of("column") == old("column") == _keyed_hash("column")
    for key in ("route", "gd3bj", "folded"):
        assert hash_of(key) == old(key) != _keyed_hash(key)
    # Nobody holds the key: p_h and p_b held their previous hash, the smaller id
    # takes it; the stale smaller id p_a does not.
    assert ids["p_a"] < ids["p_h"] < ids["p_b"]
    assert hash_of("p_h") == _keyed_hash("p_h") != old("p_h")
    assert hash_of("p_a") == old("p_a")
    assert hash_of("p_b") == old("p_b") != _keyed_hash("p_b")
    assert _keyed_hash("p_a") == _keyed_hash("p_h") == _keyed_hash("p_b")
    # The merged alias has the smaller id but is never the holder, never moves.
    assert ids["alias"] < ids["alias_holder"]
    assert hash_of("alias") == old("alias")
    assert hash_of("alias_holder") == old("alias_holder") == _keyed_hash("alias_holder")
    # Zero damping: gd3 and a folded d3zero are one level.
    assert _keyed_hash("zero_a") == _keyed_hash("zero_b")
    assert hash_of("zero_a") == _keyed_hash("zero_a")  # smallest id, nobody holds the key
    assert hash_of("zero_b") == old("zero_b")
    # Never joined.
    keys = ("column", "zero_a", "bare_d3", "refit", "wb97x_col", "contradict")
    assert len({hash_of(k) for k in keys}) == len(keys)
    assert len({_keyed_hash(k) for k in keys}) == len(keys)
    for key in ("bare_d3", "refit", "wb97x_col", "contradict"):
        assert hash_of(key) == old(key)

    # The operator is told which groups are left for the merge script.
    for holder, dups in (
        ("column", ("route", "gd3bj", "folded")),
        ("p_h", ("p_a", "p_b")),
        ("zero_a", ("zero_b",)),
    ):
        for dup in dups:
            holder_ref, dup_ref = before[ids[holder]][0], before[ids[dup]][0]
            assert f"{holder_ref} holds the key; also spelled as" in completed.stdout, holder
            assert dup_ref in completed.stdout, dup
    assert before[ids["alias"]][0] not in completed.stdout
    for key in ("refit", "wb97x_col", "bare_d3", "contradict"):
        assert before[ids[key]][0] not in completed.stdout
    assert "duplicate group(s)" in completed.stdout

    # The upload path now finds the existing rows under every spelling.
    # The resolver is application code and reads the current ``level_of_theory``
    # columns, which ``e5b2d8a4c613`` extended; bring the scratch database to the
    # head schema before asking it a question (the revision under test has
    # already been applied and checked above).
    harness.run("upgrade", "head")
    with Session(harness.engine) as session:
        for method, extra, key in (
            ("b3lyp", {"dispersion": "d3bj"}, "column"),
            ("b3lyp", {"dispersion": "EmpiricalDispersion=(GD3BJ)"}, "column"),
            ("B3LYP-GD3BJ", {}, "column"),
            ("pbe", {"dispersion": "D3(BJ)"}, "p_h"),
            ("b3lyp", {"dispersion": "GD3"}, "zero_a"),
            ("b3lyp-d3zero", {}, "zero_a"),
            ("b3lyp", {"dispersion": "d3"}, "bare_d3"),
            ("wb97x-d3(bj)", {}, "refit"),
        ):
            lot = resolve_level_of_theory_ref(
                session, LevelOfTheoryRef(method=method, basis=_BASIS, **extra)
            )
            assert lot.id == ids[key], (method, extra)
        session.rollback()


def test_downgrade_restores_every_hash_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
    original = _snapshot(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    first_upgrade = _snapshot(harness.engine)
    assert first_upgrade != original

    completed = harness.run("downgrade", _MIGRATION.parent)
    assert _snapshot(harness.engine) == original
    assert "NOT re-hashed" not in completed.stdout

    harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == first_upgrade


def test_upgrade_on_an_empty_table_is_a_no_op(harness):
    harness.run("upgrade", _MIGRATION.parent)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout
    assert _snapshot(harness.engine) == {}


def test_rows_already_in_key_form_are_left_alone(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        for method, dispersion in (("b3lyp", "d3bj"), ("b3lyp", None), ("wb97x-d3bj", None)):
            h = _level_of_theory_hash(
                LevelOfTheoryRef(method=method, basis=_BASIS, dispersion=dispersion)
            )
            conn.execute(
                text(
                    "INSERT INTO level_of_theory (method, basis, dispersion, lot_hash, public_ref) "
                    "VALUES (:m, :b, :d, :h, :r)"
                ),
                {"m": method, "b": _BASIS, "d": dispersion, "h": h, "r": make_content_ref("lot", h)},
            )
    before = _snapshot(harness.engine)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == before
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout


def test_upgrade_merge_script_then_downgrade_succeeds_and_is_exact(harness):
    """Run the merge script end to end, as the deploy steps say.

    The ``pbe`` group carries an approved calculation on its duplicate, so the
    script must refuse to move it and leave the group. The ``b3lyp`` group
    (the #630 four) carries none: the three older spellings merge into the
    column-form holder. The downgrade then succeeds and restores every hash.
    """
    from datetime import datetime

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
    from app.db.models.record_review import RecordReview

    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    with Session(harness.engine) as session:
        curator = AppUser(username="disp-curator", role=AppUserRole.curator)
        session.add(curator)
        session.flush()
        when = datetime(2026, 9, 1)
        session.add(
            RecordReview(
                record_type=SubmissionRecordType.calculation,
                record_id=ids["calc_p_a"],
                status=RecordReviewStatus.approved,
                reviewed_by=curator.id,
                reviewed_at=when,
                first_approved_at=when,
            )
        )
        session.commit()
    original = _snapshot(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)

    for args in ((), ("--commit",)):
        merged = subprocess.run(
            [
                "conda", "run", "-n", "tckdb_env", "python",
                "scripts/ops/merge_duplicate_levels_of_theory.py", *args,
            ],
            cwd=harness.root, env=harness.env, capture_output=True, text=True, check=False,
        )
        assert merged.returncode == 0, merged.stderr[-3000:]
    with harness.engine.connect() as conn:
        pairs = set(
            conn.execute(
                text("SELECT merged_lot_id, into_lot_id FROM level_of_theory_merge")
            ).all()
        )
    assert (ids["alias"], ids["alias_holder"]) in pairs  # pre-existing
    for dup in ("route", "gd3bj", "folded"):
        assert (ids[dup], ids["column"]) in pairs, (dup, merged.stdout[-3000:])
    assert (ids["zero_b"], ids["zero_a"]) in pairs
    assert (ids["p_a"], ids["p_h"]) not in pairs, merged.stdout[-3000:]
    assert (ids["p_b"], ids["p_h"]) not in pairs
    for key in ("bare_d3", "refit", "wb97x_col", "contradict"):
        assert all(ids[key] not in pair for pair in pairs), key
    assert "BLOCKED" in merged.stdout

    harness.run("downgrade", _MIGRATION.parent)
    assert _snapshot(harness.engine) == original
