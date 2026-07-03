"""Phase C API tests: refs accepted as path handles and query/body filters.

Covers the acceptance criteria from the Phase C prompt:

- Path detail routes accept integer IDs **and** public refs.
- Search endpoints accept ``*_ref`` filters where ``*_id`` filters are
  currently accepted.
- ``level_of_theory_ref`` works wherever ``level_of_theory_id`` works.
- Supplying both id and ref validates consistency (422 on conflict).
- Malformed / wrong-type refs return deterministic 422.
- Unknown path refs return 404; unknown query refs return empty record
  sets.
- Existing integer-ID behavior still works.
"""

from __future__ import annotations

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)

# ---------------------------------------------------------------------------
# Path handle: species-entries/{handle}/thermo
# ---------------------------------------------------------------------------


def _species_entry_with_thermo(db_session, smiles: str = "C#CCCN"):
    species = make_species(
        db_session, smiles=smiles, inchi_key=next_inchi_key("PCT")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    return entry


def test_species_thermo_path_accepts_integer_id(client, db_session):
    entry = _species_entry_with_thermo(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    # The integer path handle still works (Phase C contract). The
    # response identifies the entry via its public ref (Phase D
    # hides integer IDs unless include=internal_ids is allowed).
    assert resp.status_code == 200
    assert resp.json()["species_entry_ref"] == entry.public_ref


def test_species_thermo_path_accepts_public_ref(client, db_session):
    entry = _species_entry_with_thermo(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    )
    assert resp.status_code == 200
    assert resp.json()["species_entry_ref"] == entry.public_ref


def test_species_thermo_path_unknown_ref_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-entries/spe_neverexistedabcdef/thermo"
    )
    assert resp.status_code == 404
    assert "species_entry not found" in resp.text


def test_species_thermo_path_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-entries/rxe_abcdef/thermo"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_species_thermo_path_malformed_ref_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-entries/garbage_handle/thermo"
    )
    # garbage_handle parses as a ref candidate ("garbage" prefix); the
    # prefix is unknown for SpeciesEntry → 422 handle_type_mismatch.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Path handle: reaction-entries/{handle}/kinetics, /full
# ---------------------------------------------------------------------------


def _reaction_entry_with_kinetics(db_session):
    a = make_species(db_session, smiles="C#CCN", inchi_key=next_inchi_key("RK1A"))
    b = make_species(db_session, smiles="C#CCO", inchi_key=next_inchi_key("RK1B"))
    a_entry = make_species_entry(db_session, a)
    b_entry = make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[a_entry],
        product_entries=[b_entry],
    )
    make_kinetics(db_session, reaction_entry=re)
    return chem, re


def test_reaction_kinetics_path_accepts_integer_id(client, db_session):
    _, re = _reaction_entry_with_kinetics(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.id}/kinetics"
    )
    assert resp.status_code == 200


def test_reaction_kinetics_path_accepts_public_ref(client, db_session):
    _, re = _reaction_entry_with_kinetics(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/kinetics"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Phase D: integer reaction_entry_id is hidden in the default
    # response. The ref handle round-trips into reaction_entry_ref.
    assert body["reaction_entry_ref"] == re.public_ref


def test_reaction_kinetics_path_unknown_ref_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/reaction-entries/rxe_neverexistedabcdef/kinetics"
    )
    assert resp.status_code == 404


