"""An sp-only atom cannot link two single points on one geometry (#610).

"At most one ``sp`` per optimisation" is enforced against the linked ``opt``.
An atom's honest shape has no ``opt``, so the rule had nothing to bite on:
two same-level single points linked as role ``sp`` returned 201 where the
relabelled-``opt`` shape of the same atom returned 422 ``*_role_duplicate``.
With no optimisation linked, two ``sp`` links on one geometry are refused with
the same code.
"""

from __future__ import annotations

import copy

import pytest

from tests.api.test_api_bundle_monatomic_sp_primary import (
    _REACTION_URL,
    _SPECIES_URL,
    _h_species,
    _reaction_payload,
    _reshape_reaction_atom_to_sp_only,
    _species_bundle_from_reaction_atom,
)

_SCENARIO = "rotor_scan_5"


def _same_level_sp(primary: dict) -> dict:
    extra = copy.deepcopy(primary)
    extra["key"] = "r_extra_sp"
    extra.pop("artifacts", None)
    extra.pop("depends_on", None)
    return extra


def _species_route(product: str, links: int) -> tuple[str, dict]:
    bundle = _species_bundle_from_reaction_atom(_reaction_payload(_SCENARIO))
    conformer = bundle["conformers"][0]
    conformer["additional_calculations"] = [_same_level_sp(conformer["primary_calculation"])]
    sources = [
        {"calculation_key": conformer["primary_calculation"]["key"], "role": "sp"},
        {"calculation_key": "r_extra_sp", "role": "sp"},
    ][:links]
    bundle[product]["source_calculations"] = sources
    return _SPECIES_URL, bundle


def _reaction_route(product: str, links: int) -> tuple[str, dict]:
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload(_SCENARIO))
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    extra = _same_level_sp(conformer["calculation"])
    extra["geometry_key"] = conformer["geometry"]["key"]
    extra["conformer_key"] = conformer["key"]
    atom["calculations"].append(extra)
    sources = [
        {"calculation_key": conformer["calculation"]["key"], "role": "sp"},
        {"calculation_key": "r_extra_sp", "role": "sp"},
    ][:links]
    atom[product]["source_calculations"] = sources
    return _REACTION_URL, payload


_ROUTES = {"species": _species_route, "reaction": _reaction_route}


@pytest.mark.parametrize("route", ["species", "reaction"])
@pytest.mark.parametrize("product", ["thermo", "statmech"])
def test_two_sp_links_on_an_atom_are_refused(client, route, product):
    url, payload = _ROUTES[route](product, 2)
    resp = client.post(url, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == f"{product}_role_duplicate", body
    assert "at most one 'sp' per atom" in body["detail"]
    assert len(body["context"]["sp_calculation_refs"]) == 2


@pytest.mark.parametrize("route", ["species", "reaction"])
@pytest.mark.parametrize("product", ["thermo", "statmech"])
def test_one_sp_link_on_an_atom_still_stands_beside_a_second_sp(client, route, product):
    """Guard the guard: the second sp may be stored; it just may not be linked twice."""
    url, payload = _ROUTES[route](product, 1)
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:800]
