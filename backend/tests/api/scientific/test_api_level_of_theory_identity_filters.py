"""``method=`` / ``basis=`` filters compare identity keys, not spellings (#585).

``level_of_theory.lot_hash`` is taken over the method's and basis's identity
keys, so ``wb97xd`` / ``def2tzvp`` and ``WB97XD`` / ``def2-TZVP`` are one
level of theory. A filter that compared the stored spelling answered a
different question: it found the row only when the caller happened to type
the spelling the first uploader used.

The rows are stored the way real producers spell them (Gaussian and ORCA in
mixed case, Molpro in its own, ARC lower-cased) and every request uses a
*different* spelling of the same level. Each test also asserts the row comes
back with its stored spelling, which is what display keeps.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)

#: (stored method, stored basis, request method, request basis, program)
SAME_LEVEL = [
    ("CCSD(T)-F12", "cc-pVTZ-F12", "ccsd(t)-f12", "cc-pvtz-f12", "stored ORCA-style, ARC request"),
    ("wb97xd", "def2tzvp", "WB97XD", "def2-TZVP", "stored ARC, Gaussian-style request"),
    ("DLPNO-CCSD(T)-F12", "cc-pVDZ-F12", "dlpno-ccsd(t)-f12", "cc-pvdz-f12", "ORCA vs ARC"),
    ("B3LYP", "Def2SVP", "b3lyp", "def2-svp", "Gaussian basis spelling vs Psi4"),
    ("CCSD(T)-F12a", "cc-pVTZ", "ccsd(t)-f12A", "ccpvtz", "Molpro vs PySCF key"),
    ("wb97xd", "def2tzvp", "wB97X-D", "def2-tzvp", "#618: stored ARC, Q-Chem-style request"),
    ("wb97x-d", "def2tzvp", "WB97XD", "def2-tzvp", "#618: second ARC corpus, Gaussian request"),
    ("M062X", "6-311+G(d,p)", "m06-2x", "6-311+g(d,p)", "#618: Gaussian stored, ARC request"),
    ("B3LYP-D3(BJ)", "def2-tzvp", "b3lyp-gd3bj", "def2-tzvp", "#618: folded dispersion"),
]


@pytest.fixture(params=SAME_LEVEL, ids=[c[-1] for c in SAME_LEVEL])
def seeded(request, db_session):
    stored_method, stored_basis, ask_method, ask_basis, _label = request.param
    lot = make_lot(db_session, method=stored_method, basis=stored_basis)
    other = make_lot(db_session, method=stored_method + "-other", basis=stored_basis)
    species = make_species(
        db_session, smiles="[CH3]", inchi_key=next_inchi_key("IDF")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    decoy = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=other.id
    )
    return {
        "lot": lot,
        "calc": calc,
        "decoy": decoy,
        "stored": (stored_method, stored_basis),
        "ask": (ask_method, ask_basis),
    }


def _qs(method, basis) -> str:
    from urllib.parse import quote

    return f"method={quote(method)}&basis={quote(basis)}"


def test_lot_search_finds_the_row_and_shows_its_stored_spelling(client, seeded):
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/search?{_qs(*seeded['ask'])}"
    )
    assert resp.status_code == 200, resp.text
    lots = [r["level_of_theory"] for r in resp.json()["records"]]
    assert [lot["level_of_theory_ref"] for lot in lots] == [seeded["lot"].public_ref]
    assert (lots[0]["method"], lots[0]["basis"]) == seeded["stored"]


def test_lot_browse_finds_the_row(client, seeded):
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/browse?{_qs(*seeded['ask'])}"
    )
    assert resp.status_code == 200, resp.text
    refs = [r["level_of_theory"]["level_of_theory_ref"] for r in resp.json()["records"]]
    assert refs == [seeded["lot"].public_ref]


def test_calculation_search_finds_the_calculation(client, seeded):
    resp = client.get(
        f"/api/v1/scientific/calculations/search?{_qs(*seeded['ask'])}"
    )
    assert resp.status_code == 200, resp.text
    refs = {r["calculation"]["calculation_ref"] for r in resp.json()["records"]}
    assert refs == {seeded["calc"].public_ref}


def test_species_calculation_search_finds_the_calculation(client, seeded):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=[CH3]&"
        + _qs(*seeded["ask"])
    )
    assert resp.status_code == 200, resp.text
    records = resp.json()["records"]
    assert len(records) == 1, resp.text


def test_legacy_calculation_list_finds_the_calculation(client, seeded):
    resp = client.get(f"/api/v1/calculations?{_qs(*seeded['ask'])}")
    assert resp.status_code == 200, resp.text
    assert [c["id"] for c in resp.json()["items"]] == [seeded["calc"].id]


def test_legacy_level_of_theory_list_finds_the_row_with_its_stored_spelling(
    client, seeded
):
    resp = client.get(f"/api/v1/levels-of-theory?{_qs(*seeded['ask'])}")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["id"] for i in items] == [seeded["lot"].id]
    assert (items[0]["method"], items[0]["basis"]) == seeded["stored"]


def test_method_alone_is_case_blind_but_still_exact_otherwise(client, seeded):
    method = seeded["ask"][0]
    resp = client.get(
        "/api/v1/scientific/level-of-theories/search?method="
        + method.replace("(", "%28").replace(")", "%29")
    )
    assert resp.status_code == 200, resp.text
    refs = [r["level_of_theory"]["level_of_theory_ref"] for r in resp.json()["records"]]
    # The "-other" decoy has a different method and must not match.
    assert refs == [seeded["lot"].public_ref]


# ---------------------------------------------------------------------------
# What must stay two levels of theory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored_method", "stored_basis", "ask_method", "ask_basis", "why"),
    [
        ("wb97xd", "def2tzvp", "wB97X-D3", "def2tzvp", "Gaussian wB97XD is not ORCA wB97X-D3"),
        ("b3lyp", "def2tzvp", "b3lyp-d3(bj)", "def2tzvp", "a request that folds a dispersion in needs the level to have it"),
        ("wb97x", "def2tzvp", "wb97x-d3bj", "def2tzvp", "wB97X-D3BJ is a refit functional, not wB97X plus D3BJ"),
        ("CCSD(T)-F12a", "cc-pVTZ", "CCSD(T)-F12b", "cc-pVTZ", "F12a is not F12b"),
        ("b3lyp", "6-31G*", "b3lyp", "6-31G**", "6-31G* is not 6-31G**"),
        ("b3lyp", "cc-pVTZ", "b3lyp", "aug-cc-pVTZ", "aug- prefix"),
        ("hf", "def2tzvp", "rhf", "def2tzvp", "HF is not RHF here"),
    ],
)
def test_a_different_method_or_basis_does_not_match(
    client, db_session, stored_method, stored_basis, ask_method, ask_basis, why
):
    lot = make_lot(db_session, method=stored_method, basis=stored_basis)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("IDN"))
    entry = make_species_entry(db_session, species)
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/search?{_qs(ask_method, ask_basis)}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == [], why


def test_the_seeded_fixture_is_not_empty(db_session):
    # Guards the parametrised fixture above against yielding nothing.
    assert len(SAME_LEVEL) >= 9


# ---------------------------------------------------------------------------
# dispersion= / solvent= compare identity keys too (#602)
# ---------------------------------------------------------------------------

#: (field, stored, requested): the same component under two spellings.
SAME_COMPONENT = [
    ("dispersion", "GD3BJ", "gd3bj"),
    ("dispersion", "d3bj", "D3BJ"),
    ("dispersion", "EmpiricalDispersion=GD3BJ", "d3bj"),
    ("dispersion", "gd3bj", "D3(BJ)"),
    ("solvent", "Water", "water"),
    ("solvent", "acetonitrile", "ACETONITRILE"),
]


@pytest.fixture(params=SAME_COMPONENT, ids=lambda c: f"{c[0]}-{c[1]}-{c[2]}")
def component(request, db_session):
    field, stored, ask = request.param
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp", **{field: stored})
    decoy = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    return {"field": field, "stored": stored, "ask": ask, "lot": lot, "decoy": decoy}


def test_legacy_list_filters_dispersion_and_solvent_by_key(client, component):
    resp = client.get(f"/api/v1/levels-of-theory?{component['field']}={component['ask']}")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["id"] for i in items] == [component["lot"].id]
    # Display keeps the stored spelling.
    assert items[0][component["field"]] == component["stored"]


def test_lot_search_filters_dispersion_and_solvent_by_key(client, db_session, component):
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("IDC"))
    entry = make_species_entry(db_session, species)
    for lot in (component["lot"], component["decoy"]):
        make_calculation(
            db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
        )
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/search?{component['field']}={component['ask']}"
    )
    assert resp.status_code == 200, resp.text
    lots = [r["level_of_theory"] for r in resp.json()["records"]]
    assert [lot["level_of_theory_ref"] for lot in lots] == [component["lot"].public_ref]
    assert lots[0][component["field"]] == component["stored"]


def _search_refs(client, query: str) -> list[str]:
    resp = client.get(f"/api/v1/scientific/level-of-theories/search?{query}")
    assert resp.status_code == 200, resp.text
    return [r["level_of_theory"]["level_of_theory_ref"] for r in resp.json()["records"]]


def test_folded_dispersion_and_column_dispersion_are_one_level_in_every_filter(
    client, db_session
):
    """#630: ``b3lyp-d3bj`` is ``b3lyp`` + ``d3bj``, so each filter finds it by
    either spelling, and the other levels stay out."""
    folded = make_lot(db_session, method="b3lyp-d3bj", basis="def2tzvp")
    bare = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    refit = make_lot(db_session, method="wb97x-d3bj", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("IDF2"))
    entry = make_species_entry(db_session, species)
    for lot in (folded, bare, refit):
        make_calculation(
            db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
        )

    # The request folds the dispersion in: the bare functional is excluded.
    assert _search_refs(client, "method=b3lyp-d3%28bj%29") == [folded.public_ref]
    # The dispersion filter sees the folded dispersion.
    assert _search_refs(client, "dispersion=GD3BJ") == [folded.public_ref]
    # Method alone is the method component: both b3lyp levels.
    assert sorted(_search_refs(client, "method=B3LYP")) == sorted(
        [folded.public_ref, bare.public_ref]
    )
    # A refit functional is not wb97x plus a dispersion.
    assert _search_refs(client, "method=wb97x&dispersion=d3bj") == []
    assert _search_refs(client, "method=wb97x-d3bj") == [refit.public_ref]


def test_a_solvent_synonym_does_not_match(client, db_session):
    """``h2o`` is not ``water`` to a case rule; that needs a curated table."""
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp", solvent="water")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("IDH"))
    entry = make_species_entry(db_session, species)
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    resp = client.get("/api/v1/scientific/level-of-theories/search?solvent=h2o")
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == []


# ---------------------------------------------------------------------------
# No filter site may go back to comparing spellings
# ---------------------------------------------------------------------------

_APP = pathlib.Path(__file__).resolve().parents[3] / "app"
_COLUMN = r"\b(?:LevelOfTheory|lot)\.(?:method|basis)\b"
#: A raw comparison of a stored method/basis spelling, in any of the forms a
#: filter takes: ``==`` / ``!=`` either way round (the operator may follow a
#: line break), ``.in_``, ``.ilike``, ``.like`` and ``.startswith``, and a
#: hand-rolled ``func.lower(...)``.
_RAW_COMPARISON = re.compile(
    rf"{_COLUMN}\s*[=!]="
    rf"|[=!]=\s*{_COLUMN}"
    rf"|{_COLUMN}\s*\.\s*(?:in_|not_in|ilike|like|startswith|contains)\b"
    rf"|lower\(\s*{_COLUMN}",
    re.S,
)


def test_no_read_path_compares_a_raw_method_or_basis_spelling():
    """Every ``method=`` / ``basis=`` filter goes through ``lot_identity_filters``."""
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        text = path.read_text()
        for match in _RAW_COMPARISON.finditer(text):
            number = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(_APP)}:{number}: {match.group(0)!r}")
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "line",
    [
        "stmt.where(LevelOfTheory.method == request.method)",
        "stmt.where(request.method == LevelOfTheory.method)",
        "stmt.where(LevelOfTheory.basis\n    == request.basis)",
        "stmt.where(LevelOfTheory.basis != x)",
        "stmt.where(LevelOfTheory.method.in_(names))",
        "stmt.where(LevelOfTheory.basis.ilike('def2%'))",
        "stmt.where(func.lower(LevelOfTheory.method) == 'b3lyp')",
        "stmt.where(lot.basis == basis)",
    ],
)
def test_the_scan_pattern_catches_each_form(line):
    assert _RAW_COMPARISON.search(line), line


def test_the_scan_sees_the_files_it_is_meant_to_scan():
    files = list(_APP.rglob("*.py"))
    assert any(p.name == "lot_identity_filters.py" for p in files)
    assert len(files) > 300
