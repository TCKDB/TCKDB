"""Method-name case, curated aliases and level-of-theory identity (#585, #618).

The spellings are what each producer writes. ARC lower-cases every method
(``arc/level.py``, ``Level.lower``) and every fixture under
``tests/fixtures`` carries ARC's lower-case spelling; Gaussian, ORCA and
Molpro inputs are written in mixed or upper case.

Every same-method case asserts the exact key, not only that two spellings
agree, and the negative table pins what must stay two levels of theory.
Punctuation is equated only through the curated alias table
(``method_names.NAME_ALIASES`` / ``SUFFIX_ALIASES``, #618); the tests below
also hold that table to its rules (every entry cited, none program-scoped
while the hash cannot see a program, each canonical key stable).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
from types import SimpleNamespace

import pytest
from sqlalchemy import Text, literal, select
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import level_identity_keys
from app.chemistry.method_names import (
    NAME_ALIASES,
    SUFFIX_ALIASES,
    method_identity_key,
)
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_level_of_theory_ref,
)
from app.services.scientific_read.lot_identity_filters import (
    basis_key_sql,
    method_key_sql,
)
from tests.services.test_basis_identity_key import _all_spellings

#: (method, [(program, spelling), ...], exact identity key)
SAME_METHOD: list[tuple[str, list[tuple[str, str]], str]] = [
    (
        "CCSD(T)-F12",
        [
            ("ARC", "ccsd(t)-f12"),
            ("ORCA", "CCSD(T)-F12"),
            ("Molpro", "CCSD(T)-F12"),
            ("mixed", "Ccsd(T)-F12"),
        ],
        "ccsd(t)-f12",
    ),
    (
        "DLPNO-CCSD(T)-F12",
        [("ARC", "dlpno-ccsd(t)-f12"), ("ORCA", "DLPNO-CCSD(T)-F12")],
        "dlpno-ccsd(t)-f12",
    ),
    (
        "CCSD(T)-F12a",
        [("ARC", "ccsd(t)-f12a"), ("Molpro", "CCSD(T)-F12A"), ("Molpro doc", "CCSD(T)-F12a")],
        "ccsd(t)-f12a",
    ),
    ("B3LYP", [("ARC", "b3lyp"), ("Gaussian", "B3LYP"), ("ORCA", "B3LYP")], "b3lyp"),
    (
        "wB97XD",
        [
            ("ARC", "wb97xd"),
            ("Gaussian", "wB97XD"),
            ("Gaussian upper", "WB97XD"),
            ("Q-Chem", "wB97X-D"),
            ("Psi4", "wb97x-d"),
            ("second ARC corpus", "wb97x-d"),
        ],
        "wb97xd",
    ),
    ("wB97X-D3", [("ARC", "wb97x-d3"), ("ORCA", "wB97X-D3")], "wb97x-d3"),
    ("HF", [("ARC", "hf"), ("Gaussian", "HF"), ("ORCA", "HF")], "hf"),
    (
        "M06-2X",
        [
            ("ARC", "m06-2x"),
            ("Q-Chem", "M06-2X"),
            ("Gaussian", "M062X"),
            ("ORCA", "M062X"),
            ("ARC unhyphenated", "m062x"),
        ],
        "m062x",
    ),
    (
        "B3LYP-D3(BJ)",
        [
            ("Psi4", "b3lyp-d3bj"),
            ("Psi4 alias", "B3LYP-D3(BJ)"),
            ("Gaussian-style", "b3lyp-gd3bj"),
            ("Gaussian-style upper", "B3LYP-GD3BJ"),
            ("ORCA-style", "B3LYP-D3BJ"),
        ],
        "b3lyp-d3bj",
    ),
    ("wB97X-D3BJ", [("ORCA", "wB97X-D3BJ"), ("ARC", "wb97x-d3bj")], "wb97x-d3bj"),
    ("wB97X-D3(BJ)", [("Psi4 alias", "wB97X-D3(BJ)")], "wb97x-d3bj"),
    # Named composite methods (ADR 0021). The key is the Gaussian / ARC spelling.
    (
        "CBS-QB3",
        [
            ("Gaussian", "CBS-QB3"),
            ("ARC", "cbs-qb3"),
            ("Arkane", "cbsqb3"),
            ("Arkane upper", "CBSQB3"),
            ("padded", "CBSQB3 "),
        ],
        "cbs-qb3",
    ),
    (
        "ROCBS-QB3",
        [("Gaussian", "ROCBS-QB3"), ("ARC", "rocbs-qb3"), ("Arkane", "rocbsqb3")],
        "rocbs-qb3",
    ),
    ("CBS-4M", [("Gaussian", "CBS-4M"), ("ARC", "cbs-4m"), ("Arkane", "cbs4m")], "cbs-4m"),
    (
        "CBS-APNO",
        [("Gaussian", "CBS-APNO"), ("ARC", "cbs-apno"), ("Arkane", "cbsapno")],
        "cbs-apno",
    ),
    (
        "G4(MP2)",
        [("Gaussian", "G4MP2"), ("ARC", "g4mp2"), ("paper", "G4(MP2)"), ("paper lower", "g4(mp2)")],
        "g4mp2",
    ),
    ("G3(MP2)", [("Gaussian", "G3MP2"), ("ARC", "g3mp2"), ("paper", "G3(MP2)")], "g3mp2"),
    (
        "G3(MP2)B3",
        [("Gaussian", "G3MP2B3"), ("ARC", "g3mp2b3"), ("paren", "G3(MP2)B3")],
        "g3mp2b3",
    ),
    ("G4", [("Gaussian", "G4"), ("ARC", "g4")], "g4"),
    ("G3", [("Gaussian", "G3"), ("ARC", "g3")], "g3"),
    ("G3B3", [("Gaussian", "G3B3"), ("ARC", "g3b3")], "g3b3"),
    ("W1U", [("Gaussian", "W1U"), ("ARC", "w1u")], "w1u"),
    ("W1BD", [("Gaussian", "W1BD"), ("ARC", "w1bd")], "w1bd"),
    ("W1RO", [("Gaussian", "W1RO"), ("ARC", "w1ro")], "w1ro"),
]

#: Different methods. Each pair differs by a character the key must keep, or
#: is the same functional under two program-specific spellings that only an
#: alias table can equate.
DIFFERENT_METHOD: list[tuple[str, str, str]] = [
    ("wB97XD", "wB97X-D3", "Gaussian wB97XD is not ORCA wB97X-D3 (different dispersion)"),
    ("wB97X-D", "wB97X-D3", "Chai 2008 vs the D3 zero-damping refit"),
    ("wB97X-D3", "wB97X-D3BJ", "zero damping vs Becke-Johnson damping"),
    ("wB97X-D3", "wB97X-D4", "D3 vs D4"),
    ("wB97XD3", "wB97XD", "unhyphenated D3 is not an entry; only wb97x-d is"),
    ("wb97xd", "wb97x-v", "different functional"),
    ("B3LYP-D3", "B3LYP-D3(BJ)", "D3 alone is zero damping in Psi4 and BJ in ORCA"),
    ("B3LYP", "B3LYP-D3(BJ)", "dispersion folded into the method is not the bare functional"),
    ("M06", "M062X", "different functionals"),
    ("M06-2X", "M06-2X-D3", "with dispersion is another level"),
    ("M06-2X", "M06-HF", "different functionals"),
    ("-D3(BJ)", "-D3BJ", "a suffix alias needs a stem"),
    ("CCSD(T)-F12a", "CCSD(T)-F12b", "F12a is not F12b"),
    ("CCSD(T)", "CCSD(T)-F12", "F12 suffix"),
    ("CCSD", "CCSD(T)", "triples"),
    ("HF", "RHF", "spin treatment lives in its own column; alias table"),
    ("B3LYP", "UB3LYP", "unrestricted"),
    ("DLPNO-CCSD(T)", "CCSD(T)", "local approximation"),
    ("PBE", "PBE0", "different functionals"),
    # Named composite methods (ADR 0021): recipes that look alike and are not.
    ("W1BD", "W1-BD", "no source writes W1-BD, so it is not an alias; it stays its own key"),
    ("W1-BD", "W1U", "a hyphenated W1BD is not W1U either"),
    ("W1", "W1U", "W1 uses ROCCSD; W1U uses UCCSD (Gaussian 09 manual, W1 methods)"),
    ("W1", "W1BD", "W1BD replaces coupled cluster with Brueckner doubles"),
    ("W1", "W1RO", "W1RO has a different scalar relativistic correction"),
    ("W1U", "W1BD", "UCCSD vs Brueckner doubles"),
    ("W1U", "W1RO", "UCCSD vs ROCCSD with another relativistic correction"),
    ("W1BD", "W1RO", "Brueckner doubles vs ROCCSD"),
    ("W1", "W2", "different recipes"),
    ("CBS-QB3", "ROCBS-QB3", "restricted-open-shell, no spin correction (Wood 2006)"),
    ("cbsqb3", "rocbsqb3", "the hyphen-free spellings keep the two recipes apart"),
    ("CBS-QB3", "CBS-4M", "different recipes"),
    ("CBS-QB3", "CBS-APNO", "different recipes"),
    ("CBS-QB3", "cbs-qb3-paraskevas", "a correction-table name is not the method; never aliased"),
    ("cbsqb3", "cbs-qb3-paraskevas", "a correction-table name is not the method; never aliased"),
    ("CBS-QB3", "cbsqb32023", "a year suffix names a correction table; never aliased"),
    ("CBS-QB3", "CBS-QB3-2023", "a year suffix names a correction table; never aliased"),
    ("G4", "G4(MP2)", "reduced-order perturbation theory is another recipe"),
    ("G3", "G3(MP2)", "reduced-order perturbation theory is another recipe"),
    ("G3B3", "G3(MP2)B3", "reduced-order perturbation theory is another recipe"),
    ("G3(MP2)", "G3(MP2)B3", "B3LYP geometries and frequencies are another recipe"),
    ("G3", "G3B3", "MP2/HF geometries vs B3LYP geometries"),
    ("G4(MP2)", "G3(MP2)", "different generations"),
]


def _same_method_cases():
    for method, spellings, key in SAME_METHOD:
        for program, spelling in spellings:
            yield pytest.param(spelling, key, id=f"{method}-{program}-{spelling}")


@pytest.mark.parametrize(("spelling", "key"), list(_same_method_cases()))
def test_every_real_spelling_has_the_exact_key(spelling, key):
    assert method_identity_key(spelling) == key


def test_the_same_method_table_is_not_empty():
    # Guards against a parametrize list that silently yields nothing.
    assert sum(len(spellings) for _, spellings, _ in SAME_METHOD) >= 18


@pytest.mark.parametrize(
    ("a", "b"), [pytest.param(a, b, id=why) for a, b, why in DIFFERENT_METHOD]
)
def test_different_methods_keep_different_keys(a, b):
    assert method_identity_key(a) != method_identity_key(b)


def test_surrounding_whitespace_is_not_identity():
    assert method_identity_key("  B3LYP\t") == "b3lyp"


def _all_methods() -> list[str]:
    return sorted(
        {s for _, pairs, _ in SAME_METHOD for _, s in pairs}
        | {s for a, b, _ in DIFFERENT_METHOD for s in (a, b)}
    )


@pytest.mark.parametrize("spelling", _all_methods())
def test_the_key_is_idempotent(spelling):
    # The migration's collision-freedom argument depends on this.
    key = method_identity_key(spelling)
    assert method_identity_key(key) == key


# ---------------------------------------------------------------------------
# lot_hash
# ---------------------------------------------------------------------------


def _hash(method: str, **fields) -> str:
    return _level_of_theory_hash(LevelOfTheoryRef(method=method, **fields))


def test_arc_and_orca_ccsdt_f12_are_one_level_of_theory():
    """The #585 pair: ARC ``ccsd(t)-f12/cc-pvtz-f12``, stored ``CCSD(T)-F12/cc-pVTZ-F12``."""
    assert _hash("ccsd(t)-f12", basis="cc-pvtz-f12") == _hash(
        "CCSD(T)-F12", basis="cc-pVTZ-F12"
    )


