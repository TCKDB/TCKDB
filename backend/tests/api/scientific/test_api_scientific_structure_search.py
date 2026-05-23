"""API tests for ``GET|POST /api/v1/scientific/species/structure-search``.

Exercises the RDKit-cartridge-backed structure search at species-entry
grain: substructure (SMARTS + SMILES), similarity (SMILES + InChI),
exact match (InChIKey / SMILES / InChI), input validation, default
trust posture, deterministic sorting, pagination, include behavior,
GET/POST parity, payload safety.
"""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import inchi as _inchi

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


SEARCH_URL = "/api/v1/scientific/species/structure-search"


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _real_inchi_key(smiles: str) -> str:
    """Compute the canonical InChIKey for *smiles* via RDKit."""
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"RDKit could not parse fixture SMILES {smiles!r}"
    return _inchi.MolToInchiKey(mol)


def _make_real_species(db_session, smiles: str):
    """Make a Species + SpeciesEntry where ``inchi_key`` matches RDKit's."""
    species = make_species(
        db_session,
        smiles=smiles,
        inchi_key=_real_inchi_key(smiles),
    )
    entry = make_species_entry(db_session, species)
    return species, entry


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_get_missing_structure_query_returns_422(client, db_session):
    resp = client.get(SEARCH_URL)
    assert resp.status_code == 422
    assert "missing_structure_query" in resp.text


def test_post_missing_structure_query_returns_422(client, db_session):
    resp = client.post(SEARCH_URL, json={"mode": "substructure"})
    assert resp.status_code == 422
    assert "missing_structure_query" in resp.text


def test_get_multiple_structure_queries_returns_422(client, db_session):
    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&query_smarts=%5B%236%5D"
    )
    assert resp.status_code == 422
    assert "multiple_structure_queries" in resp.text


def test_post_multiple_structure_queries_returns_422(client, db_session):
    resp = client.post(
        SEARCH_URL,
        json={
            "query_smiles": "CCO",
            "query_smarts": "[#6]O",
            "mode": "substructure",
        },
    )
    assert resp.status_code == 422
    assert "multiple_structure_queries" in resp.text


def test_invalid_smiles_returns_422(client, db_session):
    resp = client.get(
        SEARCH_URL + "?query_smiles=NOT_A_REAL_MOLECULE&mode=substructure"
    )
    assert resp.status_code == 422
    assert "invalid_structure_query" in resp.text


def test_invalid_smarts_returns_422(client, db_session):
    # `?` is not valid SMARTS atom syntax on its own.
    resp = client.get(
        SEARCH_URL + "?query_smarts=%5B%26%26%26%5D&mode=substructure"
    )
    assert resp.status_code == 422
    assert "invalid_structure_query" in resp.text


def test_substructure_rejects_inchi_key(client, db_session):
    resp = client.get(
        SEARCH_URL
        + "?query_inchi_key=LFQSCWFLJHTTHZ-UHFFFAOYSA-N&mode=substructure"
    )
    assert resp.status_code == 422
    assert "invalid_structure_query" in resp.text


def test_similarity_rejects_smarts(client, db_session):
    resp = client.get(
        SEARCH_URL + "?query_smarts=%5B%236%5D&mode=similarity"
    )
    assert resp.status_code == 422
    assert "invalid_structure_query" in resp.text


def test_exact_rejects_smarts(client, db_session):
    resp = client.get(
        SEARCH_URL + "?query_smarts=%5B%236%5D&mode=exact"
    )
    assert resp.status_code == 422
    assert "invalid_structure_query" in resp.text


def test_get_rejects_client_sort(client, db_session):
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_unknown_include_token_returns_422(client, db_session):
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Substructure mode
# ---------------------------------------------------------------------------


def test_substructure_by_smarts_matches(client, db_session):
    # Ethanol contains a [#6]-O substructure; methane does not.
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "C")

    resp = client.get(
        SEARCH_URL + "?query_smarts=%5B%236%5DO&mode=substructure"
    )
    assert resp.status_code == 200
    refs = [r["species_entry_ref"] for r in resp.json()["records"]]
    assert ethanol_entry.public_ref in refs
    # methane should not match — its species_entry_ref must be absent.
    methane_refs = [
        r["species_entry_ref"]
        for r in resp.json()["records"]
        if r["smiles"] == "C"
    ]
    assert methane_refs == []


def test_substructure_by_smiles_matches(client, db_session):
    # Propanol contains an OCC substructure; methane does not.
    _, propanol_entry = _make_real_species(db_session, "CCCO")
    _make_real_species(db_session, "C")

    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure"
    )
    assert resp.status_code == 200
    refs = [r["species_entry_ref"] for r in resp.json()["records"]]
    assert propanol_entry.public_ref in refs


def test_substructure_does_not_match_absent_pattern(client, db_session):
    _make_real_species(db_session, "C")

    # Phenyl ring is not a substructure of methane.
    resp = client.get(
        SEARCH_URL + "?query_smiles=c1ccccc1&mode=substructure"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# Similarity mode
# ---------------------------------------------------------------------------


def test_similarity_by_smiles_matches_self(client, db_session):
    # Ethanol vs. ethanol should be similarity 1.0.
    _, ethanol_entry = _make_real_species(db_session, "CCO")

    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=similarity&similarity_threshold=0.9"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        r["species_entry_ref"] == ethanol_entry.public_ref
        for r in body["records"]
    )
    self_record = next(
        r
        for r in body["records"]
        if r["species_entry_ref"] == ethanol_entry.public_ref
    )
    assert self_record["match"]["similarity_score"] == pytest.approx(1.0)
    assert self_record["match"]["mode"] == "similarity"


def test_similarity_threshold_filters_results(client, db_session):
    # Ethanol and benzene should not be similar enough at threshold 0.8.
    _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "c1ccccc1")

    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=similarity&similarity_threshold=0.95"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The only thing that should clear a 0.95 threshold is the self-match
    # on ethanol (similarity = 1.0). Benzene should be filtered out.
    smiles_in = {r["smiles"] for r in body["records"]}
    assert "CCO" in smiles_in
    assert "c1ccccc1" not in smiles_in


def test_similarity_records_include_score_and_ordering(
    client, db_session
):
    # Three species with varying similarity to ethanol. The self-match
    # must come first, the close analog next, the dissimilar one last
    # (or excluded if below threshold).
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    _, propanol_entry = _make_real_species(db_session, "CCCO")
    _, methane_entry = _make_real_species(db_session, "C")

    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=similarity&similarity_threshold=0.0"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(
        r["match"]["similarity_score"] is not None for r in body["records"]
    )
    # Each consecutive pair must satisfy score DESC.
    scores = [r["match"]["similarity_score"] for r in body["records"]]
    assert scores == sorted(scores, reverse=True)
    # Self-match (ethanol vs ethanol) ranks first.
    assert body["records"][0]["species_entry_ref"] == ethanol_entry.public_ref


def test_similarity_default_threshold_used_when_omitted(client, db_session):
    # No similarity_threshold supplied → default 0.5 applies. Ethanol
    # vs. ethanol (self) should still match.
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    resp = client.get(SEARCH_URL + "?query_smiles=CCO&mode=similarity")
    assert resp.status_code == 200
    body = resp.json()
    refs = [r["species_entry_ref"] for r in body["records"]]
    assert ethanol_entry.public_ref in refs
    # Default threshold is echoed.
    assert body["request"]["filter"]["similarity_threshold"] == 0.5


# ---------------------------------------------------------------------------
# Exact mode
# ---------------------------------------------------------------------------


def test_exact_by_inchi_key(client, db_session):
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "CCN")

    key = _real_inchi_key("CCO")
    resp = client.get(
        SEARCH_URL + f"?query_inchi_key={key}&mode=exact"
    )
    assert resp.status_code == 200
    refs = [r["species_entry_ref"] for r in resp.json()["records"]]
    assert refs == [ethanol_entry.public_ref]


def test_exact_by_smiles(client, db_session):
    _, ethanol_entry = _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "CCN")

    resp = client.get(SEARCH_URL + "?query_smiles=CCO&mode=exact")
    assert resp.status_code == 200
    refs = [r["species_entry_ref"] for r in resp.json()["records"]]
    assert refs == [ethanol_entry.public_ref]


