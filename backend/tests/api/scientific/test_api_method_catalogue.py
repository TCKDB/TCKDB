"""The catalogue reads: method and basis-set browse and detail, and ``include=catalogue`` on a level of theory."""

from __future__ import annotations

import json
import uuid

from app.db.models.common import LiteratureKind, LiteratureTitleSource
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.method_catalogue import MethodCatalogue, MethodCatalogueLiterature
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry

BASE = "/api/v1/scientific"


def _lot(db_session, **fields) -> LevelOfTheory:
    lot = LevelOfTheory(lot_hash=uuid.uuid4().hex * 2, **fields)
    db_session.add(lot)
    db_session.flush()
    return lot


def _level(db_session, **fields) -> LevelOfTheory:
    lot = _lot(db_session, **fields)
    entry = make_species_entry(db_session, make_species(db_session))
    make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)
    make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)
    return lot


def test_the_method_browse_lists_every_entry_in_key_order(client):
    body = client.get(f"{BASE}/methods").json()
    keys = [r["key"] for r in body["records"]]
    assert keys == sorted(keys)
    assert len(keys) == 22
    gfn2 = next(r for r in body["records"] if r["key"] == "gfn2-xtb")
    assert gfn2["short_name"] == "GFN2-xTB"
    assert gfn2["keys"] == ["gfn2", "gfn2-xtb"]
    assert gfn2["family"] == "semiempirical"
    assert gfn2["catalogue_ref"].startswith("mcat_")
    assert [(c["role"], c["doi"]) for c in gfn2["references"]] == [
        ("original", "10.1021/acs.jctc.8b01176"),
        ("implementation", "10.1002/wcms.1493"),
    ]


def test_browse_filters_by_kind_and_family(client):
    dispersion = client.get(f"{BASE}/methods", params={"kind": "dispersion_correction"}).json()["records"]
    assert [r["key"] for r in dispersion] == ["d3bj", "d3zero"]
    assert all(r["family"] is None for r in dispersion)
    dft = client.get(f"{BASE}/methods", params={"method_family": "dft"}).json()["records"]
    assert {r["key"] for r in dft} == {"b2plyp", "b2plypd3", "b3lyp", "m062x", "wb97x-d3", "wb97xd"}
    aux = client.get(f"{BASE}/basis-sets", params={"role": "auxiliary_correlation"}).json()["records"]
    assert [r["key"] for r in aux] == ["aug-cc-pvtz/c"]


def test_usage_and_spellings_are_derived_from_levels_with_calculations(client, db_session):
    _level(db_session, method="GFN2", basis=None)
    _level(db_session, method="gfn2-xtb", basis=None, dispersion=None, solvent="water")
    _lot(db_session, method="gfn2", basis=None, solvent="benzene")  # no calculation: not counted

    body = client.get(f"{BASE}/methods/gfn2").json()
    card = body["record"]["method"]
    assert card["key"] == "gfn2-xtb"
    assert card["usage"] == {"level_usage_count": 2, "calculation_usage_count": 4}
    assert card["spellings_seen"] == ["GFN2", "gfn2-xtb"]
    assert len(body["record"]["levels"]) == 2
    assert all(level["calculation_usage_count"] == 2 for level in body["record"]["levels"])
    assert all(level["level_of_theory_ref"].startswith("lot_") for level in body["record"]["levels"])


def test_a_basis_set_is_reached_by_ref_and_by_spelling(client, db_session):
    _level(db_session, method="dlpno-ccsd(t)-f12", basis="cc-pVTZ-F12", aux_basis="aug-cc-pVTZ/C",
           cabs_basis="cc-pVTZ-F12-CABS")
    listed = client.get(f"{BASE}/basis-sets", params={"role": "auxiliary_correlation"}).json()["records"]
    by_ref = client.get(f"{BASE}/basis-sets/{listed[0]['catalogue_ref']}")
    assert by_ref.status_code == 200, by_ref.text
    card = by_ref.json()["record"]["basis_set"]
    assert card["key"] == "aug-cc-pvtz/c" and card["role"] == "auxiliary_correlation"
    assert card["usage"]["level_usage_count"] == 1 and card["spellings_seen"] == ["aug-cc-pVTZ/C"]
    assert client.get(f"{BASE}/basis-sets/def2tzvp").json()["record"]["basis_set"]["short_name"] == "def2-TZVP"


