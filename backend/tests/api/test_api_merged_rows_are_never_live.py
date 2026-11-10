"""A merged-away row is kept, but no list, search, lookup, count or export shows it as a live record (DR-0038).

The fixture is the REAL post-migration shape (:mod:`tests.api._merged_world`): the kept rows keep their old marked
SMILES, InChIKey, label and stoichiometry hash. Every read path that enumerates or looks up species, entries or
reactions by their own columns is hit with a query that matches the kept row's old values as well as the survivor's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api._merged_world import KEY_INCHI, MARKED_INCHI, World, build_world

_V1 = "/api/v1"
_SCI = f"{_V1}/scientific"

#: (path, query) for every route that enumerates or looks up the three tables without anchoring on a result row.
_ROUTES: list[tuple[str, dict]] = [
    (f"{_SCI}/species/browse", {"include_rejected": True}),
    (f"{_SCI}/species/browse", {"formula": "C4H8", "include_rejected": True}),
    (f"{_SCI}/species/search", {"smiles": "C/C=C/C", "include_rejected": True}),
    (f"{_SCI}/species/search", {"smiles": "CC=CC", "include_rejected": True}),
    (f"{_SCI}/species/search", {"inchi_key": MARKED_INCHI, "include_rejected": True}),
    (f"{_SCI}/species/search", {"inchi_key": KEY_INCHI, "include_rejected": True}),
    (f"{_SCI}/species/structure-search", {"mode": "exact", "query_smiles": "C/C=C/C", "include_rejected": True}),
    (f"{_SCI}/species/structure-search", {"mode": "substructure", "query_smiles": "C=C", "include_rejected": True}),
    (f"{_SCI}/reactions/browse", {"include_rejected": True}),
    (f"{_SCI}/reactions/browse", {"reactant_smiles": "C/C=C/C", "include_rejected": True}),
    (f"{_SCI}/reactions/browse", {"reactant_smiles": "CC=CC", "include_rejected": True}),
    (f"{_SCI}/reactions/search", {"reactants": ["C/C=C/C"], "products": ["CCCC"], "include_rejected": True}),
    (f"{_SCI}/reactions/search", {"reactants": ["CC=CC"], "products": ["CCCC"], "include_rejected": True}),
    (f"{_SCI}/reactions/search", {"include_rejected": True}),
    (f"{_SCI}/species-calculations/search", {"smiles": "C/C=C/C", "include_rejected": True}),
    (f"{_SCI}/export/ml/species.ndjson", {"all": True}),
    (f"{_SCI}/export/ml/reactions.ndjson", {"all": True}),
    (f"{_SCI}/export/ndjson", {"all": True}),
    (f"{_V1}/species", {}),
    (f"{_V1}/species", {"smiles": "C/C=C/C"}),
    (f"{_V1}/species", {"inchi_key": MARKED_INCHI}),
    (f"{_V1}/reactions", {}),
    (f"{_V1}/lookup/species", {"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1}),
    (f"{_V1}/lookup/reaction", {"reactants": ["C/C=C/C"], "products": ["CCCC"]}),
]


@pytest.fixture
def world(db_session, login_as, _api_curator_user) -> World:
    login_as(_api_curator_user)  # the exports are curator routes; the lists behave the same for any role
    return build_world(db_session)


@pytest.mark.parametrize(("path", "params"), _ROUTES, ids=[f"{p.removeprefix(_V1)}?{sorted(q)}" for p, q in _ROUTES])
def test_no_list_search_or_lookup_returns_a_kept_row_as_live(client: TestClient, world: World, path, params) -> None:
    response = client.get(path, params=params)
    assert response.status_code in (200, 404, 422), (path, response.text[:300])
    for ref in world.kept_refs:
        assert ref not in response.text, f"{path} {params} shows the merged-away row {ref}"
    # The kept row's old InChIKey must not appear as a live record's identity either (a query echoes its own).
    if MARKED_INCHI not in str(params):
        assert MARKED_INCHI not in response.text, f"{path} {params} shows the kept species' old InChIKey"


def test_the_survivor_is_what_a_marked_or_flat_spelling_finds(client: TestClient, world: World) -> None:
    for query in ("C/C=C/C", "CC=CC", "C/C=C\\C"):
        response = client.get(
            f"{_SCI}/reactions/search",
            params={"reactants": [query], "products": ["CCCC"], "include_rejected": True},
        )
        assert response.status_code == 200, response.text
        refs = [r["reaction_ref"] for r in response.json()["records"]]
        assert refs == [world.reaction.public_ref], (query, refs)
        response = client.get(
            f"{_SCI}/reactions/browse", params={"reactant_smiles": query, "include_rejected": True}
        )
        assert response.status_code == 200, response.text
        assert [r["reaction_ref"] for r in response.json()["records"]] == [world.reaction.public_ref], query