# ---------------------------------------------------------------------------
# Trust / review behavior
# ---------------------------------------------------------------------------


def test_default_hides_rejected_entries(client, db_session):
    # Use two distinct species that both match a "CCO" substructure
    # (ethanol and isotopically labeled ethanol-d6) so the only thing
    # gating visibility is the second entry's rejected review state.
    _, e_ok = _make_real_species(db_session, "CCO")
    species_d = make_species(
        db_session,
        smiles="[2H]C([2H])([2H])C([2H])([2H])O",
        inchi_key=_real_inchi_key("[2H]C([2H])([2H])C([2H])([2H])O"),
    )
    e_bad = make_species_entry(db_session, species_d)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_bad.id,
        status=RecordReviewStatus.rejected,
    )

    resp = client.get(SEARCH_URL + "?query_smiles=CCO&mode=substructure")
    assert resp.status_code == 200
    refs = [r["species_entry_ref"] for r in resp.json()["records"]]
    assert e_ok.public_ref in refs
    assert e_bad.public_ref not in refs


def test_include_rejected_restores_rejected(client, db_session):
    _, e_ok = _make_real_species(db_session, "CCO")
    species_d = make_species(
        db_session,
        smiles="[2H]C([2H])([2H])C([2H])([2H])O",
        inchi_key=_real_inchi_key("[2H]C([2H])([2H])C([2H])([2H])O"),
    )
    e_bad = make_species_entry(db_session, species_d)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_bad.id,
        status=RecordReviewStatus.rejected,
    )

    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=substructure&include_rejected=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    refs = [r["species_entry_ref"] for r in body["records"]]
    assert e_ok.public_ref in refs
    assert e_bad.public_ref in refs
    # Rejected entry must sort after the OK entry (review_rank ASC).
    pos_ok = refs.index(e_ok.public_ref)
    pos_bad = refs.index(e_bad.public_ref)
    assert pos_ok < pos_bad


# ---------------------------------------------------------------------------
# Pagination + deterministic ordering
# ---------------------------------------------------------------------------


def test_pagination_envelope_correct(client, db_session):
    # Three substructure-matching species; limit=2 must return two pages.
    _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "CCCO")
    _make_real_species(db_session, "CCCCO")

    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure&limit=2&offset=0"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["limit"] == 2
    assert body["pagination"]["offset"] == 0
    assert body["pagination"]["returned"] == 2
    assert body["pagination"]["total"] >= 2


def test_deterministic_ordering_substructure(client, db_session):
    _, e1 = _make_real_species(db_session, "CCO")
    _, e2 = _make_real_species(db_session, "CCCO")
    _, e3 = _make_real_species(db_session, "CCCCO")

    body = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure"
    ).json()
    refs_a = [r["species_entry_ref"] for r in body["records"]]
    # Repeated identical call must produce identical ordering.
    body2 = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure"
    ).json()
    refs_b = [r["species_entry_ref"] for r in body2["records"]]
    assert refs_a == refs_b


# ---------------------------------------------------------------------------
# GET / POST parity
# ---------------------------------------------------------------------------


def test_get_post_parity(client, db_session):
    _make_real_species(db_session, "CCO")
    _make_real_species(db_session, "CCCO")

    get_body = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure"
    ).json()
    post_body = client.post(
        SEARCH_URL,
        json={"query_smiles": "CCO", "mode": "substructure"},
    ).json()
    get_refs = [r["species_entry_ref"] for r in get_body["records"]]
    post_refs = [r["species_entry_ref"] for r in post_body["records"]]
    assert get_refs == post_refs


