"""A program-run named composite (CBS-QB3, G4, ...) as a calculation (ADR 0021, P3a).

The worked payload of the plan's section 3(a): CBS-QB3 run by Gaussian as one
program run, with the separate B3LYP/CBSB7 frequency job ARC runs beside it.

What is pinned here, and why each is not vacuous
------------------------------------------------
* R1 -- geometry and frequency come from the recipe when no ``opt`` / ``freq``
  is linked (``composite_recipe``), the energy is the composite's
  (``energy_source == "composite"``), and a linked ``freq`` takes the
  frequency over (``freq``). The mutations in the PR body each break one of
  these.
* Every refusal and warning P3a adds, by code, with the response's own
  ``code`` field -- never a substring of ``detail``.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.db.models.calculation import (
    Calculation,
    CalculationCompositeResult,
    CalculationCompositeTerm,
    CalculationDependency,
)
from app.db.models.common import CalculationDependencyRole, CalculationType

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_CBS_QB3 = {"method": "CBS-QB3"}
_B3LYP_CBSB7 = {"method": "B3LYP", "basis": "CBSB7"}
_OTHER = {"method": "wB97X-D", "basis": "def2-TZVP"}

# A water-like geometry: three atoms, so the conformer is not a monatomic one.
_WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}

# Energies chosen so e0 = electronic + zpe holds exactly in binary floating point.
_ELECTRONIC = -76.25
_ZPE = 0.0625
_E0 = _ELECTRONIC + _ZPE


def _composite(*, lot: dict | None = None, **result) -> dict:
    block = {
        "assembly": "program_run",
        "electronic_energy_hartree": _ELECTRONIC,
        "e0_hartree": _E0,
        "recipe_zpe_hartree": _ZPE,
    }
    block.update(result)
    return {
        "type": "composite",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _CBS_QB3,
        "composite_result": block,
    }


def _freq(lot: dict | None = None, **overrides) -> dict:
    calc = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _B3LYP_CBSB7,
        "freq_result": {"n_imag": 0, "zpe_hartree": _ZPE},
    }
    calc.update(overrides)
    return calc


def _opt(lot: dict, **overrides) -> dict:
    calc = {
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "opt_result": {"converged": True},
    }
    calc.update(overrides)
    return calc


def _sp(lot: dict, **overrides) -> dict:
    calc = {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "sp_result": {"electronic_energy_hartree": -76.4},
    }
    calc.update(overrides)
    return calc


def _deposit_conformer(client, *, primary: dict, additional: list[dict] | None = None, xyz: str = _WATER_XYZ):
    payload: dict = {"species_entry": dict(_WATER), "geometry": {"xyz_text": xyz}, "calculation": primary}
    if additional:
        payload["additional_calculations"] = additional
    return client.post("/api/v1/uploads/conformers", json=payload)


def _thermo_payload(links: list[tuple[int, str]], **extra) -> dict:
    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": dict(_WATER),
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "source_calculations": [{"existing_calculation_id": cid, "role": role} for cid, role in links],
    }
    payload.update(extra)
    return payload


def _code_of(response, status: int = 422) -> dict:
    assert response.status_code == status, response.text[:600]
    return response.json()


def _warning_codes(response) -> list[str]:
    return [w["code"] for w in response.json().get("warnings", [])]


def _thermo_levels(client, species_entry_id: int) -> dict:
    read = client.get(f"/api/v1/scientific/species-entries/{species_entry_id}/thermo")
    assert read.status_code == 200, read.text
    return read.json()["records"][0]["levels"]


# ---------------------------------------------------------------------------
# Plan 3(a): CBS-QB3 plus ARC's separate freq
# ---------------------------------------------------------------------------


def test_worked_payload_a_levels_with_the_separate_freq_linked(client, db_session):
    """Geometry from the recipe, frequency from the linked freq, energy CBS-QB3."""
    resp = _deposit_conformer(client, primary=_composite(), additional=[_freq()])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    composite_id = body["primary_calculation"]["calculation_id"]
    freq_id = body["additional_calculations"][0]["calculation_id"]

    # The composite is the conformer's primary and carries its geometry.
    composite = db_session.get(Calculation, composite_id)
    assert composite.type is CalculationType.composite
    assert len(composite.output_geometries) == 1
    result = db_session.get(CalculationCompositeResult, composite_id)
    assert result.electronic_energy_hartree == _ELECTRONIC
    assert result.e0_hartree == _E0
    assert result.recipe_zpe_hartree == _ZPE
    # ARC's freq hangs off the composite, which has an output geometry (widened parent).
    edge = db_session.get(
        CalculationDependency, {"parent_calculation_id": composite_id, "child_calculation_id": freq_id}
    )
    assert edge is not None and edge.dependency_role is CalculationDependencyRole.freq_on

    thermo = client.post(
        "/api/v1/uploads/thermo", json=_thermo_payload([(composite_id, "composite"), (freq_id, "freq")])
    )
    assert thermo.status_code == 201, thermo.text
    levels = _thermo_levels(client, thermo.json()["species_entry_id"])

    assert levels["energy_source"] == "composite"
    assert levels["energy"]["method"] == "CBS-QB3"
    assert levels["energy"]["composite_scheme"]["name"] == "CBS-QB3"
    assert levels["geometry"]["method"] == "B3LYP" and levels["geometry"]["basis"] == "CBSB7"
    assert levels["geometry_source"] == "composite_recipe"
    assert levels["frequency"]["method"] == "B3LYP" and levels["frequency"]["basis"] == "CBSB7"
    assert levels["frequency_source"] == "freq"


def test_worked_payload_a_frequency_comes_from_the_recipe_without_a_freq(client):
    resp = _deposit_conformer(client, primary=_composite())
    assert resp.status_code == 201, resp.text
    composite_id = resp.json()["primary_calculation"]["calculation_id"]

    thermo = client.post("/api/v1/uploads/thermo", json=_thermo_payload([(composite_id, "composite")]))
    assert thermo.status_code == 201, thermo.text
    levels = _thermo_levels(client, thermo.json()["species_entry_id"])

    assert levels["energy_source"] == "composite"
    assert levels["geometry_source"] == "composite_recipe"
    assert levels["frequency_source"] == "composite_recipe"
    assert levels["frequency"]["method"] == "B3LYP" and levels["frequency"]["basis"] == "CBSB7"


def test_the_calculation_read_returns_the_result_and_its_terms(client, db_session):
    terms = [{"term_position": 0, "value_hartree": -76.0}, {"term_position": 1, "value_hartree": -0.25}]
    resp = _deposit_conformer(client, primary=_composite(terms=terms))
    assert resp.status_code == 201, resp.text
    composite_id = resp.json()["primary_calculation"]["calculation_id"]
    ref = db_session.get(Calculation, composite_id).public_ref

    read = client.get(f"/api/v1/scientific/calculations/{ref}", params={"include": "results"})
    assert read.status_code == 200, read.text
    results = read.json()["record"]["results"]
    assert results["kind"] == "composite"
    block = results["composite"]
    assert block["assembly"] == "program_run"
    assert block["electronic_energy_hartree"] == _ELECTRONIC
    assert block["e0_hartree"] == _E0
    assert block["recipe_zpe_hartree"] == _ZPE
    assert block["terms"] == terms
    # No row id anywhere in the result block.
    assert not any(key.endswith("_id") for key in block)


# ---------------------------------------------------------------------------
# Refusals of the calculation itself
# ---------------------------------------------------------------------------


def test_a_composite_calculation_without_its_result_is_refused(client):
    calc = _composite()
    del calc["composite_result"]
    body = _code_of(_deposit_conformer(client, primary=calc))
    assert body["code"] == "composite_type_requires_composite_result"


@pytest.mark.parametrize("builder", [_opt, _sp])
def test_a_composite_result_on_another_type_is_refused(client, builder):
    calc = builder(_CBS_QB3)
    calc["composite_result"] = _composite()["composite_result"]
    body = _code_of(_deposit_conformer(client, primary=calc))
    assert body["code"] == "composite_result_requires_composite_type"


def test_an_assembled_composite_is_refused_as_not_yet_accepted(client):
    body = _code_of(_deposit_conformer(client, primary=_composite(assembly="assembled")))
    assert body["code"] == "composite_assembled_not_accepted"
    assert "later release" in body["detail"]


def test_a_composite_at_a_level_that_is_not_scheme_bound_is_refused(client):
    body = _code_of(_deposit_conformer(client, primary=_composite(lot=_B3LYP_CBSB7)))
    assert body["code"] == "composite_level_not_scheme_bound"
    assert body["context"]["level_of_theory"] == "B3LYP/CBSB7"


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"e0_hartree": _E0 + 2e-6}, "composite_e0_inconsistent"),
        (
            {"terms": [{"term_position": 0, "value_hartree": _ELECTRONIC + 2e-6}]},
            "composite_terms_do_not_sum",
        ),
    ],
)
def test_the_two_arithmetic_checks_block_at_the_api(client, result, code):
    body = _code_of(_deposit_conformer(client, primary=_composite(**result)))
    assert body["code"] == code
    assert body["context"]["tolerance_hartree"] >= 1e-6


def test_an_unstated_energy_is_stored_as_unstated_not_zero(client, db_session):
    """Every energy is nullable; NULL is 'not stated'."""
    calc = _composite()
    calc["composite_result"] = {"assembly": "program_run"}
    resp = _deposit_conformer(client, primary=calc)
    assert resp.status_code == 201, resp.text
    row = db_session.get(CalculationCompositeResult, resp.json()["primary_calculation"]["calculation_id"])
    assert (row.electronic_energy_hartree, row.e0_hartree, row.recipe_zpe_hartree) == (None, None, None)
    assert db_session.scalars(select(CalculationCompositeTerm)).all() == []


# ---------------------------------------------------------------------------
# Warn tier
# ---------------------------------------------------------------------------


def test_an_opt_at_a_named_method_level_warns_and_is_stored(client, db_session):
    resp = _deposit_conformer(client, primary=_opt(_CBS_QB3))
    assert resp.status_code == 201, resp.text
    assert _warning_codes(resp).count("named_composite_deposited_as_opt") == 1
    stored = db_session.get(Calculation, resp.json()["primary_calculation"]["calculation_id"])
    assert stored.type is CalculationType.opt


def test_an_sp_at_a_named_method_level_warns_and_is_stored(client, db_session):
    resp = _deposit_conformer(client, primary=_opt(_B3LYP_CBSB7), additional=[_sp(_CBS_QB3)])
    assert resp.status_code == 201, resp.text
    assert _warning_codes(resp).count("named_composite_deposited_as_sp") == 1
    assert "named_composite_deposited_as_opt" not in _warning_codes(resp)
    stored = db_session.get(Calculation, resp.json()["additional_calculations"][0]["calculation_id"])
    assert stored.type is CalculationType.sp


def test_an_ordinary_level_does_not_warn(client):
    resp = _deposit_conformer(client, primary=_opt(_B3LYP_CBSB7), additional=[_sp(_OTHER)])
    assert resp.status_code == 201, resp.text
    assert not [c for c in _warning_codes(resp) if c.startswith("named_composite_deposited_as_")]


def test_a_composite_does_not_warn_as_a_misshapen_deposit(client):
    resp = _deposit_conformer(client, primary=_composite())
    assert resp.status_code == 201, resp.text
    assert not [c for c in _warning_codes(resp) if c.startswith("named_composite_deposited_as_")]


def test_role_composite_on_a_non_composite_calculation_warns(client):
    """The legacy shape: accepted, with a warning (decision 7)."""
    conf = _deposit_conformer(client, primary=_opt(_B3LYP_CBSB7)).json()
    opt_id = conf["primary_calculation"]["calculation_id"]
    resp = client.post("/api/v1/uploads/thermo", json=_thermo_payload([(opt_id, "composite")]))
    assert resp.status_code == 201, resp.text
    assert "composite_role_on_non_composite_calculation" in _warning_codes(resp)


def test_a_freq_at_another_level_than_the_recipes_warns(client):
    conf = _deposit_conformer(client, primary=_composite(), additional=[_freq(_OTHER)]).json()
    composite_id = conf["primary_calculation"]["calculation_id"]
    freq_id = conf["additional_calculations"][0]["calculation_id"]
    resp = client.post(
        "/api/v1/uploads/thermo", json=_thermo_payload([(composite_id, "composite"), (freq_id, "freq")])
    )
    assert resp.status_code == 201, resp.text
    assert "composite_frequency_level_differs_from_recipe" in _warning_codes(resp)


def test_a_freq_at_the_recipes_own_level_does_not_warn(client):
    conf = _deposit_conformer(client, primary=_composite(), additional=[_freq()]).json()
    composite_id = conf["primary_calculation"]["calculation_id"]
    freq_id = conf["additional_calculations"][0]["calculation_id"]
    resp = client.post(
        "/api/v1/uploads/thermo", json=_thermo_payload([(composite_id, "composite"), (freq_id, "freq")])
    )
    assert resp.status_code == 201, resp.text
    assert "composite_frequency_level_differs_from_recipe" not in _warning_codes(resp)


# ---------------------------------------------------------------------------
# Bundle primary
# ---------------------------------------------------------------------------


def _species_bundle(primary: dict) -> dict:
    return {
        "species_entry": dict(_WATER),
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _WATER_XYZ},
                "primary_calculation": {"key": "primary", **copy.deepcopy(primary)},
            }
        ],
    }


def test_a_species_bundle_may_have_a_composite_primary(client, db_session):
    resp = client.post("/api/v1/uploads/computed-species", json=_species_bundle(_composite()))
    assert resp.status_code == 201, resp.text
    composite = db_session.scalars(
        select(Calculation).where(Calculation.type == CalculationType.composite)
    ).one()
    assert len(composite.output_geometries) == 1
    assert composite.conformer_observation_id is not None


def test_a_species_bundle_primary_of_another_non_opt_type_is_still_refused(client):
    resp = client.post("/api/v1/uploads/computed-species", json=_species_bundle(_freq()))
    assert resp.status_code == 422, resp.text[:300]
    assert "must be 'opt'" in resp.text


# ---------------------------------------------------------------------------
# The legacy shape reads exactly as it did before the composite type existed
# ---------------------------------------------------------------------------
#
# A calculation of another type linked under the role ``composite`` is accepted
# (with a warning). It must also *read* as it always did: after an opt, no recipe
# levels, and without moving the thermo provenance. Expected values are what main
# returned for the same deposits.

_LOT_A = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_B = {"method": "wB97X-D", "basis": "def2-TZVP"}


def _inline_thermo(calcs: dict[str, dict], links: list[tuple[str, str]]) -> dict:
    return {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": dict(_WATER),
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "calculations": [{"key": key, "calculation": calc} for key, calc in calcs.items()],
        "source_calculations": [{"calculation_key": key, "role": role} for key, role in links],
    }


def _levels_and_provenance(client, payload: dict) -> tuple[dict, dict]:
    resp = client.post("/api/v1/uploads/thermo", json=payload)
    assert resp.status_code == 201, resp.text[:600]
    read = client.get(f"/api/v1/scientific/species-entries/{resp.json()['species_entry_id']}/thermo")
    assert read.status_code == 200, read.text
    record = read.json()["records"][0]
    return record["levels"], record["provenance"]


def test_opt_sp_and_a_legacy_composite_role_link_read_as_on_main(client):
    legacy = _sp(_CBS_QB3)
    levels, provenance = _levels_and_provenance(
        client,
        _inline_thermo(
            {"o": _opt(_LOT_A), "s": _sp(_LOT_B), "c": legacy},
            [("o", "opt"), ("s", "sp"), ("c", "composite")],
        ),
    )
    assert levels["energy_source"] == "sp" and levels["energy"]["method"] == "wB97X-D"
    assert levels["geometry"]["method"] == "B3LYP" and levels["geometry_source"] == "opt"
    assert levels["frequency"] is None and levels["frequency_source"] is None
    assert provenance["level_of_theory"]["method"] == "wB97X-D"


def test_opt_and_a_legacy_composite_role_link_read_the_opt_as_the_energy(client):
    levels, provenance = _levels_and_provenance(
        client,
        _inline_thermo({"o": _opt(_LOT_A), "c": _sp(_CBS_QB3)}, [("o", "opt"), ("c", "composite")]),
    )
    assert levels["energy_source"] == "opt" and levels["energy"]["method"] == "B3LYP"
    assert levels["frequency"] is None and levels["frequency_source"] is None
    # main's picker: sp -> composite -> freq -> opt, so the legacy link is the primary calculation.
    assert provenance["level_of_theory"]["method"] == "CBS-QB3"


def test_a_legacy_opt_at_a_named_method_level_gets_no_recipe_frequency(client):
    levels, _ = _levels_and_provenance(
        client, _inline_thermo({"o": _opt(_CBS_QB3)}, [("o", "opt")])
    )
    assert levels["geometry"]["method"] == "CBS-QB3" and levels["geometry_source"] == "opt"
    assert levels["energy_source"] == "opt"
    assert levels["frequency"] is None and levels["frequency_source"] is None


def test_a_legacy_composite_role_link_alone_is_the_energy_and_nothing_else(client):
    levels, _ = _levels_and_provenance(
        client, _inline_thermo({"c": _sp(_CBS_QB3)}, [("c", "composite")])
    )
    assert levels["energy_source"] == "composite" and levels["energy"]["method"] == "CBS-QB3"
    assert levels["geometry"] is None and levels["geometry_source"] is None
    assert levels["frequency"] is None


# ---------------------------------------------------------------------------
# Statmech reads the recipe levels too
# ---------------------------------------------------------------------------


def _out(xyz: str) -> list[dict]:
    return [{"geometry": {"xyz_text": xyz}, "role": "final"}]


def _statmech(calcs: dict[str, dict], links: list[tuple[str, str]]) -> dict:
    return {
        "species_entry": dict(_WATER),
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "calculations": [{"key": key, "calculation": calc} for key, calc in calcs.items()],
        "source_calculations": [{"calculation_key": key, "role": role} for key, role in links],
    }


def test_the_statmech_read_takes_geometry_and_frequency_from_the_recipe(client):
    composite = {**_composite(), "output_geometries": _out(_WATER_XYZ)}
    resp = client.post("/api/v1/uploads/statmech", json=_statmech({"c": composite}, [("c", "composite")]))
    assert resp.status_code == 201, resp.text[:600]
    levels = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}").json()["record"]["levels"]
    assert levels["energy_source"] == "composite" and levels["energy"]["method"] == "CBS-QB3"
    assert levels["geometry_source"] == "composite_recipe"
    assert (levels["geometry"]["method"], levels["geometry"]["basis"]) == ("B3LYP", "CBSB7")
    assert levels["frequency_source"] == "composite_recipe"
    assert (levels["frequency"]["method"], levels["frequency"]["basis"]) == ("B3LYP", "CBSB7")


def test_the_statmech_read_takes_a_legacy_composite_role_link_as_on_main(client):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_statmech({"o": _opt(_LOT_A), "c": _sp(_CBS_QB3)}, [("o", "opt"), ("c", "composite")]),
    )
    assert resp.status_code == 201, resp.text[:600]
    levels = client.get(f"/api/v1/scientific/statmech/{resp.json()['id']}").json()["record"]["levels"]
    assert levels["energy_source"] == "opt"
    assert levels["frequency"] is None and levels["frequency_source"] is None


# ---------------------------------------------------------------------------
# Inline calculations warn too
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["thermo", "statmech", "transport"])
@pytest.mark.parametrize(
    ("builder", "code"),
    [(_opt, "named_composite_deposited_as_opt"), (_sp, "named_composite_deposited_as_sp")],
)
def test_an_inline_opt_or_sp_at_a_named_method_level_warns_on_the_standalone_routes(client, route, builder, code):
    calcs = {"x": builder(_CBS_QB3)}
    if route == "thermo":
        payload = _inline_thermo(calcs, [("x", "opt" if builder is _opt else "sp")])
    elif route == "statmech":
        payload = _statmech(calcs, [("x", "opt" if builder is _opt else "sp")])
    else:
        payload = {
            "species_entry": dict(_WATER),
            "scientific_origin": "computed",
            "calculations": [{"key": "x", "calculation": calcs["x"]}],
            "source_calculations": [{"calculation_key": "x", "role": "supporting_geometry"}],
            "sigma_angstrom": 2.6,
            "epsilon_over_k_k": 80.0,
        }
    resp = client.post(f"/api/v1/uploads/{route}", json=payload)
    assert resp.status_code == 201, resp.text[:600]
    assert _warning_codes(resp).count(code) == 1


# ---------------------------------------------------------------------------
# The recipe frequency level is compared after following level merges
# ---------------------------------------------------------------------------


def test_the_freq_level_warning_follows_a_merge_of_the_recipes_frequency_level(client, db_session):
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
    from app.db.models.level_of_theory import LevelOfTheoryMerge
    from app.services.calculation_resolution import resolve_level_of_theory_ref

    first = _deposit_conformer(client, primary=_composite())
    assert first.status_code == 201, first.text
    cbs = db_session.scalar(
        select(LevelOfTheoryComposite).join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
        .where(CompositeScheme.name == "CBS-QB3")
    )
    scheme = db_session.get(CompositeScheme, cbs.scheme_id)
    recipe_level_id = scheme.frequency_level_of_theory_id
    kept = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="B3LYP", basis="cbsb7-kept"))
    db_session.add(LevelOfTheoryMerge(merged_lot_id=recipe_level_id, into_lot_id=kept.id))
    db_session.flush()

    kept_level = {"method": "B3LYP", "basis": "cbsb7-kept"}
    conf = _deposit_conformer(client, primary=_composite(), additional=[_freq(kept_level)]).json()
    resp = client.post(
        "/api/v1/uploads/thermo",
        json=_thermo_payload(
            [
                (conf["primary_calculation"]["calculation_id"], "composite"),
                (conf["additional_calculations"][0]["calculation_id"], "freq"),
            ]
        ),
    )
    assert resp.status_code == 201, resp.text[:600]
    assert "composite_frequency_level_differs_from_recipe" not in _warning_codes(resp)