@pytest.mark.parametrize(("spelling", "key"), list(_same_method_cases()))
def test_every_same_method_spelling_hashes_alike(spelling, key):
    assert _hash(spelling, basis="cc-pvtz") == _hash(key, basis="cc-pvtz")


@pytest.mark.parametrize(
    ("a", "b"), [pytest.param(a, b, id=why) for a, b, why in DIFFERENT_METHOD]
)
def test_different_methods_hash_apart(a, b):
    assert _hash(a, basis="def2-tzvp") != _hash(b, basis="def2-tzvp")


def test_a_lower_case_method_hashes_as_it_always_did():
    """Rows already spelled like the key keep their hash, so their refs hold."""
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
    pre_585 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert _hash("b3lyp", basis="def2-tzvp") == pre_585
    assert _hash("B3LYP", basis="def2-tzvp") == pre_585


def test_keywords_are_still_hashed_as_written():
    # Free-form route text can hold case-sensitive quoted strings (#602).
    assert _hash("b3lyp", keywords="Opt") != _hash("b3lyp", keywords="opt")


def test_dispersion_and_solvent_case_is_no_longer_identity():
    # Reproduces #602 on main: these were two levels of theory.
    assert _hash("b3lyp", dispersion="d3bj") == _hash("b3lyp", dispersion="D3BJ")


