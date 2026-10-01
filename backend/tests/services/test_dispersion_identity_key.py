"""Dispersion-column synonyms and a dispersion folded into the method (#630).

#627 left four spellings of one calculation as four levels of theory:
``b3lyp`` + ``d3bj``, + ``gd3bj``, + ``EmpiricalDispersion=GD3BJ``, and
``b3lyp-d3bj``. ARC's Gaussian adapter writes ``level.dispersion`` into the
route unchanged, so the second and third are what a Gaussian-run ARC upload
carries.

Every positive case asserts the exact key. The negative tables pin what must
stay apart: bare ``d3`` (ORCA BJ-damped, Psi4 zero-damped), a refit functional
that merely ends in a dispersion name, and a level that contradicts itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Text, literal, select
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.dispersion_names import (
    DISPERSION_ALIASES,
    FOLDED_STEMS,
    FOLDED_SUFFIXES,
    dispersion_identity_key,
    level_identity_keys,
    method_matches_level,
)
from app.chemistry.method_names import method_identity_key
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.scientific_read.lot_identity_filters import (
    dispersion_key_sql,
    level_keys_sql,
)

#: (producer, spelling in the dispersion column, exact key)
DISPERSION = [
    ("ARC", "d3bj", "d3bj"),
    ("ARC lower-case of Gaussian", "gd3bj", "d3bj"),
    ("ORCA", "D3BJ", "d3bj"),
    ("Psi4", "D3(BJ)", "d3bj"),
    ("Gaussian", "GD3BJ", "d3bj"),
    ("ARC Gaussian route", "empiricaldispersion=gd3bj", "d3bj"),
    ("Gaussian route", "EmpiricalDispersion=GD3BJ", "d3bj"),
    ("Gaussian route, spaced", "EmpiricalDispersion = GD3BJ", "d3bj"),
    ("Gaussian route, parenthesised", "EmpiricalDispersion(GD3BJ)", "d3bj"),
    ("Gaussian route, both", "EmpiricalDispersion=(GD3BJ)", "d3bj"),
    ("Gaussian route, padded", "  EMPIRICALDISPERSION = ( GD3BJ ) \n", "d3bj"),
    ("Gaussian", "GD3", "d3zero"),
    ("Gaussian route", "EmpiricalDispersion=GD3", "d3zero"),
    ("ORCA / Psi4", "D3ZERO", "d3zero"),
    ("Gaussian", "GD2", "d2"),
    ("Gaussian route", "EmpiricalDispersion=(GD2)", "d2"),
    ("ORCA", "D2", "d2"),
    ("blank", "  ", None),
    ("absent", None, None),
]

#: Left as written (bar case): neither alias nor a spelling the table knows.
UNRECOGNISED = [
    ("bare d3 is not BJ (ORCA) nor zero (Psi4)", "d3", "d3"),
    ("Gaussian PFD", "EmpiricalDispersion=PFD", "empiricaldispersion=pfd"),
    ("D4", "D4", "d4"),
    ("unbalanced parenthesis", "empiricaldispersion=(gd3bj", "empiricaldispersion=(gd3bj"),
    ("a suffix is not a column value", "b3lyp-d3bj", "b3lyp-d3bj"),
    ("wrapper around a canonical key", "empiricaldispersion=d3bj", "empiricaldispersion=d3bj"),
]


@pytest.mark.parametrize(("program", "spelling", "key"), DISPERSION, ids=str)
def test_every_real_spelling_has_the_exact_key(program, spelling, key):
    assert dispersion_identity_key(spelling) == key


@pytest.mark.parametrize(("why", "spelling", "key"), UNRECOGNISED, ids=lambda v: str(v)[:40])
def test_unrecognised_spellings_stay_as_written(why, spelling, key):
    assert dispersion_identity_key(spelling) == key


@pytest.mark.parametrize("spelling", [s for _, s, _ in [*DISPERSION, *UNRECOGNISED]])
def test_the_key_is_idempotent(spelling):
    key = dispersion_identity_key(spelling)
    assert dispersion_identity_key(key) == key


def test_the_tables_are_not_empty():
    assert len(DISPERSION) >= 15 and len(UNRECOGNISED) >= 5


def test_the_alias_table_is_cited_program_independent_and_leaves_bare_d3_out():
    assert len(DISPERSION_ALIASES) >= 4
    for entry in DISPERSION_ALIASES:
        assert entry.programs is None, "the hash cannot see a program"
        assert len(entry.citations) >= 2, entry.alias
        assert entry.alias == entry.alias.lower()
        # A canonical spelling is never itself an alias: rules stay order
        # independent and the key idempotent.
        assert entry.canonical not in {a.alias for a in DISPERSION_ALIASES}
    assert "d3" not in {a.alias for a in DISPERSION_ALIASES}
    assert "d3" not in {a.canonical for a in DISPERSION_ALIASES}


# ---------------------------------------------------------------------------
# Folded dispersion
# ---------------------------------------------------------------------------

#: (method, dispersion column, exact (method key, dispersion key))
LEVELS = [
    ("b3lyp", "d3bj", ("b3lyp", "d3bj")),
    ("b3lyp", "gd3bj", ("b3lyp", "d3bj")),
    ("b3lyp", "EmpiricalDispersion=GD3BJ", ("b3lyp", "d3bj")),
    ("b3lyp-d3bj", None, ("b3lyp", "d3bj")),
    ("B3LYP-D3(BJ)", None, ("b3lyp", "d3bj")),
    ("b3lyp-gd3bj", None, ("b3lyp", "d3bj")),
    ("b3lyp-d3bj", "d3bj", ("b3lyp", "d3bj")),
    ("b3lyp-d3bj", "GD3BJ", ("b3lyp", "d3bj")),
    ("b3lyp-d3zero", None, ("b3lyp", "d3zero")),
    ("b3lyp", "gd3", ("b3lyp", "d3zero")),
    ("pbe-d2", None, ("pbe", "d2")),
    ("M06-2X-D3ZERO", None, ("m062x", "d3zero")),
    ("m062x-d3bj", "", ("m062x", "d3bj")),
    ("cam-b3lyp-d3bj", None, ("cam-b3lyp", "d3bj")),
    ("b2plyp-d3bj", None, ("b2plyp", "d3bj")),
    ("hf-d3bj", None, ("hf", "d3bj")),
    # Already in column form: unchanged.
    ("b3lyp", None, ("b3lyp", None)),
    ("wb97xd", "d3bj", ("wb97xd", "d3bj")),
]

#: Levels that must NOT be split, and the key they get instead.
NOT_SPLIT = [
    ("wb97x-d3bj", None, ("wb97x-d3bj", None), "refit functional, own parametrisation"),
    ("wB97X-D3(BJ)", None, ("wb97x-d3bj", None), "refit functional, Psi4 spelling"),
    ("wb97m-d3bj", None, ("wb97m-d3bj", None), "refit functional"),
    ("b97-d3bj", None, ("b97-d3bj", None), "B97-D is a refit"),
    ("dsd-blyp-d3bj", None, ("dsd-blyp-d3bj", None), "double hybrid with refit"),
    ("b3lyp-d3", None, ("b3lyp-d3", None), "bare d3 is ambiguous across programs"),
    ("b3lyp-d4", None, ("b3lyp-d4", None), "D4 is not in the table"),
    ("b3lyp-d3bj", "d3zero", ("b3lyp-d3bj", "d3zero"), "the level contradicts itself"),
    ("b3lyp-d3bj", "d4", ("b3lyp-d3bj", "d4"), "the level contradicts itself"),
    ("-d3bj", None, ("-d3bj", None), "a suffix needs a stem"),
    ("ub3lyp-d3bj", None, ("ub3lyp-d3bj", None), "ub3lyp is not an alias of b3lyp here"),
]


@pytest.mark.parametrize(("method", "dispersion", "keys"), LEVELS, ids=str)
def test_level_keys(method, dispersion, keys):
    assert level_identity_keys(method, dispersion) == keys


@pytest.mark.parametrize(
    ("method", "dispersion", "keys", "why"), NOT_SPLIT, ids=lambda v: str(v)[:40]
)
def test_levels_that_stay_folded_or_apart(method, dispersion, keys, why):
    assert level_identity_keys(method, dispersion) == keys, why


@pytest.mark.parametrize(
    "method,dispersion",
    [(m, d) for m, d, _ in LEVELS] + [(m, d) for m, d, _, _ in NOT_SPLIT],
)
def test_level_keys_are_idempotent(method, dispersion):
    first = level_identity_keys(method, dispersion)
    assert level_identity_keys(*first) == first


def test_every_stem_and_suffix_is_exercised_and_keyed():
    """Each stem splits with each suffix, to a stem the method key leaves alone."""
    for stem in FOLDED_STEMS:
        for suffix in FOLDED_SUFFIXES:
            got = level_identity_keys(f"{stem}-{suffix}", None)
            assert got == (method_identity_key(stem), suffix), (stem, suffix)
    assert {"wb97x", "wb97m", "b97"}.isdisjoint(FOLDED_STEMS)
    assert "d3" not in FOLDED_SUFFIXES


# ---------------------------------------------------------------------------
# The hash
# ---------------------------------------------------------------------------


def _hash(method, dispersion=None, **fields) -> str:
    return _level_of_theory_hash(
        LevelOfTheoryRef(method=method, basis="def2-tzvp", dispersion=dispersion, **fields)
    )


ONE_LEVEL = [
    ("b3lyp", "d3bj"),
    ("b3lyp", "gd3bj"),
    ("b3lyp", "EmpiricalDispersion=GD3BJ"),
    ("b3lyp", "empiricaldispersion=gd3bj"),
    ("b3lyp", "D3(BJ)"),
    ("b3lyp-d3bj", None),
    ("b3lyp-d3(bj)", None),
    ("B3LYP-GD3BJ", None),
    ("b3lyp-d3bj", "GD3BJ"),
]


def test_the_four_spellings_of_issue_630_are_one_hash():
    hashes = {_hash(m, d) for m, d in ONE_LEVEL}
    assert len(hashes) == 1


def test_a_column_form_row_keeps_the_hash_it_always_had():
    """``b3lyp`` + ``d3bj`` is the key form, so deployed rows in it keep their refs."""
    import hashlib
    import json

    payload = {
        "method": "b3lyp",
        "basis": "def2-tzvp",
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": "d3bj",
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unknown",
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert _hash("b3lyp", "d3bj") == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(("b3lyp", None), ("b3lyp", "d3bj"), id="bare functional vs dispersion"),
        pytest.param(("b3lyp", "d3"), ("b3lyp", "d3bj"), id="bare d3 vs BJ"),
        pytest.param(("b3lyp", "d3"), ("b3lyp", "d3zero"), id="bare d3 vs zero damping"),
        pytest.param(("b3lyp", "d3bj"), ("b3lyp", "d3zero"), id="BJ vs zero damping"),
        pytest.param(("b3lyp-d3", None), ("b3lyp-d3bj", None), id="folded d3 vs folded d3bj"),
        pytest.param(("wb97x-d3bj", None), ("wb97x", "d3bj"), id="refit functional vs add-on"),
        pytest.param(("wb97x-d3bj", None), ("wb97xd", None), id="wb97x-d3bj vs wb97xd"),
        pytest.param(("b3lyp-d3bj", "d3zero"), ("b3lyp", "d3bj"), id="self-contradicting level"),
        pytest.param(("b3lyp-d3bj", "d3zero"), ("b3lyp", "d3zero"), id="contradiction vs zero"),
        pytest.param(("b3lyp", "gd3"), ("b3lyp", "d3"), id="gd3 is zero damping, not bare d3"),
    ],
)
def test_levels_that_must_stay_apart_hash_apart(a, b):
    assert _hash(*a) != _hash(*b)


def test_the_dispersion_is_not_joined_across_other_hashed_fields():
    """The join is only in the key: a different basis or solvent is another level."""
    assert _hash("b3lyp-d3bj") != _hash("b3lyp", "d3bj", solvent="water", solvent_model="smd")
    assert _hash("b3lyp-d3bj", keywords="Opt") != _hash("b3lyp", "d3bj", keywords="opt")


def test_the_upload_path_resolves_every_spelling_to_one_row(db_session):
    ids = {
        resolve_level_of_theory_ref(
            db_session, LevelOfTheoryRef(method=m, basis="def2-tzvp", dispersion=d)
        ).id
        for m, d in ONE_LEVEL
    }
    assert len(ids) == 1
    refit = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="wb97x-d3bj", basis="def2-tzvp")
    )
    assert refit.id not in ids


def test_the_row_keeps_the_verbatim_spelling(db_session):
    lot = resolve_level_of_theory_ref(
        db_session,
        LevelOfTheoryRef(
            method="B3LYP", basis="def2-tzvp", dispersion="EmpiricalDispersion=GD3BJ"
        ),
    )
    again = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="b3lyp-d3(bj)", basis="def2-tzvp")
    )
    assert again.id == lot.id
    assert (lot.method, lot.dispersion) == ("B3LYP", "EmpiricalDispersion=GD3BJ")


# ---------------------------------------------------------------------------
# The SQL twin agrees with the Python key
# ---------------------------------------------------------------------------

_ALL_PAIRS = [(m, d) for m, d, _ in LEVELS] + [(m, d) for m, d, _, _ in NOT_SPLIT] + [
    ("b3lyp", s) for _, s, _ in UNRECOGNISED
]


@pytest.mark.parametrize(("_program", "spelling", "_key"), DISPERSION, ids=str)
def test_the_sql_dispersion_key_agrees_with_python(db_session, _program, spelling, _key):
    got = db_session.scalar(select(dispersion_key_sql(literal(spelling, type_=Text))))
    assert got == dispersion_identity_key(spelling)


@pytest.mark.parametrize(("method", "dispersion"), _ALL_PAIRS, ids=str)
def test_the_sql_level_keys_agree_with_python(db_session, method, dispersion):
    method_sql, dispersion_sql = level_keys_sql(
        literal(method, type_=Text), literal(dispersion, type_=Text)
    )
    row = db_session.execute(select(method_sql, dispersion_sql)).one()
    assert tuple(row) == level_identity_keys(method, dispersion)


def test_the_sql_and_python_in_memory_matchers_agree(db_session):
    from app.db.models.level_of_theory import LevelOfTheory
    from app.services.scientific_read.lot_identity_filters import method_matches
    from tests.services.scientific_read._factories import make_lot

    lots = [
        make_lot(db_session, method="b3lyp-d3bj", basis="def2tzvp"),
        make_lot(db_session, method="b3lyp", basis="def2tzvp", dispersion="gd3bj"),
        make_lot(db_session, method="b3lyp", basis="def2tzvp"),
        make_lot(db_session, method="wb97x-d3bj", basis="def2tzvp"),
    ]
    for ask in ("b3lyp", "B3LYP-D3(BJ)", "b3lyp-gd3bj", "wb97x", "wb97x-d3bj", "b3lyp-d3zero"):
        in_sql = set(db_session.scalars(select(LevelOfTheory.id).where(method_matches(ask))))
        in_python = {
            lot.id for lot in lots if method_matches_level(lot.method, lot.dispersion, ask)
        }
        assert in_sql == in_python, ask
