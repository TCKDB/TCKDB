"""Basis-set spelling and level-of-theory identity (#574).

The spellings are what each program actually writes, from the group
knowledge base's "Basis set spelling" card (``ess/capabilities.md``) and each
program's input conventions: Gaussian ``Def2TZVP``; ORCA, Q-Chem and Molpro
``def2-TZVP`` / ``cc-pVTZ``; Psi4 and PySCF ``def2-tzvp`` / ``cc-pvtz``;
PySCF's internal basis key ``ccpvtz``; ARC writing Gaussian's spelling in
lower case, ``def2tzvp``.

Every same-basis case asserts the exact key, not only that two spellings
agree, so a rule that merges more than it should is caught by the negative
table and a rule that merges less is caught here.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.services.calculation_resolution import _level_of_theory_hash

#: (basis set, [(program, spelling), ...], exact identity key)
SAME_BASIS: list[tuple[str, list[tuple[str, str]], str]] = [
    (
        "def2-TZVP",
        [
            ("Gaussian", "Def2TZVP"),
            ("ARC for Gaussian", "def2tzvp"),
            ("ORCA", "def2-TZVP"),
            ("Q-Chem", "def2-TZVP"),
            ("Molpro", "def2-TZVP"),
            ("Psi4", "def2-tzvp"),
            ("PySCF", "def2-tzvp"),
        ],
        "def2-tzvp",
    ),
    (
        "def2-SVP",
        [
            ("Gaussian", "Def2SVP"),
            ("ARC for Gaussian", "def2svp"),
            ("ORCA", "def2-SVP"),
            ("Psi4", "def2-svp"),
        ],
        "def2-svp",
    ),
    (
        "def2-QZVPPD",
        [("Gaussian", "Def2QZVPPD"), ("ORCA", "def2-QZVPPD"), ("Psi4", "def2-qzvppd")],
        "def2-qzvppd",
    ),
    (
        "cc-pVTZ",
        [
            ("Gaussian", "cc-pVTZ"),
            ("ORCA", "cc-pVTZ"),
            ("Q-Chem", "cc-pVTZ"),
            ("Molpro", "cc-pVTZ"),
            ("Psi4", "cc-pvtz"),
            ("PySCF", "cc-pvtz"),
            ("PySCF basis key", "ccpvtz"),
        ],
        "cc-pvtz",
    ),
    (
        "cc-pwCVTZ",
        [("Molpro", "cc-pwCVTZ"), ("Psi4", "cc-pwcvtz"), ("PySCF basis key", "ccpwcvtz")],
        "cc-pwcvtz",
    ),
    (
        "aug-cc-pVTZ",
        [
            ("Gaussian", "aug-cc-pVTZ"),
            ("ORCA", "aug-cc-pVTZ"),
            ("Molpro", "aug-cc-pVTZ"),
            ("Psi4", "aug-cc-pvtz"),
        ],
        "aug-cc-pvtz",
    ),
    (
        "cc-pVTZ-F12",
        [("Molpro", "cc-pVTZ-F12"), ("ORCA", "cc-pVTZ-F12"), ("Psi4", "cc-pvtz-f12")],
        "cc-pvtz-f12",
    ),
    (
        "def2-TZVP/C",
        [("ORCA", "def2-TZVP/C"), ("ORCA, lower case", "def2-tzvp/c")],
        "def2-tzvp/c",
    ),
    (
        "6-31G(d)",
        [("Gaussian", "6-31G(d)"), ("Psi4", "6-31g(d)"), ("Q-Chem", "6-31G(d)")],
        "6-31g(d)",
    ),
    (
        "6-311+G(2d,p)",
        [("Gaussian", "6-311+G(2d,p)"), ("Psi4", "6-311+g(2d,p)")],
        "6-311+g(2d,p)",
    ),
]

#: Pairs that are different basis sets. Each differs only by a character the
#: key must keep.
DIFFERENT_BASIS: list[tuple[str, str, str]] = [
    ("6-31G*", "6-31G**", "polarisation on hydrogen"),
    ("6-31+G", "6-31G", "diffuse functions"),
    ("6-31++G", "6-31+G", "diffuse on hydrogen"),
    ("6-31G(d,p)", "6-31G(d)", "p polarisation on hydrogen"),
    ("aug-cc-pVTZ", "cc-pVTZ", "aug- prefix"),
    ("jun-cc-pVTZ", "aug-cc-pVTZ", "calendar prefix"),
    ("may-cc-pVTZ", "jun-cc-pVTZ", "calendar prefix"),
    ("d-aug-cc-pVTZ", "aug-cc-pVTZ", "doubly augmented"),
    ("cc-pVTZ-F12", "cc-pVTZ", "-F12 suffix"),
    ("cc-pVTZ-PP", "cc-pVTZ", "-PP pseudopotential suffix"),
    ("cc-pVTZ-DK", "cc-pVTZ", "-DK relativistic suffix"),
    ("cc-pCVTZ", "cc-pVTZ", "core-valence"),
    ("cc-pwCVTZ", "cc-pCVTZ", "weighted core-valence"),
    ("def2-TZVP/C", "def2-TZVP", "/C auxiliary"),
    ("def2/J", "def2/JK", "/J vs /JK auxiliary"),
    ("def2-TZVP(-f)", "def2-TZVP", "(-f) removes f functions"),
    ("ma-def2-TZVP", "def2-TZVP", "minimally augmented"),
    ("def2-TZVPP", "def2-TZVP", "extra polarisation"),
    ("def2-SVPD", "def2-SVP", "diffuse"),
]

#: Same basis, deliberately not equated: equating them needs an alias
#: table, not a spelling rule. A false split is recoverable; a false merge
#: is not.
NOT_EQUATED: list[tuple[str, str, str]] = [
    ("augccpvtz", "aug-cc-pvtz", "PySCF key: the hyphen after aug- is kept"),
    ("vtz", "cc-pVTZ", "Molpro shorthand"),
    ("avtz", "aug-cc-pVTZ", "Molpro shorthand"),
    ("6-31G*", "6-31G(d)", "star notation for d on heavy atoms"),
    ("6-31G**", "6-31G(d,p)", "star notation for d,p"),
]


def _same_basis_cases():
    for basis, spellings, key in SAME_BASIS:
        for program, spelling in spellings:
            yield pytest.param(spelling, key, id=f"{basis}-{program}")


@pytest.mark.parametrize(("spelling", "key"), list(_same_basis_cases()))
def test_every_real_spelling_has_the_exact_key(spelling, key):
    assert basis_identity_key(spelling) == key


def test_the_same_basis_table_is_not_empty():
    # Guards against a parametrize list that silently yields nothing.
    assert sum(len(spellings) for _, spellings, _ in SAME_BASIS) >= 30


@pytest.mark.parametrize(
    ("a", "b"), [pytest.param(a, b, id=why) for a, b, why in DIFFERENT_BASIS]
)
def test_different_basis_sets_keep_different_keys(a, b):
    assert basis_identity_key(a) != basis_identity_key(b)


@pytest.mark.parametrize(
    ("a", "b"), [pytest.param(a, b, id=why) for a, b, why in NOT_EQUATED]
)
def test_program_shorthands_are_not_equated(a, b):
    assert basis_identity_key(a) != basis_identity_key(b)


def test_the_key_keeps_every_distinguishing_character_verbatim():
    assert basis_identity_key("6-31G**") == "6-31g**"
    assert basis_identity_key("6-31+G(d,p)") == "6-31+g(d,p)"
    assert basis_identity_key("def2-TZVP(-f)") == "def2-tzvp(-f)"
    assert basis_identity_key("def2/JK") == "def2/jk"
    assert basis_identity_key("ma-def2TZVP") == "ma-def2-tzvp"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_basis_has_no_key(value):
    assert basis_identity_key(value) is None


def _all_spellings() -> list[str]:
    spellings = [s for _, pairs, _ in SAME_BASIS for _, s in pairs]
    spellings += [s for a, b, _ in DIFFERENT_BASIS + NOT_EQUATED for s in (a, b)]
    return spellings


@pytest.mark.parametrize("spelling", _all_spellings())
def test_the_key_is_idempotent(spelling):
    # The migration's collision-freedom argument depends on this.
    key = basis_identity_key(spelling)
    assert basis_identity_key(key) == key


# ---------------------------------------------------------------------------
# lot_hash
# ---------------------------------------------------------------------------


def _hash(**fields) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(method="b3lyp", **fields))


def test_psi4_and_gaussian_water_are_one_level_of_theory():
    """The #572 pair: Psi4 ``b3lyp/def2-tzvp``, Gaussian record ``b3lyp/def2tzvp``."""
    assert _hash(basis="def2-tzvp") == _hash(basis="def2tzvp")