def test_the_upload_path_resolves_both_spellings_to_one_row(db_session):
    stored = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="CCSD(T)-F12", basis="cc-pVTZ-F12")
    )
    arc = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="ccsd(t)-f12", basis="cc-pvtz-f12")
    )
    assert arc.id == stored.id
    # The first uploader's spelling is what the row keeps and shows.
    assert (stored.method, stored.basis) == ("CCSD(T)-F12", "cc-pVTZ-F12")


def test_the_upload_path_joins_the_two_arc_corpus_spellings_of_wb97xd(db_session):
    """The #618 pair: one Gaussian functional, spelled two ways by two ARC corpora."""
    a = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="wb97xd", basis="def2-tzvp"))
    b = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="wb97x-d", basis="def2-tzvp"))
    assert a.id == b.id
    assert a.method == "wb97xd"  # the first uploader's spelling is kept


def test_the_upload_path_keeps_gaussian_wb97xd_apart_from_orca_wb97x_d3(db_session):
    a = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="wB97XD", basis="def2-tzvp"))
    b = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="wB97X-D3", basis="def2-tzvp"))
    assert a.id != b.id


def test_the_upload_path_joins_a_folded_dispersion_to_the_dispersion_column(db_session):
    """#630: ``b3lyp-d3(bj)`` and ``b3lyp`` + ``d3bj`` are one level (they were two in #627)."""
    folded = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="b3lyp-d3(bj)"))
    column = resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="b3lyp", dispersion="d3bj")
    )
    assert folded.id == column.id
    assert resolve_level_of_theory_ref(
        db_session, LevelOfTheoryRef(method="B3LYP-GD3BJ")
    ).id == folded.id
    # The bare functional is still another level.
    bare = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="b3lyp"))
    assert bare.id != folded.id


