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
        ("wb97xd", "def2tzvp", "wB97X-D", "def2tzvp", "punctuation is an alias, not a case rule"),
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
    assert len(SAME_LEVEL) >= 5


# ---------------------------------------------------------------------------
# No filter site may go back to comparing spellings
# ---------------------------------------------------------------------------

_APP = pathlib.Path(__file__).resolve().parents[3] / "app"
_RAW_COMPARISON = re.compile(r"LevelOfTheory\.(method|basis)\s*==")


def test_no_read_path_compares_a_raw_method_or_basis_spelling():
    """Every ``method=`` / ``basis=`` filter goes through ``lot_identity_filters``."""
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if _RAW_COMPARISON.search(line):
                offenders.append(f"{path.relative_to(_APP)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_scan_sees_the_files_it_is_meant_to_scan():
    files = list(_APP.rglob("*.py"))
    assert any(p.name == "lot_identity_filters.py" for p in files)
    assert len(files) > 300
