"""Disposable-database contract for ``b9e4c2a7d153`` (ADR 0021).

The revision re-keys ``level_of_theory.lot_hash`` so the named composite
methods' alias spellings hash by identity key. Seeded at its parent with rows
spelled the ways producers spell them:

=====================  =====================================================
seed row               what it pins
=====================  =====================================================
``cbsqb3``             Arkane's spelling; older than ``CBS-QB3``, keeps its
                       old hash, and is a duplicate group
``cbs_qb3``            the Gaussian spelling; already the key, holds it
``g4mp2_stale`` /      ``G4(MP2)`` and ``g4(mp2)`` were one level at the
``g4mp2_holder``       parent (case), and neither holds ``g4mp2``: the row
                       that held the parent's key takes it, although its id
                       is larger
``alias`` /            ``G4(MP2)`` merged into ``g4(mp2)`` by the parent's
``alias_holder``       merge script: the alias has the smaller id and must
                       NOT take the key
``rocbsqb3`` /         ROCBS-QB3, as for CBS-QB3
``rocbs_upper``
``g3b3_paren`` /       ``G3(MP2)B3`` beside the Gaussian keyword
``g3b3_kw``
``w1bd`` / ``w1_bd``   ``W1-BD`` is not an alias: never joined
``w1u`` / ``w1ro``     the other W1 recipes: never joined
``rocbs_vs_cbs``       ``ROCBS-QB3`` is not ``CBS-QB3``
``paraskevas`` /       correction-table names are not methods: never joined
``year``
``cbs_basis``          ``cbsqb3`` with another basis: a different level
=====================  =====================================================

Every row keeps its ``public_ref`` and its verbatim names; no calculation
moves. The downgrade restores every hash exactly, with or without the merge
script in between.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.services.public_refs import make_content_ref
from tests._frozen_lot_hash import frozen_level_of_theory_hash
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("b9e4c2a7d153")
_MIGRATION_FILE = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("b9e4c2a7d153_*.py")
)

#: key -> fields of the row. ``hash`` says how the parent left its hash:
#: ``"prior"`` = it holds the parent's key; ``"stale"`` = it was a duplicate
#: the parent left un-re-hashed.
_SEED: dict[str, dict] = {
    "cbsqb3": {"method": "cbsqb3", "hash": "prior"},
    "cbs_qb3": {"method": "CBS-QB3", "hash": "prior"},
    "g4mp2_stale": {"method": "G4(MP2)", "hash": "stale"},
    "g4mp2_holder": {"method": "g4(mp2)", "hash": "prior"},
    "alias": {"method": "G4(MP2)", "basis": "def2-svp", "hash": "stale"},
    "alias_holder": {"method": "g4(mp2)", "basis": "def2-svp", "hash": "prior"},
    "rocbsqb3": {"method": "rocbsqb3", "hash": "prior"},
    "rocbs_upper": {"method": "ROCBS-QB3", "hash": "prior"},
    "g3b3_paren": {"method": "G3(MP2)B3", "hash": "prior"},
    "g3b3_kw": {"method": "g3mp2b3", "hash": "prior"},
    "w1bd": {"method": "W1BD", "hash": "prior"},
    "w1_bd": {"method": "W1-BD", "hash": "prior"},
    "w1u": {"method": "W1U", "hash": "prior"},
    "w1ro": {"method": "W1RO", "hash": "prior"},
    "paraskevas": {"method": "cbs-qb3-paraskevas", "hash": "prior"},
    "year": {"method": "cbsqb32023", "hash": "prior"},
    "cbs_basis": {"method": "cbsqb3", "basis": "cbsb7", "hash": "prior"},
    "wb97xd": {"method": "wb97x-d", "hash": "prior"},
    # #630 (f3b8d5a1c702) already keyed these; this revision must keep them keyed.
    "disp_gd3bj": {"method": "B3LYP", "dispersion": "GD3BJ", "hash": "prior"},
    "disp_gd3": {"method": "b3lyp", "dispersion": "EmpiricalDispersion=GD3", "hash": "prior"},
    "disp_folded": {"method": "PBE0-D3(BJ)", "hash": "prior"},
}
_DEFAULTS = {
    "basis": None, "aux_basis": None, "cabs_basis": None, "dispersion": None, "solvent": None,
    "solvent_model": None, "keywords": None, "spin_treatment": None,
}


def _fields(key: str) -> dict:
    return {**_DEFAULTS, **{k: v for k, v in _SEED[key].items() if k != "hash"}}


def _mig():
    spec = importlib.util.spec_from_file_location("_mig_b9e4_db", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parent_hash(key: str) -> str:
    """The hash the parent revision gives this row (its own formula)."""
    return _mig()._lot_hash(SimpleNamespace(_mapping=_fields(key)), aliased=False)


def _stale(key: str) -> str:
    return hashlib.sha256(f"stale:{key}".encode()).hexdigest()


def _start_hash(key: str) -> str:
    return _stale(key) if _SEED[key]["hash"] == "stale" else _parent_hash(key)


def _keyed_hash(key: str) -> str:
    return frozen_level_of_theory_hash(**_fields(key))


@pytest.fixture
def harness():
    created = _Harness("lot_composite_alias_key")
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
    # The parent's merge: the alias is kept, resolving to its holder, and its
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
        # Everything but the hash: ref and every verbatim name.
        assert (after[row_id][0], *after[row_id][2:]) == (row[0], *row[2:])
    assert _calc_lots(harness.engine) == calcs_before

    def hash_of(key):
        return after[ids[key]][1]

    def old(key):
        return before[ids[key]][1]

    # The older spelling keeps its hash; the Gaussian spelling already held the key.
    assert ids["cbsqb3"] < ids["cbs_qb3"]
    assert hash_of("cbs_qb3") == old("cbs_qb3") == _keyed_hash("cbs_qb3")
    assert hash_of("cbsqb3") == old("cbsqb3") != _keyed_hash("cbsqb3")
    assert _keyed_hash("cbsqb3") == _keyed_hash("cbs_qb3")
    # Neither holds the key: the row that held the parent's key takes it,
    # although its id is larger. The stale row keeps its hash.
    assert ids["g4mp2_stale"] < ids["g4mp2_holder"]
    assert hash_of("g4mp2_holder") == _keyed_hash("g4mp2_holder") != old("g4mp2_holder")
    assert hash_of("g4mp2_stale") == old("g4mp2_stale")
    # The merged alias has the smaller id but is never the holder, never moves.
    assert ids["alias"] < ids["alias_holder"]
    assert hash_of("alias") == old("alias")
    assert hash_of("alias_holder") == _keyed_hash("alias_holder")
    # Restricted-open-shell and the parenthesised G3 keyword.
    assert hash_of("rocbs_upper") == old("rocbs_upper") == _keyed_hash("rocbs_upper")
    assert _keyed_hash("rocbsqb3") == _keyed_hash("rocbs_upper")
    assert hash_of("g3b3_kw") == old("g3b3_kw") == _keyed_hash("g3b3_kw")
    assert _keyed_hash("g3b3_paren") == _keyed_hash("g3b3_kw")
    # #630's dispersion keys survive: re-pointing the chain without freezing them
    # would re-hash these three back to their pre-#630 hashes.
    for key in ("disp_gd3bj", "disp_gd3", "disp_folded"):
        assert hash_of(key) == old(key) == _keyed_hash(key), key
    # The #618 aliases are untouched: wb97x-d was already keyed by the parent.
    assert hash_of("wb97xd") == old("wb97xd")
    # Never joined: the W1 recipes, W1-BD, ROCBS-QB3 vs CBS-QB3, the
    # correction-table names, and another basis.
    distinct = {
        hash_of(k)
        for k in (
            "cbs_qb3", "rocbs_upper", "w1bd", "w1_bd", "w1u", "w1ro", "paraskevas", "year",
            "cbs_basis", "g3b3_kw",
        )
    }
    assert len(distinct) == 10
    for key in ("w1bd", "w1_bd", "w1u", "w1ro", "paraskevas", "year"):
        assert hash_of(key) == old(key) == _keyed_hash(key), key
    # ``cbsqb3`` with another basis is the only holder of its own key, so it
    # moves to it; it stays a different level from the no-basis one.
    assert hash_of("cbs_basis") == _keyed_hash("cbs_basis") != old("cbs_basis")
    assert _keyed_hash("w1_bd") != _keyed_hash("w1bd")
    assert _keyed_hash("paraskevas") != _keyed_hash("cbs_qb3")
    assert _keyed_hash("year") != _keyed_hash("cbs_qb3")

    # The operator is told which groups are left for the merge script.
    for pair in (
        ("cbs_qb3", "cbsqb3"),
        ("g4mp2_holder", "g4mp2_stale"),
        ("rocbs_upper", "rocbsqb3"),
        ("g3b3_kw", "g3b3_paren"),
    ):
        holder_ref, dup_ref = before[ids[pair[0]]][0], before[ids[pair[1]]][0]
        assert f"{holder_ref} holds the key; also spelled as {dup_ref}" in completed.stdout, pair
    assert before[ids["alias"]][0] not in completed.stdout
    for key in (
        "w1bd", "w1_bd", "w1u", "w1ro", "paraskevas", "year", "wb97xd",
        "disp_gd3bj", "disp_gd3", "disp_folded",
    ):
        assert before[ids[key]][0] not in completed.stdout, key
    assert "duplicate group(s)" in completed.stdout

    # Stage 2 (keywords decision): the upload path is not asked here whether it finds these re-keyed rows.
    # The application's hash formula no longer has the ``keywords`` key this revision's rows were hashed with,
    # so a resolver run against them would (by design) make new rows; the instance is reseeded, not re-keyed
    # (``d5f2b8c1a704`` refuses to run over them). The frozen rules are held to the application's identity
    # keys in tests/services/ instead.


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
        for method, basis in (("cbs-qb3", None), ("g4mp2", None), ("wb97xd", "def2-tzvp")):
            h = frozen_level_of_theory_hash(method=method, basis=basis)
            conn.execute(
                text(
                    "INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) "
                    "VALUES (:m, :b, :h, :r)"
                ),
                {"m": method, "b": basis, "h": h, "r": make_content_ref("lot", h)},
            )
    before = _snapshot(harness.engine)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == before
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout


# The end-to-end "run the merge script between upgrade and downgrade" test that was here is retired (Stage 2,
# keywords decision). The script groups rows by the application's *current* hash formula and needs a row that holds
# it to keep; the rows a historical revision leaves hold that revision's formula, which has since lost its
# ``keywords`` key, so no row holds the key and every group is (correctly) blocked. The script is held to the
# current formula by tests/scripts/test_merge_duplicate_levels_of_theory.py, and this revision's re-keying and
# downgrade by the other tests in this file.