# ---------------------------------------------------------------------------
# The alias table's own rules (#618)
# ---------------------------------------------------------------------------


def _entries():
    return [*NAME_ALIASES, *SUFFIX_ALIASES]


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e.alias)
def test_every_alias_is_cited(entry):
    assert len(entry.citations) >= 2
    assert all(len(c) > 30 for c in entry.citations)


def test_the_alias_table_is_not_empty():
    assert len(NAME_ALIASES) >= 2 and len(SUFFIX_ALIASES) >= 2


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e.alias)
def test_no_alias_is_program_scoped_while_the_hash_cannot_see_a_program(entry):
    """A scoped entry cannot be honoured: nothing in the hash names a program.

    ``_level_of_theory_hash`` reads only fields of ``LevelOfTheoryRef``, and
    that carries no software. If it ever does, this test is the place to lift.
    """
    assert "software" not in LevelOfTheoryRef.model_fields
    assert "program" not in LevelOfTheoryRef.model_fields
    assert entry.programs is None


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e.alias)
def test_an_alias_is_lower_case_and_is_not_its_own_canonical(entry):
    assert entry.alias == entry.alias.lower()
    assert entry.canonical == entry.canonical.lower()
    assert entry.alias != entry.canonical


def test_no_canonical_key_is_itself_an_alias():
    """Keeps the key idempotent and the rules order-independent."""
    aliases = {a.alias for a in NAME_ALIASES}
    suffixes = {a.alias for a in SUFFIX_ALIASES}
    for entry in NAME_ALIASES:
        assert entry.canonical not in aliases
    for entry in SUFFIX_ALIASES:
        assert entry.canonical not in suffixes