def test_an_unknown_handle_is_a_404(client):
    for path in (f"{BASE}/methods/not-a-method", f"{BASE}/basis-sets/not-a-basis", f"{BASE}/methods/mcat_unknown"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["code"] == "handle_not_found"


def test_a_level_of_theory_carries_its_catalogue_cards_only_when_asked(client, db_session):
    lot = _level(db_session, method="b3lyp-d3bj", basis="def2tzvp")
    url = f"{BASE}/level-of-theories/{lot.public_ref}"

    plain = client.get(url).json()["record"]
    assert "catalogue" not in plain
    assert plain["available_sections"]["has_catalogue"] is True

    catalogue = client.get(url, params={"include": "catalogue"}).json()["record"]["catalogue"]
    assert catalogue["method"]["key"] == "b3lyp"
    assert catalogue["dispersion"]["key"] == "d3bj"
    assert catalogue["dispersion"]["spellings_seen"] == ["b3lyp-d3bj"]
    assert catalogue["basis"]["key"] == "def2-tzvp"
    assert catalogue["aux_basis"] is None and catalogue["cabs_basis"] is None


def test_a_level_whose_spellings_key_to_nothing_has_no_cards(client, db_session):
    lot = _level(db_session, method="pm7", basis="sto-3g")
    record = client.get(f"{BASE}/level-of-theories/{lot.public_ref}", params={"include": "catalogue"}).json()["record"]
    assert record["available_sections"]["has_catalogue"] is False
    assert record["catalogue"] == {
        "method": None, "dispersion": None, "basis": None, "aux_basis": None, "cabs_basis": None
    }


def test_the_level_of_theory_browse_carries_catalogue_cards(client, db_session):
    lot = _level(db_session, method="wB97X-D", basis="def2-TZVP", solvent="toluene")
    body = client.get(
        f"{BASE}/level-of-theories/browse", params={"level_of_theory_ref": lot.public_ref, "include": "catalogue"}
    ).json()
    assert body["records"][0]["catalogue"]["method"]["short_name"] == "ωB97X-D"


def test_a_reference_doi_is_served_only_when_the_registry_resolved_it(client, db_session):
    entry = db_session.query(MethodCatalogue).filter_by(key="g4").one()
    link = db_session.query(MethodCatalogueLiterature).filter_by(method_catalogue_id=entry.id).one()
    deposited = Literature(
        kind=LiteratureKind.article,
        title="typed by a depositor",
        title_source=LiteratureTitleSource.depositor_supplied,
        doi="10.9999/deposited.only",
    )
    db_session.add(deposited)
    db_session.flush()
    link.literature_id = deposited.id
    db_session.flush()

    body = client.get(f"{BASE}/methods/g4").json()
    (citation,) = body["record"]["method"]["references"]
    assert citation["literature_ref"] == deposited.public_ref
    assert citation["doi"] is None
    assert "10.9999/deposited.only" not in json.dumps(body)
    resolved = client.get(f"{BASE}/methods/mp2").json()["record"]["method"]["references"][0]
    assert resolved["doi"] == "10.1103/physrev.46.618"


def test_no_catalogue_response_carries_an_internal_id(client):
    for path in (f"{BASE}/methods", f"{BASE}/basis-sets", f"{BASE}/methods/b3lyp", f"{BASE}/basis-sets/def2-svp"):
        text = client.get(path).text
        assert '_id"' not in text and '"id"' not in text, path
