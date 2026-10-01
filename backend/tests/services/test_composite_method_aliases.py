"""Named composite methods: aliases, SQL parity and the upload path (ADR 0021).

Reproduction on ``main`` (f22d3a80): ``cbsqb3`` and ``CBS-QB3`` hash differently
and so made two ``level_of_theory`` rows; ``g4(mp2)`` and ``g4mp2`` likewise.
These tests fail there and pass with the curated aliases of
``app/chemistry/method_names.py``.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import literal, select
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.method_names import NAME_ALIASES, method_identity_key
from app.db.models.level_of_theory import LevelOfTheory
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.scientific_read.lot_identity_filters import method_key_sql, method_matches

#: Spellings the plan's alias list and its adversaries name. The SQL twin must
#: key every one the way Python does, including padding and the unaliased
#: lookalikes (``W1-BD``, the correction-table names).
_COMPOSITE_EDGES = [
    "CBS-QB3",
    "cbsqb3",
    "CBSQB3 ",
    "  cbsqb3\t",
    "G4(MP2)",
    "g4mp2",
    "g4(mp2)",
    " G4(MP2)\n",
    "G3(MP2)",
    "G3(MP2)B3",
    "g3(mp2)b3",
    "w1bd",
    "W1-BD",
    "W1BD",
    "W1U",
    "ROCBSQB3",
    "rocbs-qb3",
    "cbs4m",
    "CBS-4M",
    "cbsapno",
    "cbs-qb3-paraskevas",
    "cbsqb32023",
    "cbs-qb3-2023",
    "g4(mp2)-d3(bj)",
    "cbsqb3-gd3bj",
    "(mp2)",
    "g4(mp2",
]

_COMPOSITE_ALIASES = {
    "cbsqb3": "cbs-qb3",
    "rocbsqb3": "rocbs-qb3",
    "cbs4m": "cbs-4m",
    "cbsapno": "cbs-apno",
    "g4(mp2)": "g4mp2",
    "g3(mp2)": "g3mp2",
    "g3(mp2)b3": "g3mp2b3",
}


def _hash(method: str, **fields) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(method=method, **fields))


@pytest.mark.parametrize(("alias", "canonical"), sorted(_COMPOSITE_ALIASES.items()))
def test_each_composite_alias_keys_to_the_gaussian_arc_spelling(alias, canonical):
    assert method_identity_key(alias) == canonical
    assert method_identity_key(canonical) == canonical  # idempotent
    assert method_identity_key(alias.upper()) == canonical
    assert method_identity_key(f"  {alias}\t") == canonical


def test_the_composite_aliases_are_exactly_these_in_the_table():
    table = {a.alias: a.canonical for a in NAME_ALIASES}
    assert {k: v for k, v in table.items() if k in _COMPOSITE_ALIASES} == _COMPOSITE_ALIASES
    assert set(table) - set(_COMPOSITE_ALIASES) == {"wb97x-d", "m06-2x"}


@pytest.mark.parametrize("spelling", [*_COMPOSITE_EDGES, *_COMPOSITE_ALIASES])
def test_composite_key_is_idempotent_and_sql_agrees(db_session, spelling):
    key = method_identity_key(spelling)
    assert method_identity_key(key) == key
    assert db_session.scalar(select(method_key_sql(literal(spelling)))) == key


def test_w1_hyphen_bd_is_not_merged_with_w1bd():
    """``W1-BD`` is unsafe to alias: no source spells it, and a wrong merge is permanent.

    The Gaussian keyword is ``W1BD`` (Gaussian 09 manual, W1 methods; ARC
    ``data/ess_methods.yml``). Nothing cited writes a hyphen. A split is
    recoverable by the merge script later; a false join is not.
    """
    assert method_identity_key("W1-BD") == "w1-bd"
    assert method_identity_key("W1BD") == "w1bd"
    assert _hash("W1-BD") != _hash("W1BD")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("CBS-QB3", "cbsqb3"),
        ("CBS-QB3", "CBSQB3 "),
        ("G4(MP2)", "g4mp2"),
        ("G3(MP2)", "g3mp2"),
        ("G3(MP2)B3", "g3mp2b3"),
        ("ROCBS-QB3", "rocbsqb3"),
        ("CBS-4M", "cbs4m"),
        ("CBS-APNO", "cbsapno"),
    ],
)
def test_composite_aliases_hash_alike(a, b):
    assert _hash(a) == _hash(b)
    assert _hash(a, basis="cbsb7") == _hash(b, basis="CBSB7")


def test_the_alias_hash_is_the_hash_the_canonical_spelling_always_had():
    """Rows already stored as ``cbs-qb3`` / ``g4mp2`` keep their hash and their refs."""
    for canonical in ("cbs-qb3", "g4mp2", "g3mp2b3", "w1bd"):
        payload = {
            "method": canonical,
            "basis": None,
            "aux_basis": None,
            "cabs_basis": None,
            "dispersion": None,
            "solvent": None,
            "solvent_model": None,
            "keywords": None,
            "spin_treatment": "unknown",
        }
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert _hash(canonical) == expected


def test_the_upload_path_joins_cbsqb3_and_cbs_qb3_into_one_row(db_session):
    """The reproduction on main: ``cbsqb3`` and ``CBS-QB3`` made two rows."""
    gaussian = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    arkane = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="cbsqb3"))
    arc = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="cbs-qb3 "))
    assert gaussian.id == arkane.id == arc.id
    assert gaussian.method == "CBS-QB3"  # the first uploader's spelling is kept


def test_the_upload_path_joins_the_parenthesised_g_spellings(db_session):
    for paren, plain in (("G4(MP2)", "g4mp2"), ("g3(mp2)", "G3MP2"), ("G3(MP2)B3", "g3mp2b3")):
        a = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=paren))
        b = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=plain))
        assert a.id == b.id, (paren, plain)


def test_the_upload_path_keeps_the_lookalike_recipes_apart(db_session):
    methods = [
        "CBS-QB3", "ROCBS-QB3", "W1", "W1U", "W1BD", "W1-BD", "W1RO", "W2",
        "G4", "G4MP2", "G3", "G3MP2", "G3B3", "G3MP2B3",
        "cbs-qb3-paraskevas", "cbsqb32023",
    ]
    ids = {
        m: resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=m)).id for m in methods
    }
    assert len(set(ids.values())) == len(methods), ids


def test_the_search_filter_finds_a_row_stored_under_any_alias_spelling(db_session):
    stored = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="G4(MP2)"))
    other = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="G4"))
    for request in ("g4mp2", "G4MP2", "g4(mp2)", " G4(MP2) "):
        found = db_session.scalars(select(LevelOfTheory.id).where(method_matches(request))).all()
        assert stored.id in found, request
        assert other.id not in found, request