def test_reaction_full_path_accepts_public_ref(client, db_session):
    _, re = _reaction_entry_with_kinetics(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/full"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Phase D: reaction_entry.id is hidden in the default response;
    # callers identify the row by its public ref.
    assert body["reaction_entry"]["reaction_entry_ref"] == re.public_ref


# ---------------------------------------------------------------------------
# Query / body filters: level_of_theory_ref
# ---------------------------------------------------------------------------


def _species_calc_with_lot(db_session):
    species = make_species(
        db_session, smiles="C#CCNO", inchi_key=next_inchi_key("SCL")
    )
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    return species, entry, lot, calc


def test_species_calcs_search_accepts_level_of_theory_ref(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={"smiles": species.smiles, "level_of_theory_ref": lot.public_ref},
    )
    assert resp.status_code == 200
    body = resp.json()
    matching = [
        r
        for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    ]
    assert len(matching) == 1
    # request echo includes the supplied ref.
    assert body["request"]["filter"]["level_of_theory_ref"] == lot.public_ref


def test_species_calcs_lot_id_and_ref_agree(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={
            "smiles": species.smiles,
            "level_of_theory_id": lot.id,
            "level_of_theory_ref": lot.public_ref,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        r["calculation"]["calculation_ref"] == calc.public_ref
        for r in body["records"]
    )


def test_species_calcs_lot_id_and_ref_conflict_returns_422(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    # Make a second LoT and supply its ref alongside the first one's id.
    other_lot = make_lot(db_session, method="m062x", basis="def2tzvp")
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={
            "smiles": species.smiles,
            "level_of_theory_id": lot.id,
            "level_of_theory_ref": other_lot.public_ref,
        },
    )
    assert resp.status_code == 422
    assert "level_of_theory_handle_conflict" in resp.text


def test_species_calcs_unknown_lot_ref_returns_empty(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={
            "smiles": species.smiles,
            "level_of_theory_ref": "lot_neverexistedabcdef",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_species_calcs_malformed_lot_ref_returns_422(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={
            "smiles": species.smiles,
            "level_of_theory_ref": "not-a-ref",
        },
    )
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_species_calcs_wrong_prefix_lot_ref_returns_422(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={
            "smiles": species.smiles,
            "level_of_theory_ref": "rxe_abcdef",
        },
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_species_calcs_post_body_accepts_lot_ref(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.post(
        "/api/v1/scientific/species-calculations/search",
        json={"smiles": species.smiles, "level_of_theory_ref": lot.public_ref},
    )
    assert resp.status_code == 200
    assert any(
        r["calculation"]["calculation_ref"] == calc.public_ref
        for r in resp.json()["records"]
    )


def test_species_calcs_post_query_string_ref_still_rejected(client, db_session):
    species, entry, lot, calc = _species_calc_with_lot(db_session)
    resp = client.post(
        "/api/v1/scientific/species-calculations/search"
        f"?level_of_theory_ref={lot.public_ref}",
        json={"smiles": species.smiles},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


# ---------------------------------------------------------------------------
# Species / reactions search ref filters
# ---------------------------------------------------------------------------


def test_species_search_accepts_species_ref(client, db_session):
    species = make_species(
        db_session, smiles="C#CCNOC", inchi_key=next_inchi_key("SR1")
    )
    make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search",
        params={"species_ref": species.public_ref},
    )
    assert resp.status_code == 200
    matching = [
        r
        for r in resp.json()["records"]
        if r["species_ref"] == species.public_ref
    ]
    assert len(matching) == 1


def test_species_search_accepts_species_entry_ref(client, db_session):
    species = make_species(
        db_session, smiles="C#CCNOCC", inchi_key=next_inchi_key("SR2")
    )
    entry = make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search",
        params={"species_entry_ref": entry.public_ref},
    )
    assert resp.status_code == 200
    body = resp.json()
    matching = [
        r for r in body["records"] if r["species_ref"] == species.public_ref
    ]
    assert len(matching) == 1
    entry_refs = [e["species_entry_ref"] for e in matching[0]["entries"]]
    assert entry_refs == [entry.public_ref]


def test_reactions_search_accepts_reaction_entry_ref(client, db_session):
    a = make_species(db_session, smiles="C#CCO", inchi_key=next_inchi_key("REF_A"))
    b = make_species(db_session, smiles="C#CCN", inchi_key=next_inchi_key("REF_B"))
    a_entry = make_species_entry(db_session, a)
    b_entry = make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[a_entry],
        product_entries=[b_entry],
    )
    resp = client.get(
        "/api/v1/scientific/reactions/search",
        params={"reaction_entry_ref": re.public_ref},
    )
    assert resp.status_code == 200
    matching = [
        r
        for r in resp.json()["records"]
        if r["reaction_entry_ref"] == re.public_ref
    ]
    assert len(matching) == 1


def test_reactions_search_unknown_reaction_ref_returns_empty(client, db_session):
    resp = client.get(
        "/api/v1/scientific/reactions/search",
        params={"reaction_ref": "rxn_neverexistedabcdef"},
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []
