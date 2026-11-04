"""A reaction participant serves the computed identity facets of its species entry, not only a joined label.

``species_entry_label`` joins the stereo descriptor, the state kind, the typed ``electronic_state_label``, the typed
``term_symbol`` and the isotope key into one string, so a client cannot tell which part is which. The participant now
also serves the computed parts as separate fields (named as the species surface names them). The typed
``electronic_state_label`` / ``term_symbol`` are served too, but only when they satisfy the write-time grammar, in
their canonical spelling; a legacy value outside it reads ``None``.
"""

from __future__ import annotations

_Z = {"smiles": "C/C=C\\C", "charge": 0, "multiplicity": 1}
_E = {"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1}
_FACET_KEYS = {
    "species_entry_kind",
    "electronic_state_kind",
    "stereo_label",
    "stereo_descriptor",
    "stereo_resolution",
    "isotope_key",
    "electronic_state_label",
    "term_symbol",
}


def _post(client, reactants, products) -> str:
    response = client.post(
        "/api/v1/uploads/reactions",
        json={
            "reversible": False,
            "reactants": [{"species_entry": s} for s in reactants],
            "products": [{"species_entry": s} for s in products],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _entry_ref(db_session, entry_id: int) -> str:
    from app.db.models.reaction import ReactionEntry

    return db_session.get(ReactionEntry, entry_id).public_ref


def _search(client, ref: str) -> dict:
    response = client.get("/api/v1/scientific/reactions/search", params={"reaction_entry_ref": ref})
    assert response.status_code == 200, response.text
    return response.json()["records"][0]


def _full(client, ref: str) -> dict:
    response = client.get(f"/api/v1/scientific/reaction-entries/{ref}/full", params={"include": "species"})
    assert response.status_code == 200, response.text
    return response.json()


def test_z_to_e_participants_carry_their_stereo_facets_on_search_and_full(client, db_session) -> None:
    ref = _entry_ref(db_session, _post(client, [_Z], [_E]))

    record = _search(client, ref)
    full = _full(client, ref)["species"]
    for side in ((record["reactants"], full["reactants"]), (record["products"], full["products"])):
        for participants in side:
            assert len(participants) == 1
            assert _FACET_KEYS <= participants[0].keys()
            assert participants[0]["species_entry_kind"] == "minimum"
            assert participants[0]["electronic_state_kind"] == "ground"

    assert record["reactants"][0]["stereo_label"] == full["reactants"][0]["stereo_label"] == "Z"
    assert record["products"][0]["stereo_label"] == full["products"][0]["stereo_label"] == "E"
    assert record["reactants"][0]["stereo_descriptor"] == "Z"
    assert record["products"][0]["stereo_descriptor"] == "E"


def _assert_no_state_facets(participant: dict) -> None:
    assert participant["stereo_label"] is None
    assert participant["stereo_descriptor"] is None
    assert participant["electronic_state_label"] is None
    assert participant["term_symbol"] is None


def test_a_grammar_valid_state_label_and_term_symbol_are_served_as_facets(client, db_session) -> None:
    """``E`` is both a state letter and a stereo descriptor; it is served as a state label, never as a stereo facet."""
    typed = {"smiles": "[H]", "charge": 0, "multiplicity": 2, "electronic_state_label": "E", "term_symbol": "2S"}
    ref = _entry_ref(db_session, _post(client, [typed, typed], [{"smiles": "[H][H]", "charge": 0, "multiplicity": 1}]))

    record = _search(client, ref)
    participant = record["reactants"][0]
    assert participant["species_entry_label"] == "state E \u00b2S"  # marked, so it cannot read as the descriptor E
    assert participant["electronic_state_label"] == "E"
    assert participant["term_symbol"] == "\u00b2S"  # the canonical spelling, as stored
    assert participant["stereo_label"] is None
    assert participant["stereo_descriptor"] is None
    full = _full(client, ref)["species"]["reactants"][0]
    assert (full["electronic_state_label"], full["term_symbol"]) == ("E", "\u00b2S")


def test_a_legacy_state_label_and_term_symbol_outside_the_grammar_read_as_none(client, db_session) -> None:
    """Rows stored before the grammar (planted via the ORM; an upload of ``R`` is refused) are not served."""
    from sqlalchemy import update

    from app.db.models.species import SpeciesEntry

    typed = {"smiles": "[H]", "charge": 0, "multiplicity": 2, "electronic_state_label": "X"}
    ref = _entry_ref(db_session, _post(client, [typed, typed], [{"smiles": "[H][H]", "charge": 0, "multiplicity": 1}]))
    db_session.execute(
        update(SpeciesEntry)
        .where(SpeciesEntry.electronic_state_label == "X")
        .values(electronic_state_label="R", term_symbol="DEPOSITOR_SENTINEL")
    )
    db_session.flush()

    participant = _search(client, ref)["reactants"][0]
    assert participant["species_entry_label"] == "state R DEPOSITOR_SENTINEL"  # the joined label cannot be taken apart...
    _assert_no_state_facets(participant)  # ...which is why the facets are served separately
    full = _full(client, ref)["species"]["reactants"][0]
    assert (full["electronic_state_label"], full["term_symbol"]) == (None, None)


def test_a_legacy_noncanonical_but_valid_term_symbol_is_served_canonical(client, db_session) -> None:
    from sqlalchemy import update

    from app.db.models.species import SpeciesEntry

    typed = {"smiles": "[H]", "charge": 0, "multiplicity": 2, "electronic_state_label": "X"}
    ref = _entry_ref(db_session, _post(client, [typed, typed], [{"smiles": "[H][H]", "charge": 0, "multiplicity": 1}]))
    db_session.execute(update(SpeciesEntry).where(SpeciesEntry.electronic_state_label == "X").values(term_symbol="2Pi"))
    db_session.flush()

    assert _search(client, ref)["reactants"][0]["term_symbol"] == "\u00b2\u03a0"
