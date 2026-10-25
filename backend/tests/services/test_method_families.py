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
    SUFFIX_STEM_OVERRIDES,
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
    # --- corrected: B97 (Becke 1997) is a global hybrid; the B97-D family stays rung 2 whatever its suffix
    ("B97", F.dft, 4),
    ("B97-1", F.dft, 4),
    ("B971", F.dft, 4),
    ("B972", F.dft, 4),
    ("B98", F.dft, 4),
    ("B97-D", F.dft, 2),
    ("B97-D2", F.dft, 2),
    ("B97-D3", F.dft, 2),
    ("B97-D3BJ", F.dft, 2),
    ("B97-D3(BJ)", F.dft, 2),
    ("B97-D4", F.dft, 2),
    ("B97D", F.dft, 2),
    ("B97D3", F.dft, 2),
    ("B97-3c", F.dft, 2),
    # --- ambiguous across programs: never guessed
    ("CI", F.unclassified, None),
    # --- representatives of each rung's kinds: range-separated, meta-hybrid, double hybrid
    ("CAM-B3LYP", F.dft, 4),
    ("LC-wPBE", F.dft, 4),
    ("HSE06", F.dft, 4),
    ("wB97X-V", F.dft, 4),
    ("wB97M-V", F.dft, 4),
    ("M06", F.dft, 4),
    ("M05-2X", F.dft, 4),
    ("B1B95", F.dft, 4),
    ("TPSSh", F.dft, 4),
    ("B2GP-PLYP", F.dft, 5),
    ("DSD-PBEB95", F.dft, 5),
    ("revDSD-PBEP86", F.dft, 5),
    ("B97M-V", F.dft, 3),
    ("B97M", F.dft, 3),
    ("r2SCAN-3c", F.dft, 3),
    ("PBEh-3c", F.dft, 4),
    ("wB97X-3c", F.dft, 4),
    # --- spellings added: Gaussian forms
    ("M08HX", F.dft, 4),
    ("M11L", F.dft, 3),
    ("MN12L", F.dft, 3),
    ("MN12SX", F.dft, 4),
    ("MN15L", F.dft, 3),
    ("SOGGA11X", F.dft, 4),
    ("N12SX", F.dft, 4),
    ("LC-wHPBE", F.dft, 4),
    ("wB97XD", F.dft, 4),
    ("ωB97XD", F.dft, 4),
    ("LRC-ωPBEh", F.dft, 4),
    # --- bare-D forms
    ("B3LYP-D", F.dft, 4),
    ("B2PLYP-D", F.dft, 5),
    ("B2PLYP-D3BJ", F.dft, 5),
    # --- Molpro
    ("BCCD", F.wavefunction, None),
    ("QCI", F.wavefunction, None),
    ("ACPF", F.multireference, None),
    ("AQCC", F.multireference, None),
    ("RS3", F.multireference, None),
    ("CIC", F.multireference, None),
    ("MRCIC", F.multireference, None),
    ("MRCI-F12+Q", F.multireference, None),
    ("CASPT2", F.multireference, None),
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
    assert method_stem_key(key) == key, "the exact key is tried before any suffix is stripped"
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
    # a table row of its own (b97-d3) wins over the stem it would strip to (b97); nothing else is exempt
    expected = CLASSIFICATION.get(f"{key}-{suffix}", CLASSIFICATION[SUFFIX_STEM_OVERRIDES.get(key, key)])
    assert classify_method(f"{key}-{suffix}") == expected
    assert classify_method(f"{key.upper()}-{suffix.upper()}") == expected
    if f"{key}-{suffix}" not in CLASSIFICATION and key not in SUFFIX_STEM_OVERRIDES:
        assert expected == CLASSIFICATION[key]


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


@pytest.mark.parametrize(
    ("spelled", "family", "rung"),
    [
        ("UM062X", F.dft, 4),
        ("UM06-2X", F.dft, 4),
        ("UwB97XD", F.dft, 4),
        ("UB2PLYP", F.dft, 5),
        ("UB3LYP-D3BJ", F.dft, 4),
        ("ROB3LYP", F.dft, 4),
        ("uccsd", F.wavefunction, None),
        ("rccsd", F.wavefunction, None),
        ("uccsd(t)-f12", F.wavefunction, None),
        ("rohf", F.wavefunction, None),
        ("uhf", F.wavefunction, None),
        ("umpg", F.unclassified, None),  # the rest is not a method
        ("ub", F.unclassified, None),
        ("u", F.unclassified, None),
        ("r", F.unclassified, None),
    ],
)
def test_a_spin_prefix_is_read_through_only_to_a_classified_method(spelled, family, rung):
    """Kills: a prefix stripped without checking the rest is a method, or a method such as RM1 or RPBE mangled."""
    got = classify_method(spelled)
    assert (got.family, got.dft_rung) == (family, rung)


@pytest.mark.parametrize("whole", ["rm1", "rpbe", "rscan", "r2scan", "rs2", "rs3", "uff", "revpbe", "romp2", "rhf"])
def test_a_name_that_is_its_own_row_is_never_read_through_a_prefix(whole):
    """Kills: stripping r/u from a method whose name merely starts with it (RM1 is not R + M1)."""
    assert whole in CLASSIFICATION
    assert classify_method(whole) == CLASSIFICATION[whole]
    assert method_stem_key(whole) == whole


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
                is_key_call = tail in {"sorted", "sort", "min", "max"} and any(k.arg == "key" for k in node.keywords)
                if is_key_call or tail in {"browse_order", "OrderEcho", "order_by", "order_admin", "AdminNode"}:
                    text = ast.unparse(node)
                    hit = [b for b in banned if b in text]
                    assert not hit, f"{rel}: {tail}(...) mentions {hit}: the classification must never order anything"
    assert importers == {
        "api/routes/scientific/calculations.py",
        "api/routes/scientific/kinetics.py",
        "api/routes/scientific/kinetics_search.py",
        "api/routes/scientific/level_of_theory.py",
        "api/routes/scientific/level_of_theory_browse.py",
        "api/routes/scientific/species_browse.py",
        "api/routes/scientific/species_calculations_search.py",
        "api/routes/scientific/thermo.py",
        "api/routes/scientific/thermo_search.py",
        "api/routes/scientific/transition_states.py",
        "api/routes/scientific/transition_states_browse.py",
        "schemas/reads/_method_family.py",
        "schemas/reads/scientific_level_of_theory.py",
        "services/scientific_read/calculation_provenance_filters.py",
        "services/scientific_read/calculations_search.py",
        "services/scientific_read/level_of_theory_search.py",
        "services/scientific_read/lot_identity_filters.py",
        "services/scientific_read/species_calculations_search.py",
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
