"""Disposable-database contract for ``38b06819f099`` (#574).

The revision re-keys ``level_of_theory.lot_hash`` so basis names hash by
identity key. Seeded at its parent, with rows spelled the ways producers
spell them:

====================  ==================================================
seed row              what it pins
====================  ==================================================
``psi4``              already spelled like the key: hash must not move
``gaussian``          duplicate of ``psi4`` (the #572 pair): keeps its
                      old hash, because ``psi4`` already holds the key
``svp_upper``         both members of a group non-canonical: the smaller
``svp_lower``         id takes the key, the other keeps its old hash
``molpro_upper``      a lone non-canonical spelling: re-hashed
``star`` / ``star2``  ``6-31G*`` / ``6-31G**``: stay two keys
``f12_aux``           ``aux_basis`` is keyed too
====================  ==================================================

Every row keeps its ``public_ref`` and its verbatim names; no calculation
moves. The downgrade restores every hash exactly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.public_refs import make_content_ref
from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("38b06819f099")

_FIELDS = (
    "method", "basis", "aux_basis", "cabs_basis",
    "dispersion", "solvent", "solvent_model", "keywords",
)

#: key -> (method, basis, aux_basis)
_SEED: dict[str, tuple[str, str | None, str | None]] = {
    # Gaussian's row is older, so "smallest id wins" alone would hand it the
    # key that psi4 already holds -- a unique violation, not a merge.
    "gaussian": ("b3lyp", "def2tzvp", None),
    "psi4": ("b3lyp", "def2-tzvp", None),
    "svp_upper": ("pbe0", "Def2SVP", None),
    "svp_lower": ("pbe0", "def2svp", None),
    "molpro_upper": ("wb97xd", "cc-pVTZ", None),
    "star": ("b3lyp", "6-31G*", None),
    "star2": ("b3lyp", "6-31G**", None),
    "f12_aux": ("ccsd(t)-f12", "cc-pVTZ-F12", "cc-pVTZ-F12-MP2FIT"),
}


def _pre_574_hash(method, basis, aux_basis) -> str:
    payload: dict = dict.fromkeys(_FIELDS)
    payload.update(method=method, basis=basis, aux_basis=aux_basis)
    payload["spin_treatment"] = "unknown"
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _keyed_hash(method, basis, aux_basis) -> str:
    return _level_of_theory_hash(
        LevelOfTheoryRef(method=method, basis=basis, aux_basis=aux_basis)
    )


class _Harness:
    def __init__(self, prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(prefix)
        self.env = _db_env(self.db_name)
        self._url = _database_url
        self._admin = create_engine(
            _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        self._admin_conn = self._admin.connect()
        self._admin_conn.execute(text(f'CREATE DATABASE "{self.db_name}"'))
        self.engine = None
        self.root = Path(__file__).resolve().parents[2]

    def run(self, direction: str, revision: str):
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=self.root,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr[-4000:]
        self.engine = create_engine(self._url(self.db_name), pool_pre_ping=True)
        return completed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        try:
            self._admin_conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": self.db_name},
            )
            self._admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{self.db_name}"'))
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _Harness("lot_basis_key")
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
    for key, (method, basis, aux) in _SEED.items():
        old = _pre_574_hash(method, basis, aux)
        ids[key] = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, aux_basis, lot_hash, public_ref) "
                "VALUES (:m, :b, :a, :h, :r) RETURNING id"
            ),
            {
                "m": method,
                "b": basis,
                "a": aux,
                "h": old,
                # What the before_insert listener derives for a real row.
                "r": make_content_ref("lot", f"lot_hash:{old}"),
            },
        )
        ids[f"calc_{key}"] = conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, lot_id) "
                "VALUES (CAST('sp' AS calc_type), :e, :l) RETURNING id"
            ),
            {"e": entry_id, "l": ids[key]},
        )
    return ids


def _snapshot(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, public_ref, lot_hash, method, basis, aux_basis "
                "FROM level_of_theory"
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

    # public_ref and the verbatim names: unchanged on every row.
    assert set(after) == set(before)
    for row_id, (ref, _h, method, basis, aux) in before.items():
        assert (after[row_id][0], after[row_id][2:]) == (ref, (method, basis, aux))
    # No calculation moved.
    assert _calc_lots(harness.engine) == calcs_before

    def hash_of(key):
        return after[ids[key]][1]

    def keyed(key):
        return _keyed_hash(*_SEED[key])

    def old(key):
        return _pre_574_hash(*_SEED[key])

    # Already canonical: hash did not move, and it is the key.
    assert hash_of("psi4") == old("psi4") == keyed("psi4")
    # Its duplicate keeps the old hash; psi4 holds the key although it is newer.
    assert ids["gaussian"] < ids["psi4"]
    assert hash_of("gaussian") == old("gaussian") != keyed("gaussian")
    assert keyed("gaussian") == keyed("psi4")
    # Neither member canonical: the smaller id takes the key.
    assert ids["svp_upper"] < ids["svp_lower"]
    assert hash_of("svp_upper") == keyed("svp_upper")
    assert hash_of("svp_lower") == old("svp_lower")
    # Lone non-canonical spellings are re-hashed; aux_basis is keyed too.
    assert hash_of("molpro_upper") == keyed("molpro_upper") != old("molpro_upper")
    assert hash_of("f12_aux") == keyed("f12_aux") != old("f12_aux")
    # 6-31G* and 6-31G** are still two levels of theory.
    assert hash_of("star") == keyed("star") != hash_of("star2") == keyed("star2")

    # The operator is told which groups are left for the merge script.
    assert "2 duplicate group(s)" in completed.stdout
    assert before[ids["gaussian"]][0] in completed.stdout

    # The upload path now finds the existing rows under any spelling.
    # The resolver is application code and reads the current ``level_of_theory``
    # columns, which ``e5b2d8a4c613`` extended; bring the scratch database to the
    # head schema before asking it a question (the revision under test has
    # already been applied and checked above).
    harness.run("upgrade", "head")
    with Session(harness.engine) as session:
        for spelling, key in (
            ("Def2TZVP", "psi4"),
            ("def2tzvp", "psi4"),
            ("def2-SVP", "svp_upper"),
            ("cc-pvtz", "molpro_upper"),
        ):
            method = _SEED[key][0]
            lot = resolve_level_of_theory_ref(
                session, LevelOfTheoryRef(method=method, basis=spelling)
            )
            assert lot.id == ids[key], spelling
        session.rollback()


def test_downgrade_restores_every_hash_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    original = _snapshot(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    first_upgrade = _snapshot(harness.engine)
    assert first_upgrade != original
    assert _has_merge_table(harness.engine)

    # A merge done by the ops script between upgrade and downgrade: the
    # merged row keeps its ref, and the downgrade forgets only the pointer.
    with harness.engine.begin() as conn:
        params = {"h": ids["psi4"], "d": ids["gaussian"]}
        conn.execute(text("UPDATE calculation SET lot_id = :h WHERE lot_id = :d"), params)
        conn.execute(
            text(
                "INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) "
                "VALUES (:d, :h)"
            ),
            params,
        )

    harness.run("downgrade", _MIGRATION.parent)
    assert _snapshot(harness.engine) == original
    assert not _has_merge_table(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == first_upgrade


def _has_merge_table(engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'level_of_theory_merge'"
                )
            )
        )


def test_upgrade_on_an_empty_table_is_a_no_op(harness):
    harness.run("upgrade", _MIGRATION.parent)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout
    assert _snapshot(harness.engine) == {}


def _insert_lot(conn, method, basis, lot_hash) -> int:
    return conn.scalar(
        text(
            "INSERT INTO level_of_theory (method, basis, lot_hash) "
            "VALUES (:m, :b, :h) RETURNING id"
        ),
        {"m": method, "b": basis, "h": lot_hash},
    )


@pytest.mark.parametrize("basis", ["def2-TZVP", "def2-tzvp"])
def test_a_demo_seeded_row_beside_an_uploaded_twin_round_trips(harness, basis):
    """``seed_scientific_demo_data.py`` writes ``sha256("method|basis")``.

    So before this revision a seeded row and an uploaded row with identical
    content could coexist under two hashes. The pre-#574 formula maps both to
    one value, which only one row can hold on downgrade.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _insert_lot(
            conn, "b3lyp", basis, hashlib.sha256(f"b3lyp|{basis}".encode()).hexdigest()
        )
        uploaded = _insert_lot(conn, "b3lyp", basis, _pre_574_hash("b3lyp", basis, None))
    original = _snapshot(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    hashes = [row[1] for row in _snapshot(harness.engine).values()]
    assert len(set(hashes)) == 2
    assert _keyed_hash("b3lyp", basis, None) in hashes

    harness.run("downgrade", _MIGRATION.parent)
    downgraded = _snapshot(harness.engine)
    # The uploaded row gets its formula hash back exactly.
    assert downgraded[uploaded] == original[uploaded]
    # The seeded row keeps a unique hash; exact when its key never moved.
    if basis == "def2-tzvp":
        assert downgraded[seeded] == original[seeded]
    assert len({row[1] for row in downgraded.values()}) == 2
