"""Phase C client tests: refs accepted as handles and filters.

These verify request shape only; the backend behavior is covered by
backend tests. Each test installs a stub transport, calls a client
method with a ref-flavored argument, and asserts the outgoing HTTP
request encodes the ref correctly.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx

from conftest import make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"records": [], "pagination": {}})


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _capture():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    return seen, handler


# ---------------------------------------------------------------------------
# Path handles accept string refs
# ---------------------------------------------------------------------------


def test_get_species_thermo_accepts_species_entry_ref_handle():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_species_thermo("spe_abcdef0123456789")
    assert seen[0].url.path == (
        "/api/v1/scientific/species-entries/spe_abcdef0123456789/thermo"
    )


def test_get_species_thermo_integer_handle_still_works():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_species_thermo(42)
    assert seen[0].url.path == (
        "/api/v1/scientific/species-entries/42/thermo"
    )


def test_get_reaction_kinetics_accepts_reaction_entry_ref_handle():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_reaction_kinetics("rxe_abcdef0123456789")
    assert seen[0].url.path == (
        "/api/v1/scientific/reaction-entries/rxe_abcdef0123456789/kinetics"
    )


def test_get_reaction_full_accepts_reaction_entry_ref_handle():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_reaction_full("rxe_xyzxyzxyzxyzxyz")
    assert seen[0].url.path == (
        "/api/v1/scientific/reaction-entries/rxe_xyzxyzxyzxyzxyz/full"
    )


# ---------------------------------------------------------------------------
# Detail-route ref filters serialize on the query string
# ---------------------------------------------------------------------------


def test_get_species_thermo_serializes_level_of_theory_ref():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_species_thermo(42, level_of_theory_ref="lot_abcdef0123456789")
    qs = _qs(str(seen[0].url))
    assert qs["level_of_theory_ref"] == ["lot_abcdef0123456789"]


def test_get_reaction_kinetics_serializes_level_of_theory_ref():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.get_reaction_kinetics(42, level_of_theory_ref="lot_abc123")
    qs = _qs(str(seen[0].url))
    assert qs["level_of_theory_ref"] == ["lot_abc123"]


# ---------------------------------------------------------------------------
# Search method ref filters
# ---------------------------------------------------------------------------


def test_search_species_serializes_species_ref():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_species(species_ref="spc_abcdef")
    qs = _qs(str(seen[0].url))
    assert qs["species_ref"] == ["spc_abcdef"]


def test_search_species_serializes_species_entry_ref():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_species(species_entry_ref="spe_abcdef")
    qs = _qs(str(seen[0].url))
    assert qs["species_entry_ref"] == ["spe_abcdef"]


def test_search_reactions_post_serializes_reaction_refs_in_body():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_reactions(
        reaction_ref="rxn_abc", reaction_entry_ref="rxe_def"
    )
    req = seen[0]
    assert req.method == "POST"
    body = json.loads(req.content)
    assert body["reaction_ref"] == "rxn_abc"
    assert body["reaction_entry_ref"] == "rxe_def"


def test_search_reactions_get_serializes_reaction_refs_in_query():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_reactions(reaction_entry_ref="rxe_def", method="GET")
    qs = _qs(str(seen[0].url))
    assert qs["reaction_entry_ref"] == ["rxe_def"]


def test_search_thermo_post_serializes_level_of_theory_ref_in_body():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_thermo(smiles="CCO", level_of_theory_ref="lot_xyz")
    body = json.loads(seen[0].content)
    assert body["level_of_theory_ref"] == "lot_xyz"


def test_search_thermo_post_serializes_species_refs_in_body():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_thermo(
        species_ref="spc_abc", species_entry_ref="spe_def"
    )
    body = json.loads(seen[0].content)
    assert body["species_ref"] == "spc_abc"
    assert body["species_entry_ref"] == "spe_def"


def test_search_kinetics_post_serializes_refs_in_body():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_kinetics(
        reactants=["CC"],
        products=["CO"],
        reaction_entry_ref="rxe_abc",
        level_of_theory_ref="lot_xyz",
    )
    body = json.loads(seen[0].content)
    assert body["reaction_entry_ref"] == "rxe_abc"
    assert body["level_of_theory_ref"] == "lot_xyz"


def test_search_species_calculations_post_serializes_refs_in_body():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_species_calculations(
        smiles="CCO",
        species_entry_ref="spe_abc",
        level_of_theory_ref="lot_xyz",
    )
    body = json.loads(seen[0].content)
    assert body["species_entry_ref"] == "spe_abc"
    assert body["level_of_theory_ref"] == "lot_xyz"


def test_search_species_calculations_get_serializes_refs_in_query():
    seen, handler = _capture()
    client, _ = make_client(handler)
    client.search_species_calculations(
        smiles="CCO",
        level_of_theory_ref="lot_xyz",
        method_http="GET",
    )
    qs = _qs(str(seen[0].url))
    assert qs["level_of_theory_ref"] == ["lot_xyz"]
