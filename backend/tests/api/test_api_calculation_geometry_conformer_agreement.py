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
    for letter, xyz in (("a", _XYZ_O2), ("b", _XYZ_O2.replace("1.21", "1.30"))):
        conformers.append(
            {
                "key": f"o2_conf_{letter}",
                "geometry": {"key": f"o2_geom_{letter}", "xyz_text": xyz},
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


# Every place a calculation model that can state conformer_key / geometry_key is used,
# and what covers it. A new field site fails the guard below until it is listed here.
_COVERED = {
    "BundleSpeciesIn.calculations": "assert_calculation_geometry_matches_conformer",
    "ConformerIn.calculation": "assert_conformer_primaries_agree",
    "BundleTransitionStateIn.calculation": "assert_transition_state_calculations_unanchored",
    "BundleTransitionStateIn.calculations": "assert_transition_state_calculations_unanchored",
    "NetworkSpeciesIn.calculations": "assert_calculation_geometry_matches_conformer",
    "TransitionStateIn.calculation": "assert_transition_state_calculations_unanchored",
    "TransitionStateIn.calculations": "assert_transition_state_calculations_unanchored",
}
_EXEMPT: dict[str, str] = {}


def _models():
    import importlib
    import pkgutil

    import tckdb_schemas
    from pydantic import BaseModel

    import app.schemas

    for pkg in (app.schemas, tckdb_schemas):
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            module = importlib.import_module(info.name)
            for obj in vars(module).values():
                if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                    yield obj


def _two_key_models() -> set[type]:
    """Every model that declares both ``conformer_key`` and ``geometry_key``, whatever its base."""
    return {m for m in _models() if {"conformer_key", "geometry_key"} <= set(m.model_fields)}


def _field_sites() -> set[str]:
    import typing

    two_key = tuple(_two_key_models())

    def mentions(annotation) -> bool:
        if isinstance(annotation, type) and issubclass(annotation, two_key):
            return True
        return any(mentions(arg) for arg in typing.get_args(annotation))

    return {
        f"{m.__name__}.{name}"
        for m in _models()
        for name, field in m.model_fields.items()
        if mentions(field.annotation)
    }


def test_every_field_that_holds_a_two_key_calculation_is_checked_or_exempt():
    """Where the calculation models are *used*, not just which classes have both fields.

    ``CalculationIn`` and its subclasses state ``conformer_key`` and
    ``geometry_key``. Every model field whose annotation holds one of them is a
    place a contradicting pair can arrive, so each must be listed as covered by a
    named check or exempt with a reason. A new field (say a list of extra
    calculations on a conformer) fails here until it is.

    Mutation: add ``extra: list[ComputedReactionCalculationIn] = []`` to the
    reaction ``ConformerIn``.
    """
    assert {m.__name__ for m in _two_key_models()} == {
        "CalculationIn", "ComputedReactionCalculationIn", "NetworkCalculationIn"
    }
    sites = _field_sites()
    assert sites, "the walk found no field sites; the guard would pass on nothing"
    assert sites == set(_COVERED) | set(_EXEMPT), sorted(sites ^ (set(_COVERED) | set(_EXEMPT)))
    assert all(_EXEMPT.values())


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


# ---------------------------------------------------------------------------
# A conformer's own (primary) calculation
# ---------------------------------------------------------------------------


def _reaction_primary(**keys) -> dict:
    """h2's second conformer's own opt, stating the given keys."""
    bundle = _two_conformer_h2_bundle()
    bundle["species"][1]["conformers"][1]["calculation"].update(keys)
    return bundle


def _pdep_primary(**keys) -> dict:
    payload = _pdep_with_keys(None, "o2_geom_b")
    payload["species"][1]["conformers"][1]["calculation"].update(keys)
    return payload


_PRIMARY_ROUTES = pytest.mark.parametrize(
    ("url", "build", "calc_key", "own_conf", "own_geom", "other_conf", "other_geom"),
    [
        pytest.param(
            _REACTION_URL, _reaction_primary, "h2-opt-b",
            "h2-conf-b", "h2-geom-b", "h2-conf", "h2-geom", id="computed-reaction",
        ),
        pytest.param(
            _PDEP_URL, _pdep_primary, "o2_opt_b",
            "o2_conf_b", "o2_geom_b", "o2_conf_a", "o2_geom_a", id="network-pdep",
        ),
    ],
)


@_PRIMARY_ROUTES
def test_primary_stating_another_conformers_geometry_is_refused(
    client, url, build, calc_key, own_conf, own_geom, other_conf, other_geom
):
    """Conformer b's own opt with geometry_key = a's geometry: refused, not stored on a's row.

    Mutation: drop ``assert_conformer_primaries_agree`` from the species model.
    """
    resp = client.post(url, json=build(geometry_key=other_geom))
    _assert_mismatch(
        resp, calc_key=calc_key, conformer_key=own_conf, geometry_key=other_geom,
        geometry_conformer_key=other_conf,
    )


@_PRIMARY_ROUTES
def test_primary_stating_another_conformers_key_is_refused(
    client, url, build, calc_key, own_conf, own_geom, other_conf, other_geom
):
    """Conformer b's own opt with conformer_key = a: refused, not silently ignored.

    Mutation: ignore ``conformer_key`` on a primary.
    """
    resp = client.post(url, json=build(conformer_key=other_conf))
    _assert_mismatch(
        resp, calc_key=calc_key, conformer_key=other_conf, geometry_key=own_geom,
        geometry_conformer_key=own_conf,
    )
    # Both keys naming a: still b's primary, still refused.
    resp = client.post(url, json=build(conformer_key=other_conf, geometry_key=other_geom))
    assert (resp.status_code, resp.json().get("code")) == (422, CODE), resp.text[:600]
    assert resp.json()["context"]["conformer_key"] == other_conf


@_PRIMARY_ROUTES
def test_primary_keys_that_name_nothing_keep_their_own_codes(
    client, url, build, calc_key, own_conf, own_geom, other_conf, other_geom
):
    """An undeclared key on a primary is refused by the undeclared-key code, not ours.

    Mutation: skip the undeclared branches in ``assert_conformer_primaries_agree``.
    """
    resp = client.post(url, json=build(conformer_key="nope"))
    assert (resp.status_code, resp.json().get("code")) == (422, "conformer_key_undeclared"), resp.text[:500]
    resp = client.post(url, json=build(geometry_key="nope"))
    assert (resp.status_code, resp.json().get("code")) == (422, "geometry_key_unresolved"), resp.text[:500]


@_PRIMARY_ROUTES
def test_primary_stating_its_own_keys_is_accepted(
    client, url, build, calc_key, own_conf, own_geom, other_conf, other_geom
):
    """Its own conformer_key and geometry_key (or neither) are accepted.

    Mutation: refuse any primary that states a key.
    """
    assert client.post(url, json=build(conformer_key=own_conf, geometry_key=own_geom)).status_code == 201
    assert client.post(url, json=build(geometry_key=own_geom)).status_code == 201


# ---------------------------------------------------------------------------
# Transition-state calculations
# ---------------------------------------------------------------------------


def _reaction_ts(**edits) -> dict:
    from tests.workflows.test_computed_reaction_upload import _payload_with_aec_carriers

    payload = _payload_with_aec_carriers()
    ts = payload["transition_state"]
    where = edits.pop("where")
    calc = ts["calculation"] if where == "primary" else next(c for c in ts["calculations"] if c["key"] == "ts-freq")
    calc.update(edits)
    return payload


def _pdep_ts(**edits) -> dict:
    payload = _full_payload()
    ts = payload["transition_states"][0]
    where = edits.pop("where")
    calc = ts["calculation"] if where == "primary" else ts["calculations"][0]
    calc.update(edits)
    return payload


_TS_ROUTES = pytest.mark.parametrize(
    ("url", "build", "species_geom"),
    [
        pytest.param(_REACTION_URL, _reaction_ts, "ch3-geom", id="computed-reaction"),
        pytest.param(_PDEP_URL, _pdep_ts, "ethyl_geom", id="network-pdep"),
    ],
)


@_TS_ROUTES
@pytest.mark.parametrize("where", ["primary", "extra"])
def test_transition_state_calculation_may_not_state_a_conformer_key(client, url, build, species_geom, where):
    """A conformer_key on a TS job is refused, not silently dropped.

    Mutation: delete the ``conformer_key`` branch of
    ``assert_transition_state_calculations_unanchored``.
    """
    resp = client.post(url, json=build(where=where, conformer_key="anything"))
    assert (resp.status_code, resp.json().get("code")) == (422, "transition_state_calculation_conformer_key"), resp.text[:500]
    ctx = resp.json()["context"]
    assert ctx["conformer_key"] == "anything" and ctx["field"].endswith(".conformer_key"), ctx


@_TS_ROUTES
@pytest.mark.parametrize("where", ["primary", "extra"])
def test_transition_state_calculation_may_not_run_on_a_species_geometry(client, url, build, species_geom, where):
    """A TS job whose geometry_key is a species conformer's geometry is refused.

    Mutation: delete the ``geometry_key`` branch of
    ``assert_transition_state_calculations_unanchored``.
    """
    resp = client.post(url, json=build(where=where, geometry_key=species_geom))
    assert (resp.status_code, resp.json().get("code")) == (422, "transition_state_calculation_species_geometry"), resp.text[:500]
    assert resp.json()["context"]["geometry_key"] == species_geom


@_TS_ROUTES
def test_transition_state_calculation_on_its_own_geometry_is_accepted(client, url, build, species_geom):
    """The unchanged case: a TS freq on the saddle point's own geometry.

    Mutation: refuse every geometry_key on a TS calculation.
    """
    assert client.post(url, json=build(where="extra")).status_code == 201


# ---------------------------------------------------------------------------
# Duplicate geometry keys, and geometry_key against explicit input_geometries
# ---------------------------------------------------------------------------


def test_duplicate_geometry_key_is_left_to_the_uniqueness_rule(client):
    """Two conformers declaring one geometry key: the old uniqueness refusal, not a false mismatch.

    Mutation: build the geometry-owner map without skipping duplicated keys.
    """
    bundle = _reaction_with_keys("h2-conf", "h2-geom")
    bundle["species"][1]["conformers"][1]["geometry"]["key"] = "h2-geom"
    resp = client.post(_REACTION_URL, json=bundle)
    assert resp.status_code == 422, resp.text[:500]
    assert resp.json().get("code") != CODE, resp.text[:500]
    assert "Geometry keys must be globally unique" in resp.json()["detail"][0]["msg"], resp.text[:500]


def _xyz_of(bundle: dict, conformer: int) -> str:
    return bundle["species"][1]["conformers"][conformer]["geometry"]["xyz_text"]


def test_sp_geometry_key_must_be_among_its_explicit_input_geometries(client):
    """An sp stated on b's geometry but carrying a's xyz as input is refused.

    Paired: b's own xyz as input is accepted, and an opt may carry any starting guess.

    Mutation: skip the stated-geometry comparison in ``attach_calculation_input_geometries``.
    """
    bad = _reaction_with_keys("h2-conf-b", "h2-geom-b")
    sp = next(c for c in bad["species"][1]["calculations"] if c["key"] == "h2-sp-b")
    sp["input_geometries"] = [{"xyz_text": _xyz_of(bad, 0)}]
    resp = client.post(_REACTION_URL, json=bad)
    assert (resp.status_code, resp.json().get("code")) == (422, "calculation_input_geometry_mismatch"), resp.text[:600]
    assert resp.json()["context"]["stated_by"] == "geometry_key"

    ok = _reaction_with_keys("h2-conf-b", "h2-geom-b")
    next(c for c in ok["species"][1]["calculations"] if c["key"] == "h2-sp-b")["input_geometries"] = [
        {"xyz_text": _xyz_of(ok, 1)}
    ]
    assert client.post(_REACTION_URL, json=ok).status_code == 201

    guess = _reaction_with_keys("h2-conf-b", "h2-geom-b")
    primary = guess["species"][1]["conformers"][1]["calculation"]
    primary.update({"geometry_key": "h2-geom-b", "input_geometries": [{"xyz_text": _xyz_of(guess, 0)}]})
    assert client.post(_REACTION_URL, json=guess).status_code == 201


def test_computed_species_inline_input_geometry_must_be_its_conformers(client):
    """A freq of conformer c0 carrying c1's xyz as input is refused; its own xyz is accepted.

    Mutation: drop ``stated_geometry_id`` in ``persist_computed_species_upload``.
    """
    from tests.api.test_api_bundle_extras_622_sources import _two_conformer_ethane

    bad = _two_conformer_ethane()
    bad["conformers"][0]["additional_calculations"][0]["input_geometries"] = [
        {"xyz_text": bad["conformers"][1]["geometry"]["xyz_text"]}
    ]
    resp = client.post("/api/v1/uploads/computed-species", json=bad)
    assert (resp.status_code, resp.json().get("code")) == (422, "calculation_input_geometry_mismatch"), resp.text[:600]

    ok = _two_conformer_ethane()
    ok["conformers"][0]["additional_calculations"][0]["input_geometries"] = [
        {"xyz_text": ok["conformers"][0]["geometry"]["xyz_text"]}
    ]
    assert client.post("/api/v1/uploads/computed-species", json=ok).status_code == 201


# ---------------------------------------------------------------------------
# /uploads/conformers and /uploads/transition-states: additional freq/sp
# ---------------------------------------------------------------------------

_METHANOL = [
    ("C", -0.047131, 0.664389, 0.0),
    ("O", -0.047131, -0.758574, 0.0),
    ("H", -1.092360, 0.969785, 0.0),
    ("H", 0.439423, 1.079150, 0.889434),
    ("H", 0.439423, 1.079150, -0.889434),
    ("H", 0.866106, -1.058830, 0.0),
]


def _xyz(atoms) -> str:
    return f"{len(atoms)}\nmethanol\n" + "\n".join(f"{e} {x:.12f} {y:.12f} {z:.12f}" for e, x, y, z in atoms)


def _moved_and_reordered(atoms) -> list:
    """The same structure: rotated 90 degrees about z, translated, atoms in another order."""
    moved = [(e, -y + 3.0, x - 2.0, z + 1.5) for e, x, y, z in atoms]
    return [moved[i] for i in (5, 3, 0, 4, 1, 2)]


def _conformer_with_sp(input_xyz: str | None) -> dict:
    sp = {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "sp_result": {"electronic_energy_hartree": -115.7},
    }
    if input_xyz is not None:
        sp["input_geometries"] = [{"xyz_text": input_xyz}]
    return {
        "species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": _xyz(_METHANOL)},
        "calculation": {
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "additional_calculations": [sp],
        "label": "methanol",
    }


def _other_methanol() -> str:
    """A different structure of the same atoms (the O-H bond swung round)."""
    atoms = list(_METHANOL)
    atoms[5] = ("H", 0.2, -1.2, 0.7)
    return _xyz(atoms)


def test_conformer_upload_additional_sp_must_run_on_the_conformer_geometry(client):
    """Same route, same rule as the bundles: an sp on another structure is refused.

    Paired: its own xyz is accepted, and so is a rotated, translated,
    atom-reordered copy (the same structure).

    Mutation: drop ``stated_geometry_id`` in ``persist_additional_calculations``;
    replace the polyatomic branch of ``geometries_are_one_structure`` with ``False``.
    """
    url = "/api/v1/uploads/conformers"
    resp = client.post(url, json=_conformer_with_sp(_other_methanol()))
    assert (resp.status_code, resp.json().get("code")) == (422, "calculation_input_geometry_mismatch"), resp.text[:600]
    assert client.post(url, json=_conformer_with_sp(_xyz(_METHANOL))).status_code == 201
    moved = _xyz(_moved_and_reordered(_METHANOL))
    assert moved != _xyz(_METHANOL)
    resp = client.post(url, json=_conformer_with_sp(moved))
    assert resp.status_code == 201, resp.text[:600]


def test_transition_state_upload_additional_sp_must_run_on_the_saddle_point(client):
    """The standalone TS upload shares the helper: an sp on a methane geometry is refused.

    Mutation: drop ``stated_geometry_id`` in ``persist_additional_calculations``.
    """
    from tests.api.test_api_ts_contract_621 import _XYZ_CH4, _XYZ_TS, _standalone

    def with_sp(xyz: str) -> dict:
        payload = _standalone()
        payload["additional_calculations"].append(
            {
                "type": "sp",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                "sp_result": {"electronic_energy_hartree": -40.1},
                "input_geometries": [{"xyz_text": xyz}],
            }
        )
        return payload

    url = "/api/v1/uploads/transition-states"
    resp = client.post(url, json=with_sp(_XYZ_CH4))
    assert (resp.status_code, resp.json().get("code")) == (422, "calculation_input_geometry_mismatch"), resp.text[:600]
    assert resp.json()["context"]["stated_by"] == "the geometry of the transition state"
    assert client.post(url, json=with_sp(_XYZ_TS)).status_code == 201
