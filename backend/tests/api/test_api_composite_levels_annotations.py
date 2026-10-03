"""The composite annotations on every product read: ``notation``, ``composite_energy_verification``, ``legacy_composite_shape`` (ADR 0021, P7a).

On ``main`` (fea447ec) ``levels`` had none of the three. Held here over HTTP, for thermo,
statmech and kinetics:

* **notation** for every shape: all equal, ``x//y``, a composite on its recipe's own
  geometry, a composite on an external optimisation, an assembled composite, and absent
  levels (no notation, never a fabricated one);
* **verification** reaches the product read whenever the energy source is a composite, and only then;
* **legacy shapes** are annotated and *never re-read*: ``MAIN_LEVELS`` below is what ``main``'s own code
  returned for the same deposits (captured by running ``origin/main`` fea447ec, not derived), and the
  derived levels here are compared to it exactly;
* no database id appears in any of the new fields.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests import composite_p5_fixtures as f
from tests.api.test_api_composite_assembled import _calc_by_label, _ok
from tests.api.test_api_composite_kinetics_read import _bundle as _kinetics_bundle
from tests.api.test_api_composite_kinetics_read import _read_provenance
from tests.api.test_api_composite_program_run import (
    _CBS_QB3,
    _LOT_A,
    _LOT_B,
    _WATER_XYZ,
    _composite,
    _freq,
    _inline_thermo,
    _levels_and_provenance,
    _opt,
    _out,
    _sp,
    _statmech,
)

# What main returned (energy_source, energy, geometry_source, geometry, frequency_source, frequency, provenance level),
# each level as (method, basis, composite scheme name). Captured by running origin/main fea447ec on these deposits.
MAIN_LEVELS = {
    "legacy_composite_role_alone": {
        "energy_source": "composite",
        "energy": ("CBS-QB3", None, "CBS-QB3"),
        "geometry_source": None,
        "geometry": None,
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("CBS-QB3", None, "CBS-QB3"),
    },
    "opt_and_legacy_composite_role": {
        "energy_source": "opt",
        "energy": ("B3LYP", "6-31G(d)", None),
        "geometry_source": "opt",
        "geometry": ("B3LYP", "6-31G(d)", None),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("CBS-QB3", None, "CBS-QB3"),
    },
    "opt_at_named_method": {
        "energy_source": "opt",
        "energy": ("CBS-QB3", None, "CBS-QB3"),
        "geometry_source": "opt",
        "geometry": ("CBS-QB3", None, "CBS-QB3"),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("CBS-QB3", None, "CBS-QB3"),
    },
    "opt_only": {
        "energy_source": "opt",
        "energy": ("B3LYP", "6-31G(d)", None),
        "geometry_source": "opt",
        "geometry": ("B3LYP", "6-31G(d)", None),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("B3LYP", "6-31G(d)", None),
    },
    "opt_sp_and_legacy_composite_role": {
        "energy_source": "sp",
        "energy": ("wB97X-D", "def2-TZVP", None),
        "geometry_source": "opt",
        "geometry": ("B3LYP", "6-31G(d)", None),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("wB97X-D", "def2-TZVP", None),
    },
    "ordinary_opt_and_sp": {
        "energy_source": "sp",
        "energy": ("wB97X-D", "def2-TZVP", None),
        "geometry_source": "opt",
        "geometry": ("B3LYP", "6-31G(d)", None),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("wB97X-D", "def2-TZVP", None),
    },
    "sp_at_named_method_with_opt": {
        "energy_source": "sp",
        "energy": ("CBS-QB3", None, "CBS-QB3"),
        "geometry_source": "opt",
        "geometry": ("B3LYP", "6-31G(d)", None),
        "frequency_source": None,
        "frequency": None,
        "declared_energy": None,
        "provenance_lot": ("CBS-QB3", None, "CBS-QB3"),
    },
    "statmech_typed_composite": {
        "energy_source": "composite",
        "energy": ("CBS-QB3", None, "CBS-QB3"),
        "geometry_source": "composite_recipe",
        "geometry": ("B3LYP", "CBSB7", None),
        "frequency_source": "composite_recipe",
        "frequency": ("B3LYP", "CBSB7", None),
        "declared_energy": None,
    },
    "typed_composite_with_recipe_geometry": {
        "energy_source": "composite",
        "energy": ("CBS-QB3", None, "CBS-QB3"),
        "geometry_source": "composite_recipe",
        "geometry": ("B3LYP", "CBSB7", None),
        "frequency_source": "composite_recipe",
        "frequency": ("B3LYP", "CBSB7", None),
        "declared_energy": None,
        "provenance_lot": ("CBS-QB3", None, "CBS-QB3"),
    },
}

NEW_KEYS = {"notation", "composite_energy_verification", "legacy_composite_shape"}


def _level(summary: dict | None):
    if summary is None:
        return None
    scheme = summary["composite_scheme"]
    return (summary["method"], summary["basis"], scheme["name"] if scheme else None)


def _compact(levels: dict) -> dict:
    return {
        "energy_source": levels["energy_source"],
        "energy": _level(levels["energy"]),
        "geometry_source": levels["geometry_source"],
        "geometry": _level(levels["geometry"]),
        "frequency_source": levels["frequency_source"],
        "frequency": _level(levels["frequency"]),
        "declared_energy": levels["declared_energy"],
    }


def _no_ids(node, path="") -> list[str]:
    """Every key under ``node`` that names a database id (``*_id``); refs are fine."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if re.search(r"(^|_)id$", key):
                found.append(f"{path}.{key}")
            found.extend(_no_ids(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_no_ids(value, f"{path}[{i}]"))
    return found


def _thermo(client, payload: dict) -> tuple[dict, dict]:
    levels, provenance = _levels_and_provenance(client, payload)
    return levels, provenance


# ---------------------------------------------------------------------------
# Legacy shapes: annotated, and the derived levels are exactly main's
# ---------------------------------------------------------------------------

_LEGACY = {
    "legacy_composite_role_alone": (
        lambda: _inline_thermo({"c": _sp(_CBS_QB3)}, [("c", "composite")]),
        "composite_role_on_non_composite_calculation",
        None,
    ),
    "opt_and_legacy_composite_role": (
        lambda: _inline_thermo({"o": _opt(_LOT_A), "c": _sp(_CBS_QB3)}, [("o", "opt"), ("c", "composite")]),
        "composite_role_on_non_composite_calculation",
        "B3LYP/6-31G(d)",
    ),
    "opt_sp_and_legacy_composite_role": (
        lambda: _inline_thermo(
            {"o": _opt(_LOT_A), "s": _sp(_LOT_B), "c": _sp(_CBS_QB3)}, [("o", "opt"), ("s", "sp"), ("c", "composite")]
        ),
        "composite_role_on_non_composite_calculation",
        "wB97X-D/def2-TZVP//B3LYP/6-31G(d)",
    ),
    "opt_at_named_method": (
        lambda: _inline_thermo({"o": _opt(_CBS_QB3)}, [("o", "opt")]),
        "named_method_level_on_non_composite_calculation",
        "CBS-QB3",
    ),
    "sp_at_named_method_with_opt": (
        lambda: _inline_thermo({"o": _opt(_LOT_A), "s": _sp(_CBS_QB3)}, [("o", "opt"), ("s", "sp")]),
        "named_method_level_on_non_composite_calculation",
        "CBS-QB3//B3LYP/6-31G(d)",
    ),
    "ordinary_opt_and_sp": (
        lambda: _inline_thermo({"o": _opt(_LOT_A), "s": _sp(_LOT_B)}, [("o", "opt"), ("s", "sp")]),
        None,
        "wB97X-D/def2-TZVP//B3LYP/6-31G(d)",
    ),
    "opt_only": (lambda: _inline_thermo({"o": _opt(_LOT_A)}, [("o", "opt")]), None, "B3LYP/6-31G(d)"),
    "typed_composite_with_recipe_geometry": (
        lambda: _inline_thermo({"c": {**_composite(), "output_geometries": _out(_WATER_XYZ)}}, [("c", "composite")]),
        None,
        "CBS-QB3",
    ),
}


@pytest.mark.parametrize("name", list(_LEGACY))
def test_legacy_shapes_are_annotated_and_their_derived_levels_are_mains(client, name):
    payload, shape, notation = _LEGACY[name]
    levels, provenance = _thermo(client, payload())
    # The levels, source by source, are what main returned for the same deposit.
    expected = copy.deepcopy(MAIN_LEVELS[name])
    expected_lot = expected.pop("provenance_lot")
    assert _compact(levels) == expected
    assert _level(provenance["level_of_theory"]) == expected_lot
    # The annotation, and the notation derived from those levels.
    assert levels["legacy_composite_shape"] == shape
    assert levels["notation"] == notation


def test_the_statmech_levels_are_mains_and_carry_the_new_fields(client):
    composite = {**_composite(), "output_geometries": _out(_WATER_XYZ)}
    resp = client.post("/api/v1/uploads/statmech", json=_statmech({"c": composite}, [("c", "composite")]))
    assert resp.status_code == 201, resp.text[:600]
    levels = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}").json()["record"]["levels"]
    assert _compact(levels) == MAIN_LEVELS["statmech_typed_composite"]
    assert levels["notation"] == "CBS-QB3"
    assert levels["legacy_composite_shape"] is None
    assert levels["composite_energy_verification"] == {
        "state": "program_reported",
        "assembly": "program_run",
        "reason": None,
        "difference_hartree": None,
        "tolerance_hartree": None,
    }


def test_the_statmech_legacy_role_link_is_annotated(client):
    resp = client.post(
        "/api/v1/uploads/statmech", json=_statmech({"o": _opt(_LOT_A), "c": _sp(_CBS_QB3)}, [("o", "opt"), ("c", "composite")])
    )
    assert resp.status_code == 201, resp.text[:600]
    levels = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}").json()["record"]["levels"]
    assert levels["legacy_composite_shape"] == "composite_role_on_non_composite_calculation"
    assert levels["composite_energy_verification"] is None
    assert levels["energy_source"] == "opt"


def test_the_calculation_read_annotates_an_opt_or_sp_at_a_named_method_and_nothing_else(client, db_session):
    from app.db.models.calculation import Calculation

    def read(calc_id: int) -> dict:
        ref = db_session.get(Calculation, calc_id).public_ref
        resp = client.get(f"/api/v1/scientific/calculations/{ref}")
        assert resp.status_code == 200, resp.text[:600]
        return resp.json()["record"]

    def deposit(calc: dict) -> int:
        resp = client.post(
            "/api/v1/uploads/conformers",
            json={"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}, "geometry": {"xyz_text": _WATER_XYZ}, "calculation": calc},
        )
        assert resp.status_code == 201, resp.text[:600]
        return resp.json()["primary_calculation"]["calculation_id"]

    shape = "named_method_level_on_non_composite_calculation"
    assert read(deposit(_opt(_CBS_QB3)))["legacy_composite_shape"] == shape
    assert read(deposit(_opt(_LOT_A)))["legacy_composite_shape"] is None
    typed = read(deposit(_composite()))
    assert typed["legacy_composite_shape"] is None  # the intended shape is not a legacy one
    assert typed["composite_energy_verification"]["state"] == "program_reported"
    # The calculation itself is exactly as deposited: type and level untouched.
    assert (typed["calculation"]["type"], typed["level_of_theory"]["method"]) == ("composite", "CBS-QB3")


# ---------------------------------------------------------------------------
# Notation for the other shapes, and verification at the product read
# ---------------------------------------------------------------------------


def test_a_composite_on_an_external_optimisation_names_the_geometry(client):
    levels, _ = _thermo(
        client,
        _inline_thermo(
            {"o": _opt(_LOT_A), "c": _composite()},
            [("o", "opt"), ("c", "composite")],
        ),
    )
    assert levels["energy_source"] == "composite"
    assert levels["geometry_source"] == "opt"
    assert levels["notation"] == "CBS-QB3//B3LYP/6-31G(d)"
    assert levels["composite_energy_verification"]["state"] == "program_reported"
    assert levels["legacy_composite_shape"] is None


def test_a_composite_on_an_optimisation_at_its_own_recipe_geometry_is_the_label_alone(client):
    """The opt is at the recipe's B3LYP/CBSB7: the geometry is the recipe's own, so it is not repeated."""
    levels, _ = _thermo(
        client,
        _inline_thermo(
            {"o": _opt({"method": "B3LYP", "basis": "CBSB7"}), "c": _composite()},
            [("o", "opt"), ("c", "composite")],
        ),
    )
    assert levels["geometry_source"] == "opt"
    assert levels["notation"] == "CBS-QB3"


def test_a_record_with_no_energy_or_geometry_level_has_no_notation(client):
    levels, _ = _thermo(client, _inline_thermo({"f": _freq()}, [("f", "freq")]))
    assert levels["frequency"] is not None
    assert levels["energy"] is None and levels["geometry"] is None
    assert levels["notation"] is None
    assert levels["composite_energy_verification"] is None


def test_a_non_composite_energy_has_no_verification(client):
    levels, _ = _thermo(client, _inline_thermo({"o": _opt(_LOT_A), "s": _sp(_LOT_B)}, [("o", "opt"), ("s", "sp")]))
    assert levels["composite_energy_verification"] is None


def _assembled_thermo(client, db_session) -> tuple[dict, dict]:
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    opt = _calc_by_label(db_session, body, "opt0")
    resp = client.post(
        "/api/v1/uploads/thermo",
        json={
            "enthalpy_reference_kind": "formation_298k",
            "species_entry": dict(f.WATER),
            "scientific_origin": "computed",
            "h298_kj_mol": -241.8,
            "source_calculations": [
                {"existing_calculation_id": opt.id, "role": "opt"},
                {"existing_calculation_id": composite.id, "role": "composite"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text[:800]
    read = client.get(f"/api/v1/scientific/species-entries/{resp.json()['species_entry_id']}/thermo")
    assert read.status_code == 200, read.text[:800]
    return read.json()["records"][0]["levels"], {"composite": composite, "spq": _calc_by_label(db_session, body, "spq")}


def test_an_assembled_composite_energy_is_recomputed_on_the_product_read(client, db_session):
    levels, calcs = _assembled_thermo(client, db_session)
    assert levels["energy_source"] == "composite"
    assert levels["energy"]["composite_scheme"]["kind"] == "extrapolation"
    assert levels["composite_energy_verification"]["state"] == "recomputed"
    assert levels["composite_energy_verification"]["assembly"] == "assembled"
    # A user scheme states no geometry of its own, so the optimisation's level is written.
    assert levels["notation"].startswith(f"{f.LABEL_B}//")
    assert levels["geometry"]["method"] in levels["notation"]
    assert levels["legacy_composite_shape"] is None


def test_a_later_change_to_an_input_shows_on_the_product_read_without_a_new_upload(client, db_session):
    from sqlalchemy import select

    from app.db.models.calculation import CalculationSPEnergyComponent, CalculationSPResult

    levels, calcs = _assembled_thermo(client, db_session)
    assert levels["composite_energy_verification"]["state"] == "recomputed"
    # Move the QZ single point's energy and its reference together, so its stored split still adds up
    # (a split that stopped adding up would read as unverifiable, not as a mismatch).
    db_session.get(CalculationSPResult, calcs["spq"].id).electronic_energy_hartree += 1e-3
    db_session.scalars(
        select(CalculationSPEnergyComponent).where(
            CalculationSPEnergyComponent.calculation_id == calcs["spq"].id,
            CalculationSPEnergyComponent.component == "reference",
        )
    ).one().value_hartree += 1e-3
    db_session.flush()
    thermo = client.get(f"/api/v1/scientific/species-entries/{calcs['composite'].species_entry_id}/thermo")
    assert thermo.json()["records"][0]["levels"]["composite_energy_verification"]["state"] == "recompute_mismatch"


def test_the_kinetics_levels_carry_the_new_fields(client, db_session):
    _, levels = _read_provenance(client, db_session, _kinetics_bundle(composite=True))
    assert levels["energy_source"] == "composite"
    assert levels["composite_energy_verification"]["state"] == "program_reported"
    assert levels["legacy_composite_shape"] is None
    assert levels["notation"] is not None and levels["notation"].startswith("CBS-QB3")


def test_the_kinetics_levels_of_an_sp_energy_are_not_composite(client, db_session):
    _, levels = _read_provenance(client, db_session, _kinetics_bundle(composite=False))
    assert levels["energy_source"] == "sp"
    assert levels["composite_energy_verification"] is None
    assert levels["legacy_composite_shape"] is None
    # rotor_scan_1 is the plan's case (d): a DLPNO-CCSD(T)-F12 single point on a wB97X-D geometry.
    assert levels["geometry"] is not None
    assert levels["notation"] == f"{levels['energy']['display']}//{levels['geometry']['display']}"
    assert levels["notation"].lower().startswith("dlpno-ccsd(t)-f12/") and "//" in levels["notation"]


# ---------------------------------------------------------------------------
# No id leaks
# ---------------------------------------------------------------------------


def test_no_database_id_appears_in_any_of_the_new_fields(client, db_session):
    levels, calcs = _assembled_thermo(client, db_session)
    assert _no_ids({k: levels[k] for k in NEW_KEYS}) == []
    composite = calcs["composite"]
    record = client.get(f"/api/v1/scientific/calculations/{composite.public_ref}").json()["record"]
    assert _no_ids({k: record[k] for k in ("composite_energy_verification", "legacy_composite_shape")}) == []
    # The whole levels block, which nests the level summaries, is id-free too (the integer ids are internal).
    assert _no_ids(levels) == []
