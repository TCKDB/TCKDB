"""A calculation's ``conformer_key`` and ``geometry_key`` may not name different conformers (#635).

A species-side calculation can state two local keys: ``conformer_key``, the
conformer it is evidence for (its anchor), and ``geometry_key``, the geometry
it ran on. Each used to be checked alone. ``conformer_key`` for conformer b
beside ``geometry_key`` for conformer a's geometry was accepted, the anchor
followed ``conformer_key``, and every rule that reads the anchor (the SCF
stability same-conformer rule among them) trusted it. A geometry and a
conformer must never disagree, and neither key wins: the deposit is refused,
with a coded 422 that names both keys.

Only two request models give a calculation both keys: the species of
``/uploads/computed-reaction`` and the species of ``/uploads/networks/pdep``.
The last tests pin that, so a route that gains both fields fails there until it
carries the refusal too. Each test says which mutation falsifies it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from tests.api.test_api_bundle_extras_622 import _ethane_bundle
from tests.api.test_api_bundle_extras_622_sources import _two_conformer_h2_bundle
from tests.workflows.test_network_pdep_upload import (
    _LOT_CC,
    _LOT_DFT,
    _XYZ_O2,
    _full_payload,
)
from tests.workflows.test_network_pdep_upload import (
    _SOFTWARE as _PDEP_SOFTWARE,
)

CODE = "calculation_geometry_conformer_mismatch"
_REACTION_URL = "/api/v1/uploads/computed-reaction"
_PDEP_URL = "/api/v1/uploads/networks/pdep"


# ---------------------------------------------------------------------------
# Payload builders: one species with two conformers, a and b, and a single
# calculation (the *-sp-b one) whose two keys the test chooses.
# ---------------------------------------------------------------------------


def _reaction_with_keys(conformer_key: str | None, geometry_key: str | None) -> dict:
    """``h2`` has conformers h2-conf (geometry h2-geom) and h2-conf-b (geometry h2-geom-b)."""
    bundle = _two_conformer_h2_bundle()
    calc = next(c for c in bundle["species"][1]["calculations"] if c["key"] == "h2-sp-b")
    calc.pop("conformer_key", None)
    calc.pop("geometry_key", None)
    if conformer_key is not None:
        calc["conformer_key"] = conformer_key
    if geometry_key is not None:
        calc["geometry_key"] = geometry_key
    return bundle


def _pdep_with_keys(conformer_key: str | None, geometry_key: str | None) -> dict:
    payload = _full_payload(include_solve=False)
    conformers = []
    for letter in ("a", "b"):
        conformers.append(
            {
                "key": f"o2_conf_{letter}",
                "geometry": {"key": f"o2_geom_{letter}", "xyz_text": _XYZ_O2},
                "calculation": {
                    "key": f"o2_opt_{letter}",
                    "type": "opt",
                    "software_release": _PDEP_SOFTWARE,
                    "level_of_theory": _LOT_DFT,
                    "opt_converged": True,
                },
            }
        )
    calc = {
        "key": "o2_sp_b",
        "type": "sp",
        "software_release": _PDEP_SOFTWARE,
        "level_of_theory": _LOT_CC,
        "sp_electronic_energy_hartree": -150.2,
    }
    if conformer_key is not None:
        calc["conformer_key"] = conformer_key
    if geometry_key is not None:
        calc["geometry_key"] = geometry_key
    payload["species"][1]["conformers"] = conformers
    payload["species"][1]["calculations"] = [calc]
    return payload


_ROUTES = pytest.mark.parametrize(
    ("url", "build", "calc_key", "conf_a", "conf_b", "geom_a", "geom_b"),
    [
        pytest.param(
            _REACTION_URL, _reaction_with_keys, "h2-sp-b",
            "h2-conf", "h2-conf-b", "h2-geom", "h2-geom-b",
            id="computed-reaction",
        ),
        pytest.param(
            _PDEP_URL, _pdep_with_keys, "o2_sp_b",
            "o2_conf_a", "o2_conf_b", "o2_geom_a", "o2_geom_b",
            id="network-pdep",
        ),
    ],
)


def _assert_mismatch(resp, *, calc_key, conformer_key, geometry_key, geometry_conformer_key) -> None:
    assert (resp.status_code, resp.json().get("code")) == (422, CODE), resp.text[:800]
    body = resp.json()
    assert body["context"] == {
        "field": f"calculations['{calc_key}']",
        "calculation_key": calc_key,
        "conformer_key": conformer_key,
        "geometry_key": geometry_key,
        "geometry_conformer_key": geometry_conformer_key,
    }, body
    # Both keys, and the conformer the geometry actually belongs to, in the sentence.
    # (``msg``, not the whole ``detail``: Pydantic echoes the rejected input back beside it.)
    sentence = body["detail"][0]["msg"]
    for key in (conformer_key, geometry_key, geometry_conformer_key):
        assert f"'{key}'" in sentence, body
    assert "different conformers" in sentence, body
    # Local keys only (DR-0028): every context value is the depositor's own string.
    assert all(isinstance(v, str) for v in body["context"].values()), body


@_ROUTES
def test_conformer_key_beside_another_conformers_geometry_is_refused(
    client, url, build, calc_key, conf_a, conf_b, geom_a, geom_b
):
    """conformer_key=a with geometry_key=b's geometry, and the other way round: refused.

    Neither key wins; the context names the conformer the geometry belongs to.

    Mutation: delete the comparison in the shared helper (or the validator call
    on either species model); the deposit is accepted and anchored to the
    conformer_key's conformer.
    """
    resp = client.post(url, json=build(conf_a, geom_b))
    _assert_mismatch(
        resp, calc_key=calc_key, conformer_key=conf_a, geometry_key=geom_b,
        geometry_conformer_key=conf_b,
    )
    resp = client.post(url, json=build(conf_b, geom_a))
    _assert_mismatch(
        resp, calc_key=calc_key, conformer_key=conf_b, geometry_key=geom_a,
        geometry_conformer_key=conf_a,
    )


@_ROUTES
def test_agreeing_keys_are_still_accepted(client, url, build, calc_key, conf_a, conf_b, geom_a, geom_b):
    """The same conformer in both keys is accepted, for either conformer.

    Pairs with the refusals above, so a validator that refused every pair of
    keys fails here.

    Mutation: refuse whenever both keys are present.
    """
    for conformer, geometry in ((conf_a, geom_a), (conf_b, geom_b)):
        resp = client.post(url, json=build(conformer, geometry))
        assert resp.status_code == 201, resp.text[:800]


@_ROUTES
def test_either_key_alone_is_unaffected(client, url, build, calc_key, conf_a, conf_b, geom_a, geom_b):
    """Only a contradiction is refused: a geometry_key with no conformer_key beside it is accepted.

    (A calculation of type sp must name a geometry on both routes, so the lone
    key exercised here is the geometry; a lone conformer_key is exercised by
    the PDep opt in ``test_network_species_calculation_anchors_to_its_conformer_key``.)

    Mutation: make the helper treat a missing conformer_key as a mismatch.
    """
    assert client.post(url, json=build(None, geom_b)).status_code == 201


def test_reaction_anchor_follows_the_agreeing_keys(client, db_session):
    """Consistent payloads anchor exactly as before: the sp joins its own conformer's observation.

    Mutation: anchor to the first conformer regardless of the keys.
    """
    resp = client.post(_REACTION_URL, json=_reaction_with_keys("h2-conf-b", "h2-geom-b"))
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    anchors = {
        key: db_session.scalar(
            select(Calculation.conformer_observation_id).where(Calculation.id == keys[key])
        )
        for key in ("h2-sp-b", "h2-opt-b", "h2-opt")
    }
    assert anchors["h2-sp-b"] is not None
    assert anchors["h2-sp-b"] == anchors["h2-opt-b"] != anchors["h2-opt"], anchors


@_ROUTES
def test_geometry_that_belongs_to_no_conformer_keeps_its_own_refusal(
    client, url, build, calc_key, conf_a, conf_b, geom_a, geom_b
):
    """A geometry_key naming no conformer's geometry is not this case.

    The rule that already governs it, ``geometry_key_unresolved`` ("must
    reference one of that species's conformer geometries"), refuses it, with or
    without a conformer_key beside it. The new check never sees it.

    Mutation: raise the new code for a geometry with no conformer.
    """
    for conformer_key in (conf_a, None):
        resp = client.post(url, json=build(conformer_key, "a-free-standing-geometry"))
        assert (resp.status_code, resp.json().get("code")) == (422, "geometry_key_unresolved"), resp.text[:600]


def test_helper_ignores_a_geometry_that_belongs_to_no_conformer():
    """The helper itself: a free-standing or undeclared geometry is not a disagreement.

    ``geometry_key_unresolved`` runs first in the request models, so the API
    test above cannot tell whether the new check also stays out of that case;
    this does.

    Mutation: raise when ``conformer_key_by_geometry_key`` has no entry for the geometry.
    """
    from types import SimpleNamespace

    from tckdb_schemas.bundle_source_rules import assert_calculation_geometry_matches_conformer

    calc = SimpleNamespace(key="c", conformer_key="a", geometry_key="free-standing")
    assert_calculation_geometry_matches_conformer([calc], {"ga": "a", "gb": "b"})
    other = SimpleNamespace(key="c", conformer_key="a", geometry_key="gb")
    with pytest.raises(Exception) as caught:
        assert_calculation_geometry_matches_conformer([other], {"ga": "a", "gb": "b"})
    assert getattr(caught.value, "code", None) == CODE


def test_only_the_species_calculation_models_carry_both_keys():
    """The request models where a calculation can state both keys.

    ``CalculationIn`` and its two subclasses (the computed-reaction and PDep
    calculations) are the only ones; the species-bundle, conformer,
    transition-state and thermo calculation models state neither. A model that
    gains both fields must carry the refusal and join this set.

    Mutation: add ``geometry_key`` and ``conformer_key`` to any other
    calculation payload and this fails.
    """
    import importlib
    import pkgutil

    import tckdb_schemas
    from pydantic import BaseModel

    import app.schemas

    found: set[str] = set()
    for pkg in (app.schemas, tckdb_schemas):
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            module = importlib.import_module(info.name)
            for obj in vars(module).values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseModel)
                    and {"geometry_key", "conformer_key"} <= set(obj.model_fields)
                ):
                    found.add(obj.__name__)
    assert found == {"CalculationIn", "ComputedReactionCalculationIn", "NetworkCalculationIn"}, found


def test_species_bundle_calculation_cannot_state_either_key(client):
    """A computed-species conformer's calculations declare neither key: extra fields are refused.

    Pins the claim that this route cannot carry the contradiction.
    """
    payload = _ethane_bundle()
    payload["conformers"][0]["additional_calculations"][0].update(
        {"conformer_key": "c0", "geometry_key": "g"}
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, resp.text[:400]
    assert resp.json().get("code") != CODE, resp.text[:400]
