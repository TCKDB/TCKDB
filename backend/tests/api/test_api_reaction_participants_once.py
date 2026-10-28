"""A reaction's participant rows list each distinct species entry ONCE per side, carrying its coefficient.

``H + H -> H2`` has two structure-participant rows for the one H entry. The served rows used to be one per
occurrence while each also carried the species-level coefficient (2), so a consumer that rebuilt the equation from
the rows read ``2 H + 2 H -> H2`` (4 H). Each distinct entry now appears once with its own count; the served
``equation`` string keeps the written form (``[H] + [H] -> [H][H]``).
"""

from __future__ import annotations

_H = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


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


def _entry_ref(client, db_session, entry_id: int) -> str:
    from app.db.models.reaction import ReactionEntry

    return db_session.get(ReactionEntry, entry_id).public_ref


def _full(client, ref: str) -> dict:
    response = client.get(f"/api/v1/scientific/reaction-entries/{ref}/full", params={"include": "species"})
    assert response.status_code == 200, response.text
    return response.json()


def _search(client, ref: str) -> dict:
    response = client.get("/api/v1/scientific/reactions/search", params={"reaction_entry_ref": ref})
    assert response.status_code == 200, response.text
    return response.json()["records"][0]


def test_h_plus_h_gives_h2_lists_the_h_entry_once_with_coefficient_two(client, db_session) -> None:
    ref = _entry_ref(client, db_session, _post(client, [_H, _H], [_H2]))

    full = _full(client, ref)
    reactants = full["species"]["reactants"]
    assert [(p["smiles"], p["stoichiometry"]) for p in reactants] == [("[H]", 2)]
    assert [(p["smiles"], p["stoichiometry"]) for p in full["species"]["products"]] == [("[H][H]", 1)]

    record = _search(client, ref)
    assert [(p["smiles"], p["stoichiometry"]) for p in record["reactants"]] == [("[H]", 2)]
    # The served equation is the written form and does not move.
    assert record["equation"] == "[H] + [H] -> [H][H]"


def test_a_coefficient_one_reaction_lists_every_participant_once_with_one(client, db_session) -> None:
    ref = _entry_ref(client, db_session, _post(client, [_H2], [_H, _H]))
    record = _search(client, ref)
    assert [(p["smiles"], p["stoichiometry"]) for p in record["reactants"]] == [("[H][H]", 1)]
    assert [(p["smiles"], p["stoichiometry"]) for p in record["products"]] == [("[H]", 2)]


def test_two_entries_of_one_species_on_a_side_each_keep_their_own_count(client, db_session) -> None:
    z = {"smiles": "C/C=C\\C", "charge": 0, "multiplicity": 1}
    e = {"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1}
    ref = _entry_ref(client, db_session, _post(client, [z, e], [e, e]))
    full = _full(client, ref)
    assert sorted(p["stoichiometry"] for p in full["species"]["reactants"]) == [1, 1]
    assert [p["stoichiometry"] for p in full["species"]["products"]] == [2]
