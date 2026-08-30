"""API tests for GET /api/v1/scientific/species/browse."""

from __future__ import annotations

from app.api.config import settings
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The defect this endpoint exists to fix: no identifier is required.

    ``/species/search`` with an empty query string 422s
    (``missing_identifier``); this sibling route must not.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBR")
    )
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/browse")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "request" in body and "review_summary" in body and "records" in body
    assert "pagination" in body
    matching = [r for r in body["records"] if r["species_ref"] == species.public_ref]
    assert len(matching) == 1


def test_get_ignores_identifier_query_params_it_does_not_declare(client, db_session):
    """``smiles=`` is not a declared parameter here -- FastAPI drops it.

    Asserts the *contract*, not just that the request doesn't error: a
    species that would never match ``smiles=DOES_NOT_EXIST`` on
    ``/search`` still shows up here, because browse never reads that
    parameter at all.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBRIGN")
    )
    make_species_entry(db_session, species)

    resp = client.get(
        "/api/v1/scientific/species/browse?smiles=THIS_SMILES_MATCHES_NOTHING"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    matching = [r for r in body["records"] if r["species_ref"] == species.public_ref]
    assert len(matching) == 1
    # And the ignored parameter must not even echo back as a filter --
    # there is no field on SpeciesBrowseRequest to have carried it.
    assert "smiles" not in body["request"]["filter"]


def test_get_parses_collapse_offset_limit(client, db_session):
    a = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBRCO1")
    )
    make_species_entry(db_session, a)
    b = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBRCO2")
    )
    make_species_entry(db_session, b)

    resp = client.get(
        "/api/v1/scientific/species/browse?collapse=first&offset=0&limit=1"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["limit"] == 1
    assert len(body["records"]) <= 1


def test_get_rejects_client_supplied_sort(client, db_session):
    resp = client.get("/api/v1/scientific/species/browse?sort=anything")
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_get_rejects_unknown_include_token(client, db_session):
    resp = client.get("/api/v1/scientific/species/browse?include=banana")
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_get_rejects_section_id_tokens(client, db_session):
    """``thermo`` is legal on ``/species/search`` and refused here.

    Its payload is a bare integer-id array; on an identifier-free,
    unauthenticated, whole-corpus listing that is a primary-key-harvest
    route, so browse never accepts the token at all rather than serving
    it and stripping the ids after the fact.
    """
    for token in ("thermo", "statmech", "transport", "conformers"):
        resp = client.get(f"/api/v1/scientific/species/browse?include={token}")
        assert resp.status_code == 422, (token, resp.text)
        assert resp.json()["code"] == "unknown_include_token"


def test_get_limit_above_the_framework_bound_is_rejected_by_the_framework(
    client, db_session
):
    resp = client.get("/api/v1/scientific/species/browse?limit=999")
    assert resp.status_code == 422
    assert resp.json()["code"] == "request_validation_error"


def test_get_limit_above_the_service_cap_is_rejected_by_the_service(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "public_max_limit", 10)
    resp = client.get("/api/v1/scientific/species/browse?limit=50")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "limit_too_large"


def test_get_offset_beyond_the_shipped_deep_paging_cap_is_rejected(client, db_session):
    base = "/api/v1/scientific/species/browse?offset="

    allowed = client.get(f"{base}{settings.public_max_offset}")
    assert allowed.status_code == 200, allowed.text

    resp = client.get(f"{base}{settings.public_max_offset + 1}")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "offset_too_large"


def test_get_by_formula_narrows_and_serves_that_formula_on_the_wire(client, db_session):
    methyl = make_species(
        db_session,
        smiles="[CH3]",
        multiplicity=2,
        inchi_key=next_inchi_key("APIBRFORMCH3"),
    )
    make_species_entry(db_session, methyl)

    resp = client.get("/api/v1/scientific/species/browse?formula=CH3")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    matching = [r for r in body["records"] if r["species_ref"] == methyl.public_ref]
    assert len(matching) == 1, resp.text
    assert all(r["formula"] == "CH3" for r in body["records"])


def test_get_by_elements_narrows_and_elem_mode_all_is_the_default(client, db_session):
    acetonitrile = make_species(
        db_session, smiles="CC#N", inchi_key=next_inchi_key("APIBRELACN")
    )
    make_species_entry(db_session, acetonitrile)
    ethane = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("APIBRELETH"))
    make_species_entry(db_session, ethane)

    resp = client.get("/api/v1/scientific/species/browse?elements=C,N")

    assert resp.status_code == 200, resp.text
    refs = {r["species_ref"] for r in resp.json()["records"]}
    assert acetonitrile.public_ref in refs
    assert ethane.public_ref not in refs


def test_get_by_elements_any_mode_widens_the_match(client, db_session):
    ethane = make_species(
        db_session, smiles="CC", inchi_key=next_inchi_key("APIBRELANYETH")
    )
    make_species_entry(db_session, ethane)

    resp = client.get("/api/v1/scientific/species/browse?elements=C,N&elem_mode=any")

    assert resp.status_code == 200, resp.text
    refs = {r["species_ref"] for r in resp.json()["records"]}
    assert ethane.public_ref in refs


def test_get_by_max_heavy_atoms_boundary(client, db_session):
    benzene = make_species(
        db_session, smiles="c1ccccc1", inchi_key=next_inchi_key("APIBRHA6")
    )
    make_species_entry(db_session, benzene)
    toluene = make_species(
        db_session, smiles="Cc1ccccc1", inchi_key=next_inchi_key("APIBRHA7")
    )
    make_species_entry(db_session, toluene)

    resp = client.get("/api/v1/scientific/species/browse?max_heavy_atoms=6")

    assert resp.status_code == 200, resp.text
    refs = {r["species_ref"] for r in resp.json()["records"]}
    assert benzene.public_ref in refs
    assert toluene.public_ref not in refs


def test_get_unknown_element_symbol_is_422_not_an_empty_page(client, db_session):
    resp = client.get("/api/v1/scientific/species/browse?elements=Xx")

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "unknown_element_symbol"


def test_get_dummy_atom_wildcard_is_422_not_an_empty_page(client, db_session):
    """``*`` is RDKit's dummy-atom wildcard: ``GetAtomicNumber("*")``
    returns 0 rather than raising, so a naive check would accept it and
    silently match nothing (no formula ever contains ``*``).
    """
    resp = client.get("/api/v1/scientific/species/browse?elements=*")

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "unknown_element_symbol"


def test_get_too_many_element_symbols_is_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species/browse"
        "?elements=C,H,N,O,S,Cl,Br,F,P,I,Na"
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "too_many_element_symbols"


def test_profile_curated_drops_species_with_no_approved_entries(client, db_session):
    """The #277 follow-up (must-fix from review): ``profile=curated``
    narrows visibility to ``approved`` with **no request field set at
    all** -- the field-keyed classification the original #277 fix used
    could not see this, and reproduced the bug verbatim:
    ``?profile=curated`` reported the full unfiltered species count as
    ``pagination.total`` while every record on the page carried
    ``entries: []``. Three species, every entry ``not_reviewed`` --
    mirrors the exact shape measured on the deployed archive (59
    species, zero approved).
    """
    refs = set()
    for i in range(3):
        species = make_species(
            db_session,
            smiles=unique_smiles(),
            inchi_key=next_inchi_key(f"APIBRPROFCUR{i}"),
        )
        make_species_entry(db_session, species)  # not_reviewed; never approved
        refs.add(species.public_ref)

    curated = client.get(
        "/api/v1/scientific/species/browse?profile=curated"
    ).json()
    by_status = client.get(
        "/api/v1/scientific/species/browse?min_review_status=approved"
    ).json()

    curated_refs = {r["species_ref"] for r in curated["records"]}
    by_status_refs = {r["species_ref"] for r in by_status["records"]}

    # None of the three not_reviewed-only species survive either path --
    # identical effective visibility must give identical (empty, for
    # this fixture) answers, not "list all 3" under one spelling and
    # "list none" under the other.
    assert refs.isdisjoint(curated_refs)
    assert refs.isdisjoint(by_status_refs)
    assert curated["pagination"]["total"] == by_status["pagination"]["total"]

def test_get_pagination_total_reflects_full_corpus_not_the_page(client, db_session):
    ids = set()
    for _ in range(4):
        s = make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBRTOT")
        )
        make_species_entry(db_session, s)
        ids.add(s.id)

    resp = client.get("/api/v1/scientific/species/browse?limit=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["records"]) == 2
    assert body["pagination"]["returned"] == 2
    assert body["pagination"]["total"] >= 4
    assert body["pagination"]["total"] != body["pagination"]["returned"]


def test_record_envelope_matches_search_shape(client, db_session):
    """Same four top-level keys as ``/species/search``, so a client can
    reuse one parser across both surfaces.

    ``species_id`` / ``species_entry_id`` are absent, not merely
    unasserted: the Phase D internal-ID policy
    (``app/services/scientific_read/internal_ids.py``) strips every
    ``*_id`` key from a public response by default, and this route runs
    through the exact same boundary helper
    (``apply_internal_ids_visibility``) as ``/species/search``.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("APIBRSHAPE")
    )
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/browse")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"request", "review_summary", "records", "pagination"}
    record = next(r for r in body["records"] if r["species_ref"] == species.public_ref)
    assert set(record.keys()) == {
        "species_ref",
        "canonical_smiles",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "stereo_kind",
        "entries",
    }
    # Exact, not absence-only: an absence-only check ("species_entry_id
    # not in entry_record") cannot see a field added anywhere else in the
    # dict -- only an exact key-set match does. This mirrors the
    # service-level assertion in
    # test_browse_species.py::test_record_shape_is_metadata_only, which
    # is what actually caught the M5 mutation when this API-level version
    # did not.
    entry_record = record["entries"][0]
    assert set(entry_record.keys()) == {
        "species_entry_ref",
        "species_entry_kind",
        "electronic_state_kind",
        "stereo_label",
        "electronic_state_label",
        "term_symbol",
        "isotope_key",
        "species_entry_label",
        "review",
        "availability",
    }
    assert set(entry_record["availability"].keys()) == {
        "has_thermo",
        "has_statmech",
        "has_transport",
        "has_conformers",
        "calculation_count",
    }
    assert set(entry_record["review"].keys()) == {
        "status",
        "reviewed_at",
        "reviewer_kind",
    }
    # thermo/statmech/transport/conformers summaries are permanently
    # illegal on browse (species.py::_BROWSE_LEGAL_INCLUDE_TOKENS), so
    # they are structurally absent -- already implied by the exact
    # key-set match above, and asserted again here by name for a reader
    # who does not want to diff two sets to see it.
    for gated in (
        "thermo_summary",
        "statmech_summary",
        "transport_summary",
        "conformers_summary",
    ):
        assert gated not in entry_record
