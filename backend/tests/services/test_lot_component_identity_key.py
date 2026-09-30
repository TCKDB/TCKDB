"""Dispersion, solvent and solvent-model case is not identity (#602).

The spellings are what each producer writes. ARC lower-cases every level
component (``arc/level.py``, ``Level.lower``): ``d3bj``, ``water``, ``smd``.
Gaussian, ORCA and Molpro records carry the program's own case:
``EmpiricalDispersion=GD3BJ``, ``SCRF=(SMD,Solvent=Water)``, ORCA ``! D3BJ``,
``! CPCM(Water)``, Molpro ``dispersion,d3``. Before #602 each pair was two
rows of ``level_of_theory``.

Every case asserts the exact key. The negative table pins what stays apart:
synonyms need a curated table and are out of scope (``h2o`` is not ``water``,
``d3(bj)`` is not ``d3bj`` in the dispersion column).
"""

from __future__ import annotations

import pytest
from sqlalchemy import Text, literal, select
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.lot_component_names import component_identity_key
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.scientific_read.lot_identity_filters import component_key_sql

#: (program, spelling, exact key)
DISPERSION = [
    ("ARC", "d3bj", "d3bj"),
    ("ORCA", "D3BJ", "d3bj"),
    ("Gaussian", "GD3BJ", "gd3bj"),
    ("Gaussian ARC", "empiricaldispersion=gd3bj", "empiricaldispersion=gd3bj"),
    ("Gaussian route", "EmpiricalDispersion=GD3BJ", "empiricaldispersion=gd3bj"),
    ("Gaussian", "GD3", "gd3"),
    ("Molpro", "D3", "d3"),
    ("ORCA", "D4", "d4"),
]
SOLVENT = [
    ("ARC", "water", "water"),
    ("Gaussian", "Water", "water"),
    ("ORCA", "WATER", "water"),
    ("Gaussian", "Acetonitrile", "acetonitrile"),
    ("ARC", "acetonitrile", "acetonitrile"),
    ("Gaussian", "DiMethylSulfoxide", "dimethylsulfoxide"),
]
SOLVENT_MODEL = [
    ("ARC", "smd", "smd"),
    ("Gaussian", "SMD", "smd"),
    ("Gaussian", "PCM", "pcm"),
    ("ORCA", "CPCM", "cpcm"),
    ("ARC", "cpcm", "cpcm"),
]

_ALL = [*DISPERSION, *SOLVENT, *SOLVENT_MODEL]


@pytest.mark.parametrize(("program", "spelling", "key"), _ALL, ids=lambda v: str(v))
def test_every_real_spelling_has_the_exact_key(program, spelling, key):
    assert component_identity_key(spelling) == key


def test_the_spelling_tables_are_not_empty():
    assert len(_ALL) >= 19


@pytest.mark.parametrize("spelling", [s for _, s, _ in _ALL])
def test_the_key_is_idempotent(spelling):
    key = component_identity_key(spelling)
    assert component_identity_key(key) == key


def test_none_and_blank_have_no_key():
    assert component_identity_key(None) is None
    assert component_identity_key("") is None
    assert component_identity_key("  \t") is None
    assert component_identity_key("  Water\t") == "water"


#: Different components. Synonyms are an alias table's job, not a case rule's.
DIFFERENT = [
    ("dispersion", "d3", "d3bj", "zero vs BJ damping"),
    ("dispersion", "d3bj", "d3(bj)", "synonym, not a case rule"),
    ("dispersion", "gd3bj", "d3bj", "synonym, not a case rule"),
    ("solvent", "water", "h2o", "synonym, not a case rule"),
    ("solvent", "water", "heavywater", "different solvent"),
    ("solvent_model", "smd", "pcm", "different model"),
    ("solvent_model", "cpcm", "pcm", "different model"),
]


def _hash(**fields) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(method="b3lyp", basis="def2-tzvp", **fields))


@pytest.mark.parametrize(("program", "spelling", "key"), DISPERSION, ids=lambda v: str(v))
def test_dispersion_spellings_hash_alike(program, spelling, key):
    assert _hash(dispersion=spelling) == _hash(dispersion=key)


@pytest.mark.parametrize(("program", "spelling", "key"), SOLVENT, ids=lambda v: str(v))
def test_solvent_spellings_hash_alike(program, spelling, key):
    assert _hash(solvent=spelling, solvent_model="smd") == _hash(solvent=key, solvent_model="smd")


@pytest.mark.parametrize(("program", "spelling", "key"), SOLVENT_MODEL, ids=lambda v: str(v))
def test_solvent_model_spellings_hash_alike(program, spelling, key):
    assert _hash(solvent="water", solvent_model=spelling) == _hash(
        solvent="water", solvent_model=key
    )


@pytest.mark.parametrize(
    ("field", "a", "b"), [pytest.param(f, a, b, id=why) for f, a, b, why in DIFFERENT]
)
def test_different_components_hash_apart(field, a, b):
    assert _hash(**{field: a}) != _hash(**{field: b})


def test_an_absent_component_is_not_a_present_one():
    assert _hash() != _hash(dispersion="d3bj")
    assert _hash() != _hash(solvent="water")


def test_components_are_not_interchangeable():
    """The key is per field: ``water`` as a solvent is not ``water`` as a dispersion."""
    assert _hash(solvent="water") != _hash(dispersion="water")


def test_a_lower_case_component_hashes_as_it_always_did():
    """Rows already spelled like the key keep their hash, so their refs hold."""
    import hashlib
    import json

    payload = {
        "method": "b3lyp",
        "basis": "def2-tzvp",
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": "d3bj",
        "solvent": "water",
        "solvent_model": "smd",
        "keywords": None,
        "spin_treatment": "unknown",
    }
    pre_602 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert _hash(dispersion="d3bj", solvent="water", solvent_model="smd") == pre_602
    assert _hash(dispersion="D3BJ", solvent="Water", solvent_model="SMD") == pre_602


def test_keywords_stay_verbatim():
    assert _hash(keywords="Opt") != _hash(keywords="opt")


def test_the_upload_path_resolves_case_variants_to_one_row(db_session):
    """The #602 pairs: ``D3BJ`` / ``d3bj`` and ``Water`` / ``water``."""
    stored = resolve_level_of_theory_ref(
        db_session,
        LevelOfTheoryRef(
            method="B3LYP", basis="def2-tzvp", dispersion="GD3BJ", solvent="Water", solvent_model="SMD"
        ),
    )
    arc = resolve_level_of_theory_ref(
        db_session,
        LevelOfTheoryRef(
            method="b3lyp", basis="def2-tzvp", dispersion="gd3bj", solvent="water", solvent_model="smd"
        ),
    )
    assert arc.id == stored.id
    # The first uploader's spellings are kept and shown.
    assert (stored.dispersion, stored.solvent, stored.solvent_model) == ("GD3BJ", "Water", "SMD")


def test_the_upload_path_keeps_solvent_synonyms_apart(db_session):
    a = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="b3lyp", solvent="water"))
    b = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="b3lyp", solvent="h2o"))
    assert a.id != b.id


@pytest.mark.parametrize(
    "spelling", [s for _, s, _ in _ALL] + ["", "   ", " Water ", "\tD3BJ\n", "H2O"]
)
def test_the_sql_component_key_agrees_with_python(db_session, spelling):
    assert db_session.scalar(select(component_key_sql(literal(spelling)))) == (
        component_identity_key(spelling)
    )


def test_the_sql_component_key_of_null_is_null(db_session):
    assert db_session.scalar(select(component_key_sql(literal(None, type_=Text)))) is None
