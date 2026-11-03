"""A reaction participant serves the computed identity facets of its species entry, not only a joined label.

``species_entry_label`` joins the stereo descriptor, the state kind, the typed ``electronic_state_label``, the typed
``term_symbol`` and the isotope key into one string, so a client cannot tell which part is which. The participant now
also serves the computed parts as separate fields (named as the species surface names them), and never the typed
``electronic_state_label`` / ``term_symbol``.
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


def test_the_typed_state_label_and_term_symbol_are_not_served_as_facets(client, db_session) -> None:
    """A depositor-typed ``electronic_state_label`` of ``R`` joins the label but is not a stereo facet."""
    typed = {"smiles": "[H]", "charge": 0, "multiplicity": 2, "electronic_state_label": "R"}
    ref = _entry_ref(db_session, _post(client, [typed, typed], [{"smiles": "[H][H]", "charge": 0, "multiplicity": 1}]))

    participant = _search(client, ref)["reactants"][0]
    assert participant["species_entry_label"] == "R"  # the joined label cannot be taken apart...
    assert participant["stereo_label"] is None  # ...which is why the facets are served separately
    assert participant["stereo_descriptor"] is None
    assert "electronic_state_label" not in participant
    assert "term_symbol" not in participant
