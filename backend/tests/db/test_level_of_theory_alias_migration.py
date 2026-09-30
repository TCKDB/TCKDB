"""Disposable-database contract for ``d0a7c3b91e4f`` (#618, #602).

The revision re-keys ``level_of_theory.lot_hash`` so curated method aliases
and dispersion / solvent / solvent-model case hash by identity key. Seeded at
its parent (``c8424fe82997``) with rows spelled the ways producers spell them:

=====================  =====================================================
seed row               what it pins
=====================  =====================================================
``wb97xd``             the ARC / Gaussian spelling; already the key, holds it
``wb97x_d``            the second ARC corpus (#618); older than ``wb97xd``,
                       keeps its old hash, and is a duplicate group
``m062x_stale`` /      ``M06-2X`` and ``m06-2x`` were one level at the parent
``m062x_holder``       (case), and neither holds ``m062x``: the row that held
                       the parent's key takes it, although its id is larger
``alias`` /            ``M06-2X`` merged into ``m06-2x`` by the parent's merge
``alias_holder``       script: the alias has the smaller id and must NOT take
                       the key
``d3bj_upper`` /       #602: dispersion case; the row already holding the key
``d3bj_lower``         keeps it
``water_upper`` /      #602: solvent and solvent-model case; neither holds
``water_lower``        the key
``orca_d3``            ORCA ``wB97X-D3``: never joined to ``wb97xd``
``folded_a`` /         ``b3lyp-d3(bj)`` and ``b3lyp-gd3bj``: one folded name
``folded_b``
``column``             ``b3lyp`` with ``dispersion=d3bj``: not the folded name
``kw_a`` / ``kw_b``    ``keywords`` stay verbatim: two levels of theory
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

_MIGRATION = revision_under_test("d0a7c3b91e4f")
_MIGRATION_FILE = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("d0a7c3b91e4f_*.py")
)

_BASIS = "def2-tzvp"

#: key -> fields of the row. ``hash`` says how the parent left its hash:
#: ``"prior"`` = it holds the parent's key; ``"stale"`` = it was a duplicate
#: the parent left un-re-hashed.
_SEED: dict[str, dict] = {
    # The older spelling is a duplicate; the newer one already holds the key.
    "wb97x_d": {"method": "wb97x-d", "hash": "prior"},
    "wb97xd": {"method": "wb97xd", "hash": "prior"},
    "m062x_stale": {"method": "M06-2X", "hash": "stale"},
    "m062x_holder": {"method": "m06-2x", "hash": "prior"},
    "alias": {"method": "M06-2X", "basis": "def2-svp", "hash": "stale"},
    "alias_holder": {"method": "m06-2x", "basis": "def2-svp", "hash": "prior"},
    "d3bj_upper": {"method": "b3lyp", "dispersion": "D3BJ", "hash": "prior"},
    "d3bj_lower": {"method": "b3lyp", "dispersion": "d3bj", "hash": "prior"},
    "water_upper": {"method": "b3lyp", "solvent": "Water", "solvent_model": "SMD", "hash": "prior"},
    "water_lower": {"method": "b3lyp", "solvent": "WATER", "solvent_model": "Smd", "hash": "prior"},
    "orca_d3": {"method": "wB97X-D3", "hash": "prior"},
    "folded_a": {"method": "b3lyp-d3(bj)", "hash": "prior"},
    "folded_b": {"method": "b3lyp-gd3bj", "hash": "prior"},
    "column": {"method": "b3lyp", "dispersion": "d3bj", "basis": "cc-pvdz", "hash": "prior"},
    "kw_a": {"method": "hf", "keywords": "Opt", "hash": "prior"},
    "kw_b": {"method": "hf", "keywords": "opt", "hash": "prior"},
}
_DEFAULTS = {
    "basis": _BASIS, "aux_basis": None, "cabs_basis": None, "dispersion": None, "solvent": None,
    "solvent_model": None, "keywords": None, "spin_treatment": None,
}


def _fields(key: str) -> dict:
    return {**_DEFAULTS, **{k: v for k, v in _SEED[key].items() if k != "hash"}}


def _mig():
    spec = importlib.util.spec_from_file_location("_mig_d0a7_db", _MIGRATION_FILE)
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
    fields = _fields(key)
    return _level_of_theory_hash(LevelOfTheoryRef(**fields))


@pytest.fixture
def harness():
    created = _Harness("lot_alias_key")
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

    # The older spelling keeps its hash; the newer one already held the key.
    assert ids["wb97x_d"] < ids["wb97xd"]
    assert hash_of("wb97xd") == old("wb97xd") == _keyed_hash("wb97xd")
    assert hash_of("wb97x_d") == old("wb97x_d") != _keyed_hash("wb97x_d")
    assert _keyed_hash("wb97x_d") == _keyed_hash("wb97xd")
    # Neither holds the key: the row that held the parent's key takes it,
    # although its id is larger. The stale row keeps its hash.
    assert ids["m062x_stale"] < ids["m062x_holder"]
    assert hash_of("m062x_holder") == _keyed_hash("m062x_holder") != old("m062x_holder")
    assert hash_of("m062x_stale") == old("m062x_stale")
    # The merged alias has the smaller id but is never the holder, never moves.
    assert ids["alias"] < ids["alias_holder"]
    assert hash_of("alias") == old("alias")
    assert hash_of("alias_holder") == _keyed_hash("alias_holder")
    # #602: dispersion case (the lower-case row already holds the key), and
    # solvent case (both held their own parent hash; the smaller id takes it).
    assert hash_of("d3bj_lower") == old("d3bj_lower") == _keyed_hash("d3bj_lower")
    assert hash_of("d3bj_upper") == old("d3bj_upper")
    assert ids["water_upper"] < ids["water_lower"]
    assert hash_of("water_upper") == _keyed_hash("water_upper") != old("water_upper")
    assert hash_of("water_lower") == old("water_lower")
    # The folded dispersion name: both spellings share the key.
    assert _keyed_hash("folded_a") == _keyed_hash("folded_b")
    assert {hash_of("folded_a"), hash_of("folded_b")} >= {_keyed_hash("folded_a")}
    # Never joined: ORCA wB97X-D3, the dispersion column, and verbatim keywords.
    distinct = {
        hash_of(k) for k in ("wb97xd", "orca_d3", "folded_a", "column", "kw_a", "kw_b")
    }
    assert len(distinct) == 6
    assert _keyed_hash("orca_d3") != _keyed_hash("wb97xd")
    assert _keyed_hash("column") != _keyed_hash("folded_a")
    assert hash_of("kw_a") == old("kw_a") and hash_of("kw_b") == old("kw_b")

    # The operator is told which groups are left for the merge script.
    for pair in (
        ("wb97xd", "wb97x_d"),
        ("m062x_holder", "m062x_stale"),
        ("d3bj_lower", "d3bj_upper"),
        ("water_upper", "water_lower"),
    ):
        holder_ref, dup_ref = before[ids[pair[0]]][0], before[ids[pair[1]]][0]
        assert f"{holder_ref} holds the key; also spelled as {dup_ref}" in completed.stdout, pair
    assert before[ids["alias"]][0] not in completed.stdout
    assert before[ids["orca_d3"]][0] not in completed.stdout
    assert "duplicate group(s)" in completed.stdout

    # The upload path now finds the existing rows under every spelling.
    with Session(harness.engine) as session:
        for method, basis, extra, key in (
            ("wb97x-d", _BASIS, {}, "wb97xd"),
            ("WB97XD", _BASIS, {}, "wb97xd"),
            ("m062x", _BASIS, {}, "m062x_holder"),
            ("b3lyp", _BASIS, {"dispersion": "d3BJ"}, "d3bj_lower"),
            ("b3lyp", _BASIS, {"solvent": "WATER", "solvent_model": "Smd"}, "water_upper"),
            ("b3lyp-gd3bj", _BASIS, {}, "folded_a"),
        ):
            lot = resolve_level_of_theory_ref(
                session, LevelOfTheoryRef(method=method, basis=basis, **extra)
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
        for method, basis in (("wb97xd", _BASIS), ("m062x", _BASIS), ("b3lyp", "cc-pvdz")):
            h = _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
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


def test_upgrade_merge_script_then_downgrade_succeeds_and_is_exact(harness):
    """Run the merge script end to end, as the deploy steps say.

    ``wb97x-d`` (older, carrying an approved calculation) is a duplicate of
    ``wb97xd``: the script must refuse to move approved science and leave the
    group. ``D3BJ`` / ``d3bj`` carries none: it merges. The downgrade then
    succeeds and restores every hash.
    """
    from datetime import datetime

    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
    from app.db.models.record_review import RecordReview

    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    with Session(harness.engine) as session:
        curator = AppUser(username="alias-curator", role=AppUserRole.curator)
        session.add(curator)
        session.flush()
        when = datetime(2026, 9, 1)
        session.add(
            RecordReview(
                record_type=SubmissionRecordType.calculation,
                record_id=ids["calc_wb97x_d"],
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
    # Pre-existing merge, plus the unblocked groups. The approved calculation
    # blocks wb97x-d; the kw_a / kw_b and orca_d3 rows were never a group.
    assert (ids["alias"], ids["alias_holder"]) in pairs
    assert (ids["d3bj_upper"], ids["d3bj_lower"]) in pairs
    assert (ids["m062x_stale"], ids["m062x_holder"]) in pairs
    assert (ids["water_lower"], ids["water_upper"]) in pairs
    assert (ids["wb97x_d"], ids["wb97xd"]) not in pairs, merged.stdout[-3000:]
    assert all(ids["orca_d3"] not in pair for pair in pairs)
    assert all(ids["kw_a"] not in pair and ids["kw_b"] not in pair for pair in pairs)
    assert "BLOCKED" in merged.stdout

    harness.run("downgrade", _MIGRATION.parent)
    assert _snapshot(harness.engine) == original
