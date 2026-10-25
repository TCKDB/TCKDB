"""The method-family classification: a documented table, applied to the method identity key.

It is a classification of what kind of method a name is, not a measure of accuracy. Each test names what it kills.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.chemistry.method_families import (
    CLASSIFICATION,
    CLASSIFICATION_NAME,
    CLASSIFICATION_REF,
    CLASSIFICATIONS,
    DFT_BY_RUNG,
    DISPERSION_SUFFIXES,
    OTHER_BY_FAMILY,
    MethodClassFilter,
    MethodFamily,
    classify_method,
    method_stem_key,
)
from app.chemistry.method_names import method_identity_key
from app.versioning import UnknownVersion

F = MethodFamily

APP = Path(__file__).resolve().parents[2] / "app"

# The brief's named cases, written out so a table edit that moves one fails here.
NAMED = [
    ("B3LYP", F.dft, 4),
    ("PBE0", F.dft, 4),
    ("M06-2X", F.dft, 4),
    ("ωB97X-D", F.dft, 4),
    ("wB97X-D", F.dft, 4),
    ("B97-D", F.dft, 2),
    ("PBE", F.dft, 2),
    ("BLYP", F.dft, 2),
    ("TPSS", F.dft, 3),
    ("SCAN", F.dft, 3),
    ("M06-L", F.dft, 3),
    ("r2SCAN", F.dft, 3),
    ("B2PLYP", F.dft, 5),
    ("DSD-PBEP86", F.dft, 5),
    ("PWPB95", F.dft, 5),
    ("SVWN", F.dft, 1),
    ("HF", F.wavefunction, None),
    ("MP2", F.wavefunction, None),
    ("MP3", F.wavefunction, None),
    ("MP4", F.wavefunction, None),
    ("CCSD", F.wavefunction, None),
    ("CCSD(T)", F.wavefunction, None),
    ("CCSD(T)-F12", F.wavefunction, None),
    ("CCSD(T)-F12a", F.wavefunction, None),
    ("DLPNO-CCSD(T)", F.wavefunction, None),
    ("CISD", F.wavefunction, None),
    ("QCISD(T)", F.wavefunction, None),
    ("CASSCF", F.multireference, None),
    ("CASPT2", F.multireference, None),
    ("NEVPT2", F.multireference, None),
    ("MRCI", F.multireference, None),
    ("MRCI+Q", F.multireference, None),
    ("G3", F.composite, None),
    ("G4", F.composite, None),
    ("G4MP2", F.composite, None),
    ("CBS-QB3", F.composite, None),
    ("CBS-APNO", F.composite, None),
    ("W1", F.composite, None),
    ("W1BD", F.composite, None),
    ("W2", F.composite, None),
    ("HEAT-345Q", F.composite, None),
    ("PM6", F.semiempirical, None),
    ("GFN2-xTB", F.semiempirical, None),
    ("MMFF94", F.force_field, None),
    ("UFF", F.force_field, None),
]


@pytest.mark.parametrize(("method", "family", "rung"), NAMED, ids=[n[0] for n in NAMED])
def test_the_named_methods_are_classified_as_documented(method, family, rung):
    """Kills: a table edit that moves a method the brief names (B3LYP is rung 4, B97-D rung 2, ...)."""
    got = classify_method(method)
    assert (got.family, got.dft_rung) == (family, rung)


@pytest.mark.parametrize("key", sorted(CLASSIFICATION))
def test_every_table_entry_classifies_as_its_own_row(key):
    """Kills: an entry the lookup cannot reach (a key the identity key rewrites, or one that still has a suffix)."""
    assert method_identity_key(key) == key, "a table key is a fixed point of the method identity key"
    assert method_stem_key(key) == key, "a table key carries no dispersion suffix"
    assert classify_method(key) == CLASSIFICATION[key]


@pytest.mark.parametrize("key", sorted(CLASSIFICATION))
def test_case_and_edge_whitespace_never_change_the_class(key):
    """Kills: a lookup that compares the spelling instead of the identity key."""
    assert classify_method(key.upper()) == CLASSIFICATION[key]
    assert classify_method(f"  {key.title()}\t") == CLASSIFICATION[key]


@pytest.mark.parametrize("suffix", DISPERSION_SUFFIXES)
@pytest.mark.parametrize("key", sorted(CLASSIFICATION))
def test_a_dispersion_suffix_never_changes_the_class(key, suffix):
    """Kills: a dispersion-corrected name falling out of its family (b3lyp-d3bj as unclassified)."""
    assert classify_method(f"{key}-{suffix}") == CLASSIFICATION[key]
    assert classify_method(f"{key.upper()}-{suffix.upper()}") == CLASSIFICATION[key]


@pytest.mark.parametrize(
    ("spelled", "same_as"),
    [
        ("wb97x-d", "wb97xd"),
        ("M06-2X", "m062x"),
        ("M06-2X-D3zero", "m062x"),
        ("cbsqb3", "cbs-qb3"),
        ("CBSAPNO", "cbs-apno"),
        ("G4(MP2)", "g4mp2"),
        ("G3(MP2)B3", "g3mp2b3"),
        ("b3lyp-d3(bj)", "b3lyp"),
        ("b3lyp-gd3bj", "b3lyp"),
    ],
)
def test_the_curated_method_aliases_classify_like_their_canonical_name(spelled, same_as):
    """Kills: classifying before the alias table is applied, so an alias spelling reads as unknown."""
    assert classify_method(spelled) == classify_method(same_as)
    assert classify_method(spelled).family is not F.unclassified


@pytest.mark.parametrize(
    "unknown",
    ["", "   ", "foo", "xyg3", "hf-3c", "casscf(4,4)", "b3lyp/def2-svp", "ccsd(t)-f12/cc-pvtz-f12", "ani-2x", "d3bj", "-d3"],
)
def test_a_method_the_table_does_not_name_is_unclassified_never_guessed(unknown):
    """Kills: a prefix or substring guess (anything containing 'lyp' is not thereby a hybrid)."""
    got = classify_method(unknown)
    assert (got.family, got.dft_rung) == (F.unclassified, None)


def test_none_is_unclassified():
    assert classify_method(None).family is F.unclassified


def test_a_dft_rung_belongs_to_dft_alone():
    """Kills: a rung on a method that is not a density functional."""
    for key, cls in CLASSIFICATION.items():
        assert (cls.dft_rung is not None) == (cls.family is F.dft), key
        assert cls.dft_rung is None or 1 <= cls.dft_rung <= 5, key
    assert set(DFT_BY_RUNG) == {1, 2, 3, 4, 5}
    assert F.dft not in OTHER_BY_FAMILY and F.unclassified not in OTHER_BY_FAMILY


def test_the_classification_is_a_named_version_in_a_registry():
    """Kills: a version in the identifier, or a classification that is not looked up through the registry."""
    assert (CLASSIFICATION_REF.name, CLASSIFICATION_REF.version) == (CLASSIFICATION_NAME, 1)
    assert CLASSIFICATION_NAME == "method_family_classification"
    assert CLASSIFICATIONS.is_current(CLASSIFICATION_REF)
    assert CLASSIFICATIONS.get(CLASSIFICATION_NAME) is CLASSIFICATION
    with pytest.raises(UnknownVersion):
        classify_method("b3lyp", version=2)


@pytest.mark.parametrize(
    ("selected", "method", "kept"),
    [
        (MethodClassFilter.of(None, 4, 4), "b3lyp", True),
        (MethodClassFilter.of(None, 4, 4), "tpss", False),
        (MethodClassFilter.of(None, 4, 4), "b2plyp", False),
        (MethodClassFilter.of(None, 5, None), "b2plyp", True),
        (MethodClassFilter.of(None, 5, None), "b3lyp", False),
        (MethodClassFilter.of(None, None, 1), "svwn", True),
        (MethodClassFilter.of(None, None, 1), "pbe", False),
        (MethodClassFilter.of(None, 1, 5), "ccsd(t)", False),  # a bound implies dft
        (MethodClassFilter.of(None, 1, 5), "foo", False),
        (MethodClassFilter.of([F.wavefunction], 1, 5), "ccsd(t)", False),  # a bound with a family list without dft
        (MethodClassFilter.of([F.dft, F.wavefunction], 4, 4), "b3lyp", True),
        (MethodClassFilter.of([F.dft, F.wavefunction], 4, 4), "ccsd", False),
        (MethodClassFilter.of(None, 5, 4), "b3lyp", False),  # lower bound above the upper
        (MethodClassFilter.of(None, 5, 4), "b2plyp", False),
        (MethodClassFilter.of([F.unclassified], None, None), "foo", True),
        (MethodClassFilter.of([F.unclassified], None, None), "b3lyp", False),
        (MethodClassFilter.of([F.unclassified, F.composite], None, None), "g4", True),
        (MethodClassFilter.of([F.dft], None, None), "foo", False),
        (MethodClassFilter.of([F.dft], None, None), "b3lyp-d4", True),
    ],
)
def test_the_filter_keeps_by_family_and_rung_with_bounds_at_the_edges(selected, method, kept):
    """Kills: a bound that is exclusive, a rung bound that does not imply dft, an unclassified method kept by a dft filter."""
    assert selected.matches(method) is kept


def test_an_inactive_filter_keeps_everything():
    assert MethodClassFilter().matches("anything")
    assert MethodClassFilter().matches(None)


def test_family_and_rung_are_never_an_order_key_a_tie_break_or_a_select_default():
    """No built-in method ranking: the classification may filter, and nothing may sort, rank or default by it.

    Kills: someone adding ``dft_rung`` to a ``browse_order(...)`` key, an ``order_by``, or an import of the
    classification into an ordering or selection engine. The importers are listed, so a new one is a reviewed change.
    """
    banned = ("method_family", "dft_rung", "classify_method", "method_stem_key", "MethodClass", "CLASSIFICATION")
    importers: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = str(path.relative_to(APP))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                if any(n.startswith("app.chemistry.method_families") for n in names):
                    importers.add(rel)
            if isinstance(node, ast.Call):
                tail = ast.unparse(node.func).rsplit(".", 1)[-1]
                if tail in {"browse_order", "OrderEcho", "order_by", "order_admin", "AdminNode"}:
                    text = ast.unparse(node)
                    hit = [b for b in banned if b in text]
                    assert not hit, f"{rel}: {tail}(...) mentions {hit}: the classification must never order anything"
    assert importers == {
        "api/routes/scientific/kinetics.py",
        "api/routes/scientific/kinetics_search.py",
        "api/routes/scientific/level_of_theory.py",
        "api/routes/scientific/level_of_theory_browse.py",
        "api/routes/scientific/species_browse.py",
        "api/routes/scientific/thermo.py",
        "api/routes/scientific/thermo_search.py",
        "api/routes/scientific/transition_states.py",
        "api/routes/scientific/transition_states_browse.py",
        "schemas/reads/_method_family.py",
        "schemas/reads/scientific_level_of_theory.py",
        "services/scientific_read/calculation_provenance_filters.py",
        "services/scientific_read/level_of_theory_search.py",
        "services/scientific_read/lot_identity_filters.py",
        "services/selection_filters.py",
    }, "a new importer of the classification: confirm it filters and orders nothing, then list it here"


def test_no_order_echo_or_browse_order_names_a_family_or_rung():
    """Kills: ``OrderEcho`` or a documented display order carrying the classification as a key."""
    from app.schemas.reads.scientific_common import ORDER_KEY_CATALOG, OrderEcho, browse_order

    for name in ORDER_KEY_CATALOG:
        assert "family" not in name and "rung" not in name, f"order key {name!r}"
    for name in OrderEcho.model_fields:
        assert "family" not in name and "rung" not in name, name
    import app.api.app as app_module  # noqa: F401  (routes registered)

    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func).rsplit(".", 1)[-1] == "browse_order":
                for arg in node.args:
                    assert "family" not in ast.unparse(arg) and "rung" not in ast.unparse(arg), path
    assert browse_order("review_rank", "created_at", "id")
