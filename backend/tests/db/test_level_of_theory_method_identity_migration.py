"""Disposable-database contract for ``c8424fe82997`` (#585).

The revision re-keys ``level_of_theory.lot_hash`` so method names hash by
identity key (lower case). Seeded at its parent (``38b06819f099``), with rows
spelled the ways producers spell them:

====================  ====================================================
seed row              what it pins
====================  ====================================================
``arc``               already lower case: hash must not move, and it holds
                      the key although it is newer than ``orca``
``orca``              duplicate of ``arc`` (the #585 pair): keeps its old
                      hash, because ``arc`` already holds the key
``lone_upper``        a lone upper-case method: re-hashed
``pair_upper``        neither member of the group holds the key: the
``pair_lower``        smaller id takes it, the other keeps its old hash
``alias`` /           ``PBE0/def2svp`` was merged into ``PBE0/def2-svp`` by
``alias_holder``      the #582 script. The alias has the smaller id and
                      must NOT take the key; the holder does
``wb97xd`` /          punctuation aliases stay two levels of theory
``wb97x_d``
``f12a`` / ``f12b``   F12a and F12b stay two levels of theory
====================  ====================================================

Every row keeps its ``public_ref`` and its verbatim names; no calculation
moves. The downgrade restores every hash exactly.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.public_refs import make_content_ref
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("c8424fe82997")

_FIELDS = (
    "method", "basis", "aux_basis", "cabs_basis",
    "dispersion", "solvent", "solvent_model", "keywords",
)

#: key -> (method, basis)
_SEED: dict[str, tuple[str, str | None]] = {
    # ORCA's row is older, so "smallest id wins" alone would hand it the key
    # that arc already holds -- a unique violation, not a merge.
    "orca": ("CCSD(T)-F12", "cc-pVTZ-F12"),
    "arc": ("ccsd(t)-f12", "cc-pvtz-f12"),
    "lone_upper": ("B3LYP", "6-31G(d)"),
    "pair_upper": ("DLPNO-CCSD(T)", "def2-tzvp"),
    "pair_lower": ("Dlpno-CCSD(T)", "def2-tzvp"),
    "alias": ("PBE0", "def2svp"),
    "alias_holder": ("PBE0", "def2-svp"),
    "wb97xd": ("wb97xd", "def2-tzvp"),
    "wb97x_d": ("wB97X-D", "def2-tzvp"),
    "f12a": ("CCSD(T)-F12a", "cc-pVTZ"),
    "f12b": ("CCSD(T)-F12b", "cc-pVTZ"),
    "no_basis": ("HF", None),
}


def _prior_hash(method, basis, *, verbatim_basis: bool = False) -> str:
    """The formula as ``38b06819f099`` left it: basis keyed, method verbatim."""
    payload: dict = dict.fromkeys(_FIELDS)
    payload.update(
        method=method, basis=basis if verbatim_basis else basis_identity_key(basis)
    )
    payload["spin_treatment"] = "unknown"
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _keyed_hash(method, basis) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))


@pytest.fixture
def harness():
    created = _Harness("lot_method_key")
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
    for key, (method, basis) in _SEED.items():
        # What #582's merge script left on the alias: its pre-#574 hash.
        old = _prior_hash(method, basis, verbatim_basis=(key == "alias"))
        ids[key] = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) "
                "VALUES (:m, :b, :h, :r) RETURNING id"
            ),
            {"m": method, "b": basis, "h": old, "r": make_content_ref("lot", f"lot_hash:{old}")},
        )
        ids[f"calc_{key}"] = conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, lot_id) "
                "VALUES (CAST('sp' AS calc_type), :e, :l) RETURNING id"
            ),
            {"e": entry_id, "l": ids[key]},
        )
    # The #582 merge: the alias is kept, resolving to its holder. Its
    # calculation moves to the holder first, as the script does.
    conn.execute(
        text("UPDATE calculation SET lot_id = :h WHERE lot_id = :a"),
        {"h": ids["alias_holder"], "a": ids["alias"]},
    )
    conn.execute(
        text(
            "INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :h)"
        ),
        {"a": ids["alias"], "h": ids["alias_holder"]},
    )
    return ids


def _snapshot(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, public_ref, lot_hash, method, basis FROM level_of_theory")
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
    for row_id, (ref, _h, method, basis) in before.items():
        assert (after[row_id][0], after[row_id][2:]) == (ref, (method, basis))
    assert _calc_lots(harness.engine) == calcs_before

    def hash_of(key):
        return after[ids[key]][1]

    def keyed(key):
        return _keyed_hash(*_SEED[key])

    def old(key):
        return before[ids[key]][1]

    # Already lower case: hash did not move, and it is the key.
    assert hash_of("arc") == old("arc") == keyed("arc")
    # Its duplicate keeps the old hash; arc holds the key although it is newer.
    assert ids["orca"] < ids["arc"]
    assert hash_of("orca") == old("orca") != keyed("orca")
    assert keyed("orca") == keyed("arc")
    # Neither member holds the key: the smaller id takes it.
    assert ids["pair_upper"] < ids["pair_lower"]
    assert hash_of("pair_upper") == keyed("pair_upper") != old("pair_upper")
    assert hash_of("pair_lower") == old("pair_lower")
    # A lone upper-case spelling is re-hashed; no basis is fine.
    assert hash_of("lone_upper") == keyed("lone_upper") != old("lone_upper")
    assert hash_of("no_basis") == keyed("no_basis") != old("no_basis")
    # The alias has the smaller id but is never the holder and never moves.
    assert ids["alias"] < ids["alias_holder"]
    assert hash_of("alias") == old("alias")
    assert hash_of("alias_holder") == keyed("alias_holder") != old("alias_holder")
    # Punctuation aliases and F12a/F12b stay four different levels of theory.
    assert len({hash_of(k) for k in ("wb97xd", "wb97x_d", "f12a", "f12b")}) == 4
    assert hash_of("wb97xd") == keyed("wb97xd")
    assert hash_of("f12a") == keyed("f12a")

    # The operator is told which groups are left for the merge script:
    # orca/arc and pair_upper/pair_lower, not the alias.
    assert "2 duplicate group(s)" in completed.stdout
    assert before[ids["orca"]][0] in completed.stdout
    assert before[ids["alias"]][0] not in completed.stdout

    # The upload path now finds the existing rows under any case.
    with Session(harness.engine) as session:
        for method, basis, key in (
            ("CCSD(T)-F12", "cc-pVTZ-F12", "arc"),
            ("ccsd(t)-f12", "cc-pvtz-f12", "arc"),
            ("b3lyp", "6-31G(d)", "lone_upper"),
            ("pbe0", "def2-svp", "alias_holder"),
            ("hf", None, "no_basis"),
        ):
            lot = resolve_level_of_theory_ref(
                session, LevelOfTheoryRef(method=method, basis=basis)
            )
            assert lot.id == ids[key], (method, basis)
        session.rollback()


def test_downgrade_restores_every_hash_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
    original = _snapshot(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    first_upgrade = _snapshot(harness.engine)
    assert first_upgrade != original

    harness.run("downgrade", _MIGRATION.parent)
    assert _snapshot(harness.engine) == original

    harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == first_upgrade


def test_upgrade_on_an_empty_table_is_a_no_op(harness):
    harness.run("upgrade", _MIGRATION.parent)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout
    assert _snapshot(harness.engine) == {}


def test_an_alias_beside_its_holder_needs_no_update(harness):
    """A holder that already holds the key is left alone; aliases stay put."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        holder_hash = _keyed_hash("pbe0", "def2-svp")
        holder = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, lot_hash) "
                "VALUES ('pbe0', 'def2-svp', :h) RETURNING id"
            ),
            {"h": holder_hash},
        )
        alias = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, lot_hash) "
                "VALUES ('PBE0', 'Def2SVP', :h) RETURNING id"
            ),
            {"h": _prior_hash("PBE0", "Def2SVP", verbatim_basis=True)},
        )
        conn.execute(
            text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :h)"),
            {"a": alias, "h": holder},
        )
    before = _snapshot(harness.engine)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert _snapshot(harness.engine) == before
    assert "0 row(s) re-hashed, 0 duplicate group(s)" in completed.stdout
