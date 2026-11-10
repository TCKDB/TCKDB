"""``level_of_theory.core_treatment`` joins the identity hash only when stated (ADR 0021, P4).

Frozen-core and all-electron CCSD(T)/cc-pCVTZ are different levels of theory
(their difference is the core-valence term of a focal-point scheme), and until
this field existed they collapsed to one row. The field is hashed **only when it is set**, so a
level that does not state it keeps the hash it has without the field: the field itself
adds no key and re-keys no row. (Stage 2, the keywords decision, did change every hash by removing the ``keywords``
key; that is separate from this field.)

What is held here:

* the hash of a level with no core treatment equals what ``main`` computed
  before the field existed (literals produced by the pre-change code, not by
  this module) and equals an independent restatement of the old formula;
* frozen-core, all-electron and unstated are three different hashes;
* every replica of the hash agrees: the application function, the identity
  hash the merge script recomputes from stored columns, and the frozen
  migration copy (which knows nothing of the field, and is only ever run over
  rows that have none);
* the merge script regroups by the recomputed hash, so it must not fold a
  frozen-core level into an all-electron one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from tckdb_schemas.enums import CoreTreatment as WireCoreTreatment
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import level_identity_keys
from app.chemistry.lot_component_names import component_identity_key
from app.db.models.common import CoreTreatment
from app.db.models.level_of_theory import LevelOfTheory
from app.services.calculation_resolution import _level_of_theory_hash, resolve_level_of_theory_ref
from app.services.scientific_read.lot_identity_filters import method_matches
from tests._frozen_lot_hash import frozen_level_of_theory_hash

_VERSIONS = pathlib.Path(__file__).parents[2] / "alembic" / "versions"
_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "merge_duplicate_levels_of_theory.py"

#: Levels that state no method qualifier, pinned as the application computes them after the keywords decision
#: (Stage 2: the free-text ``keywords`` key left the payload, so every hash differs from the pre-Stage-2 one by
#: design; the instance is reseeded, not re-keyed). A change to any of these from here on is a re-key of every
#: level of theory that spelled itself this way: a migration, not a test edit.
_NO_QUALIFIER_HASHES: list[tuple[dict, str]] = [
    ({"method": "wb97xd", "basis": "def2tzvp"}, "1ba2d643d5757c406f5c28afe2e5f6314ca850a02833b6fbeda4f3347c7baa0d"),
    ({"method": "CCSD(T)", "basis": "cc-pCVTZ"}, "6fa4d7f048b76844d934505442b982faed2dde442ff347ae1013a94d83e3691b"),
    (
        {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12", "cabs_basis": "cc-pVTZ-F12-CABS"},
        "70e98893e288a3ee2e4a204435db2c0641a9e2258a612ae6b1af9d54390f5b6f",
    ),
    (
        {"method": "MRCI+Davidson", "basis": "aug-cc-pV(T+d)Z"},
        "4955157382ef4be0539748ea19418864fc2ec006023520a04715cf60b5e74c44",
    ),
    (
        {
            "method": "b3lyp",
            "basis": "def2tzvp",
            "dispersion": "d3bj",
            "solvent": "water",
            "solvent_model": "smd",
            "spin_treatment": "unrestricted",
        },
        "0ca9b853d518fedc3b32b703138f69772913522db035f17adf03af723cde5666",
    ),
    ({"method": "CBS-QB3"}, "112ef295e50169ffd82d88aee89e30359e4462c5131a7270f45698d0b6c76495"),
    ({"method": "cbsqb3"}, "112ef295e50169ffd82d88aee89e30359e4462c5131a7270f45698d0b6c76495"),
]


def _old_formula(ref: LevelOfTheoryRef) -> str:
    """The hash payload with no stated method qualifier: eight keys, no core treatment."""
    method_key, dispersion_key = level_identity_keys(ref.method, ref.dispersion)
    payload = {
        "method": method_key,
        "basis": basis_identity_key(ref.basis),
        "aux_basis": basis_identity_key(ref.aux_basis),
        "cabs_basis": basis_identity_key(ref.cabs_basis),
        "dispersion": dispersion_key,
        "solvent": component_identity_key(ref.solvent),
        "solvent_model": component_identity_key(ref.solvent_model),
        "spin_treatment": getattr(ref.spin_treatment, "value", ref.spin_treatment) or "unknown",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("fields", "expected"), _NO_QUALIFIER_HASHES)
def test_a_level_with_no_core_treatment_keeps_the_hash_it_had_before_the_field(fields, expected):
    ref = LevelOfTheoryRef(**fields)
    assert ref.core_treatment is None
    assert _level_of_theory_hash(ref) == expected
    assert _old_formula(ref) == expected


def test_the_pinned_corpus_is_not_vacuous():
    # A list a refactor emptied would pass every parametrised case above.
    assert len(_NO_QUALIFIER_HASHES) >= 7
    assert len({h for _f, h in _NO_QUALIFIER_HASHES}) == 6  # cbsqb3 and CBS-QB3 are one level


def test_frozen_core_all_electron_and_unstated_are_three_levels():
    base = {"method": "CCSD(T)", "basis": "cc-pCVTZ"}
    unstated = _level_of_theory_hash(LevelOfTheoryRef(**base))
    frozen = _level_of_theory_hash(LevelOfTheoryRef(**base, core_treatment="frozen_core"))
    full = _level_of_theory_hash(LevelOfTheoryRef(**base, core_treatment="all_electron"))
    assert len({unstated, frozen, full}) == 3
    # "Not stated" is not "frozen core by default": the unstated level is the pre-P4 one.
    assert unstated == _old_formula(LevelOfTheoryRef(**base))


def test_the_hash_is_the_same_for_the_enum_and_for_its_value():
    base = {"method": "CCSD(T)", "basis": "cc-pCVTZ"}
    by_string = _level_of_theory_hash(LevelOfTheoryRef(**base, core_treatment="frozen_core"))
    by_wire_enum = _level_of_theory_hash(LevelOfTheoryRef(**base, core_treatment=WireCoreTreatment.frozen_core))
    by_db_enum = _level_of_theory_hash(LevelOfTheoryRef(**base, core_treatment=CoreTreatment.frozen_core))
    assert by_string == by_wire_enum == by_db_enum


def test_core_treatment_is_independent_of_the_other_identity_fields():
    one = LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core")
    other_basis = LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ", core_treatment="frozen_core")
    assert _level_of_theory_hash(one) != _level_of_theory_hash(other_basis)


def test_an_unknown_core_treatment_is_refused_on_the_wire():
    with pytest.raises(ValueError):
        LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment="windowed")


# ---------------------------------------------------------------------------
# Replicas
# ---------------------------------------------------------------------------


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def merge():
    return _load("merge_duplicate_lots_core_treatment", _SCRIPT)


def test_the_stored_row_and_the_merge_script_rehash_to_the_same_value(db_session, merge):
    """Column -> hash (merge script, from stored text) agrees with ref -> hash (resolution)."""
    for core in (None, "frozen_core", "all_electron", "window"):
        ref = LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment=core)
        row = resolve_level_of_theory_ref(db_session, ref)
        db_session.flush()
        assert (row.core_treatment.value if row.core_treatment else None) == core
        mapping = db_session.execute(
            text("SELECT " + ", ".join(merge._LOT_COLUMNS) + " FROM level_of_theory WHERE id = :i"),
            {"i": row.id},
        ).mappings().one()
        assert merge._identity_hash(mapping) == row.lot_hash == _level_of_theory_hash(ref)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relativistic_hamiltonian", "dkh2"),
        ("relativistic_hamiltonian", "none"),
        ("pno_truncation", "tight"),
        ("ri_approximation", "rijcosx"),
        ("ri_approximation", "none"),
        ("core_treatment", "window"),
    ],
)
def test_the_merge_script_rehashes_every_method_qualifier_to_the_stored_hash(db_session, merge, field, value):
    """Each typed qualifier (explicit ``none`` and ``window`` included) is in the script's identity hash.

    A script that left one out would recompute the unstated level's hash for a stated row and fold the two together.
    """
    ref = LevelOfTheoryRef(method="DLPNO-CCSD(T)", basis="cc-pVTZ", **{field: value})
    row = resolve_level_of_theory_ref(db_session, ref)
    db_session.flush()
    mapping = db_session.execute(
        text("SELECT " + ", ".join(merge._LOT_COLUMNS) + " FROM level_of_theory WHERE id = :i"), {"i": row.id}
    ).mappings().one()
    assert merge._identity_hash(mapping) == row.lot_hash == _level_of_theory_hash(ref)
    unstated = _level_of_theory_hash(LevelOfTheoryRef(method="DLPNO-CCSD(T)", basis="cc-pVTZ"))
    assert row.lot_hash != unstated


def test_the_merge_script_lists_every_method_qualifier_among_the_columns_it_regroups_by(merge):
    for column in ("core_treatment", "relativistic_hamiltonian", "pno_truncation", "ri_approximation"):
        assert column in merge._LOT_COLUMNS


def test_the_merge_script_lists_core_treatment_among_the_columns_it_regroups_by(merge):
    assert "core_treatment" in merge._LOT_COLUMNS


def test_the_frozen_migration_copy_agrees_for_a_null_core_treatment_and_is_blind_to_the_field():
    mig = _load("_mig_b9e4_for_p4", _VERSIONS / "b9e4c2a7d153_key_level_of_theory_composite_method_aliases.py")
    fields = {
        "method": "CCSD(T)",
        "basis": "cc-pCVTZ",
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": None,
    }
    row = SimpleNamespace(_mapping=fields)
    # The frozen revision copy hashes the pre-Stage-2 payload, so it agrees with the frozen helper, not the live hash.
    assert mig._lot_hash(row, aliased=True) == frozen_level_of_theory_hash(method="CCSD(T)", basis="cc-pCVTZ")
    # The frozen copy runs below the revision that adds the column, where every
    # row is NULL. It has no notion of the field, and that is safe only because
    # downgrade refuses while any level states one.
    stated = LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core")
    assert mig._lot_hash(row, aliased=True) != frozen_level_of_theory_hash(
        method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core"
    )
    assert stated.core_treatment is not None


def test_the_sql_name_filters_ignore_core_treatment(db_session):
    """``method=`` / ``basis=`` filters key the stored names; both cores of one method match."""
    frozen = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment="frozen_core")
    )
    full = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ", core_treatment="all_electron")
    )
    db_session.flush()
    found = set(db_session.scalars(select(LevelOfTheory.id).where(method_matches("ccsd(t)"))))
    assert {frozen.id, full.id} <= found
    assert frozen.id != full.id


def _stored(db_session, ref: LevelOfTheoryRef, *, spelling_basis: str, stale_hash: str | None = None):
    """A row that carries ``ref``'s core treatment under a different basis spelling."""
    row = LevelOfTheory(
        method=ref.method,
        basis=spelling_basis,
        core_treatment=ref.core_treatment,
        lot_hash=stale_hash or _level_of_theory_hash(ref),
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_the_merge_script_does_not_fold_a_frozen_core_level_into_an_all_electron_one(db_session, merge):
    """Regrouping is by the recomputed hash, so core treatment has to take part.

    Three rows share one method and one basis in three spellings. Two state
    ``frozen_core`` (the holder carries the real hash, the other a stale one);
    the third states ``all_electron`` and also carries a stale hash. Only the
    two frozen-core rows are the same level.
    """
    frozen = LevelOfTheoryRef(method="ccsd(t)-ptest", basis="cc-pcvtz", core_treatment="frozen_core")
    full = LevelOfTheoryRef(method="ccsd(t)-ptest", basis="cc-pcvtz", core_treatment="all_electron")
    holder = _stored(db_session, frozen, spelling_basis="cc-pcvtz")
    duplicate = _stored(db_session, frozen, spelling_basis="cc-pCVTZ", stale_hash="a" * 64)
    other_core = _stored(db_session, full, spelling_basis="CC-PCVTZ", stale_hash="b" * 64)

    plan = merge.build_plan(db_session)

    [group] = [g for g in plan.groups if g.holder and g.holder.row_id == holder.id]
    assert [d.row.row_id for d in group.duplicates] == [duplicate.id]
    # The all-electron row is in no group at all: its own recomputed hash is unique.
    assert not any(
        other_core.id in {d.row.row_id for d in g.duplicates} or (g.holder and g.holder.row_id == other_core.id)
        for g in plan.groups
    )