def test_post_rejects_query_string_filter(client, db_session):
    resp = client.post(
        SEARCH_URL + "?query_smiles=CCO",
        json={"mode": "substructure"},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


# ---------------------------------------------------------------------------
# include tokens
# ---------------------------------------------------------------------------


def test_include_review_token_accepted(client, db_session):
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure&include=review"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "review" in body["request"]["include"]


def test_include_all_expands_to_public_tokens_only(client, db_session):
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL + "?query_smiles=CCO&mode=substructure&include=all"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "review" in body["request"]["include"]
    # internal_ids must NOT be auto-included by ``all``.
    assert "internal_ids" not in body["request"]["include"]


def test_include_all_plus_internal_ids_obeys_policy_off(client, db_session):
    """Without ``allow_internal_ids`` fixture, the request layer drops
    ``internal_ids`` even when explicitly supplied — the policy gate is
    off by default in tests."""
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=substructure&include=all,internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    # internal_ids dropped because policy gate is off.
    assert "internal_ids" not in body["request"]["include"]
    # Records carry no integer ids.
    record = body["records"][0]
    assert "species_id" not in record
    assert "species_entry_id" not in record


def test_include_internal_ids_restores_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _make_real_species(db_session, "CCO")
    resp = client.get(
        SEARCH_URL
        + "?query_smiles=CCO&mode=substructure&include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "internal_ids" in body["request"]["include"]
    record = body["records"][0]
    assert isinstance(record["species_id"], int)
    assert isinstance(record["species_entry_id"], int)


# ---------------------------------------------------------------------------
# Payload safety (recursive walk)
# ---------------------------------------------------------------------------


_FORBIDDEN_KEYS = frozenset(
    {
        "mol",
        "molblock",
        "rdkit_binary",
        "geometry",
        "coordinates",
        "coords",
        "xyz_text",
        "atoms",
        "body",
        "content",
        "data",
    }
)


def _walk(payload, path=""):
    """Yield ``(path, key, value)`` triples for every dict key in payload."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            yield (path, k, v)
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            yield from _walk(item, f"{path}[{idx}]")


def test_payload_does_not_leak_forbidden_keys(client, db_session):
    _make_real_species(db_session, "CCO")
    resp = client.get(SEARCH_URL + "?query_smiles=CCO&mode=substructure")
    assert resp.status_code == 200
    body = resp.json()
    bad = [
        (path, key)
        for path, key, _v in _walk(body)
        if key in _FORBIDDEN_KEYS
    ]
    assert bad == [], f"Forbidden keys leaked: {bad!r}"


# ---------------------------------------------------------------------------
# Stored-mol-column / GiST index path (audit P1-3)
#
# Substructure and similarity SQL builders must read from the stored
# ``species_entry.mol`` cartridge column so the GiST index created in
# migration ``d4e5f6a7b8c9`` drives the scan. Inlining
# ``mol_from_smiles(sp.smiles)`` per row would force a sequential scan
# and silently defeat the index — this guard fails fast if a future
# refactor reintroduces it.
# ---------------------------------------------------------------------------


def test_structure_search_uses_stored_mol_column_not_inline_conversion():
    from pathlib import Path

    from app.services.scientific_read import structure_search as svc

    assert svc._STORED_MOL_EXPR == "se.mol", (
        f"_STORED_MOL_EXPR must be the stored column, got "
        f"{svc._STORED_MOL_EXPR!r}"
    )

    # Check the SQL builder regions of the service source. Inspecting
    # source text (rather than a runtime SQL render) keeps the assertion
    # robust: we don't have to second-guess SQLAlchemy ``text()``
    # rendering or paste literal SQL into the test that drifts when the
    # service evolves.
    source = Path(svc.__file__).read_text(encoding="utf-8")
    sub_start = source.index("def _run_substructure_query")
    sim_start = source.index("def _run_similarity_query")
    exact_start = source.index("def _run_exact_query")
    cartridge_builders = source[sub_start:exact_start]
    assert "mol_from_smiles(sp.smiles)" not in cartridge_builders, (
        "Substructure/similarity builders must not inline "
        "mol_from_smiles(sp.smiles) — that defeats the GiST index. "
        "Read from se.mol via _STORED_MOL_EXPR instead."
    )
    # Sanity: both builders reference the stored column expression.
    assert "_STORED_MOL_EXPR" in source[sub_start:sim_start]
    assert "_STORED_MOL_EXPR" in source[sim_start:exact_start]