@pytest.mark.parametrize("field", ["basis", "aux_basis", "cabs_basis"])
def test_every_basis_field_is_keyed(field):
    assert _hash(**{field: "cc-pVTZ-F12"}) == _hash(**{field: "cc-pvtz-f12"})
    assert _hash(**{field: "6-31G*"}) != _hash(**{field: "6-31G**"})


def test_basis_fields_stay_distinct_from_each_other():
    assert _hash(basis="def2-tzvp") != _hash(aux_basis="def2-tzvp")


def test_a_canonical_spelling_hashes_as_it_always_did():
    """Rows already spelled like the key keep their hash, so their refs hold."""
    import hashlib
    import json

    payload = {
        "method": "b3lyp",
        "basis": "def2-tzvp",
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unknown",
    }
    pre_574 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert _hash(basis="def2-tzvp") == pre_574


def test_method_is_still_hashed_verbatim():
    # #574 is about basis names only. Method case is a separate question.
    assert _hash(basis="def2-tzvp") != _level_of_theory_hash(
        LevelOfTheoryRef(method="B3LYP", basis="def2-tzvp")
    )


# ---------------------------------------------------------------------------
# The migration's frozen copy
# ---------------------------------------------------------------------------

_MIGRATION = (
    pathlib.Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "38b06819f099_key_level_of_theory_basis_by_identity.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("_mig_38b06819f099", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("spelling", _all_spellings())
def test_migration_rules_match_the_application(migration, spelling):
    assert migration._basis_identity_key(spelling) == basis_identity_key(spelling)


@pytest.mark.parametrize("spelling", _all_spellings())
def test_migration_hash_matches_the_application(migration, spelling):
    fields = {
        "method": "b3lyp",
        "basis": spelling,
        "aux_basis": "def2/J",
        "cabs_basis": None,
        "dispersion": "d3bj",
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unrestricted",
    }
    row = SimpleNamespace(_mapping=fields)
    assert migration._lot_hash(row, keyed=True) == _level_of_theory_hash(
        LevelOfTheoryRef(**fields)
    )


def test_the_merge_record_adds_no_column_to_level_of_theory():
    """Merges live in ``level_of_theory_merge``, not on the row (#574 review).

    Consistency-check inputs (``consistency.core.snapshot``) and
    reproducibility context hashes (``reproducibility_rubric._mapped_columns``)
    snapshot every column of a level of theory. A new column would change
    both for every row, re-keyed or not.
    """
    from app.db.models.level_of_theory import LevelOfTheory

    assert {c.key for c in LevelOfTheory.__table__.columns} == {
        "id", "method", "basis", "aux_basis", "cabs_basis", "dispersion",
        "solvent", "solvent_model", "keywords", "spin_treatment", "lot_hash",
        "created_at", "public_ref",
    }