def test_the_two_wb97x_families_the_owner_named_are_not_equated():
    """Gaussian wB97XD is Chai 2008; ORCA wB97X-D3 is a different functional."""
    assert method_identity_key("wB97XD") != method_identity_key("wB97X-D3")
    assert method_identity_key("wB97X-D") != method_identity_key("wB97X-D3")


# ---------------------------------------------------------------------------
# The migration's frozen copy
# ---------------------------------------------------------------------------

_MIGRATION = (
    pathlib.Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "c8424fe82997_key_level_of_theory_method_by_identity.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("_mig_c8424fe82997", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _case_only(spelling: str) -> str:
    return spelling.strip().lower()


@pytest.mark.parametrize("spelling", _all_methods())
def test_migration_method_rule_is_the_case_only_rule(migration, spelling):
    """``c8424fe82997`` ran the case rule; aliases arrived in ``d0a7c3b91e4f``."""
    assert migration._method_identity_key(spelling) == _case_only(spelling)


@pytest.mark.parametrize("spelling", _all_spellings())
def test_migration_basis_rule_matches_the_application(migration, spelling):
    assert migration._basis_identity_key(spelling) == basis_identity_key(spelling)


@pytest.mark.parametrize("method", _all_methods())
def test_migration_hash_matches_the_case_only_formula(migration, method):
    fields = {
        "method": method,
        "basis": "Def2TZVP",
        "aux_basis": "def2/J",
        "cabs_basis": None,
        "dispersion": "d3bj",
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unrestricted",
    }
    row = SimpleNamespace(_mapping=fields)
    app_hash = _level_of_theory_hash(LevelOfTheoryRef(**fields))
    if level_identity_keys(method, "d3bj") == (_case_only(method), "d3bj"):
        assert migration._lot_hash(row, keyed_method=True) == app_hash
    else:
        # An alias spelling: the application now keys it further than the
        # case rule ``c8424fe82997`` ran (``d0a7c3b91e4f`` re-keys those rows; ``f3b8d5a1c702`` also moves a folded dispersion).
        assert migration._lot_hash(row, keyed_method=True) != app_hash


def test_migration_pre_revision_hash_keeps_the_method_verbatim(migration):
    fields = {
        "method": "B3LYP",
        "basis": "def2tzvp",
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": None,
    }
    row = SimpleNamespace(_mapping=fields)
    assert migration._lot_hash(row, keyed_method=False) != migration._lot_hash(
        row, keyed_method=True
    )
    lower = SimpleNamespace(_mapping={**fields, "method": "b3lyp"})
    assert migration._lot_hash(lower, keyed_method=False) == migration._lot_hash(
        lower, keyed_method=True
    )


# ---------------------------------------------------------------------------
# The SQL twins used by the search filters
# ---------------------------------------------------------------------------


#: Edge cases of the alias rules that no real name exercises but a divergence
#: between the Python and SQL keys would show on.
_ALIAS_EDGES = [
    " WB97X-D ",
    "wb97x-d-d3bj",
    "-d3(bj)",
    "-gd3bj",
    "x-d3(bj)",
    "x-gd3bj",
    "b3lyp-d3(bj)-x",
    "b3lyp-d3(bj)",
    "B3LYP-GD3BJ",
    "b3lyp d3(bj)",
    "m06-2x-d3",
    "M062X",
]


@pytest.mark.parametrize("spelling", [*_all_methods(), *_ALIAS_EDGES, "  padded\t"])
def test_the_sql_method_key_agrees_with_python(db_session, spelling):
    assert db_session.scalar(select(method_key_sql(literal(spelling)))) == method_identity_key(
        spelling
    )


@pytest.mark.parametrize("spelling", [*_all_spellings(), "", "   ", " Def2TZVP ", "Def2TZVP/JK"])
def test_the_sql_basis_key_agrees_with_python(db_session, spelling):
    assert db_session.scalar(select(basis_key_sql(literal(spelling)))) == basis_identity_key(
        spelling
    )


def test_the_sql_basis_key_of_null_is_null(db_session):
    assert db_session.scalar(select(basis_key_sql(literal(None, type_=Text)))) is None
