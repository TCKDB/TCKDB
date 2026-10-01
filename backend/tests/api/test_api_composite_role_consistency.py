"""R2'-R5 for a linked ``composite`` energy (ADR 0021, decision 4).

A linked ``composite`` is the record's energy calculation in place of the
``sp``s, so the ensemble-aware rules of ``app.services.calculation_levels``
apply to it exactly as to an ``sp``. Every test runs on both products
(``thermo`` and ``statmech``), because each product carries its own copy of the
codes and its own call into the shared rule.

Each refusal is asserted by the response's ``code`` and by the *context keys it
names*, so a mutation that moves the check to a different rule (or drops the
composite from it) turns a test red rather than leaving a different code green.
"""

from __future__ import annotations

import pytest

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
_B3LYP_CBSB7 = {"method": "B3LYP", "basis": "CBSB7"}
_CBS_QB3 = {"method": "CBS-QB3"}
_G4 = {"method": "G4"}
_SP_LEVEL = {"method": "wB97X-D", "basis": "def2-TZVP"}

_G1 = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
_G2 = "3\nwater\nO 0.0 0.0 0.2\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"

_ELECTRONIC = -76.25
_ZPE = 0.0625


def _out(xyz: str) -> list[dict]:
    return [{"geometry": {"xyz_text": xyz}, "role": "final"}]


def _opt(xyz: str, lot: dict | None = None) -> dict:
    return {
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _B3LYP_CBSB7,
        "opt_result": {"converged": True},
        "output_geometries": _out(xyz),
    }


def _sp(xyz: str, lot: dict | None = None) -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _SP_LEVEL,
        "sp_result": {"electronic_energy_hartree": -76.4},
        "input_geometries": [{"xyz_text": xyz}],
    }


def _composite(xyz: str | None, lot: dict | None = None, *, geometry: str = "input") -> dict:
    calc: dict = {
        "type": "composite",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _CBS_QB3,
        "composite_result": {
            "assembly": "program_run",
            "electronic_energy_hartree": _ELECTRONIC,
            "e0_hartree": _ELECTRONIC + _ZPE,
            "recipe_zpe_hartree": _ZPE,
        },
    }
    if xyz is not None:
        if geometry == "input":
            calc["input_geometries"] = [{"xyz_text": xyz}]
        else:
            calc["output_geometries"] = _out(xyz)
    return calc


def _payload(product: str, calcs: dict[str, dict], links: list[tuple[str, str]], **extra) -> tuple[str, dict]:
    payload: dict = {
        "species_entry": dict(_WATER),
        "scientific_origin": "computed",
        "calculations": [{"key": key, "calculation": calc} for key, calc in calcs.items()],
        "source_calculations": [{"calculation_key": key, "role": role} for key, role in links],
    }
    if product == "thermo":
        payload.update({"enthalpy_reference_kind": "formation_298k", "h298_kj_mol": -241.8})
    else:
        payload.update({"statmech_treatment": "rrho", "external_symmetry": 2})
    payload.update(extra)
    return f"/api/v1/uploads/{product}", payload


def _refused(client, product, calcs, links, code_suffix, **extra) -> dict:
    url, payload = _payload(product, calcs, links, **extra)
    resp = client.post(url, json=payload)
    assert resp.status_code == 422, resp.text[:600]
    body = resp.json()
    assert body["code"] == f"{product}_{code_suffix}", body
    return body


PRODUCTS = pytest.mark.parametrize("product", ["thermo", "statmech"])


@PRODUCTS
def test_an_opt_with_one_composite_on_its_geometry_is_accepted(client, product):
    """The control: the shape every refusal below breaks one rule of."""
    url, payload = _payload(
        product,
        {"o1": _opt(_G1), "c1": _composite(_G1)},
        [("o1", "opt"), ("c1", "composite")],
    )
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:600]


@PRODUCTS
def test_a_composite_that_declares_no_geometry_is_not_compared(client, product):
    """Absence of evidence is not a mismatch: with one opt it is assumed to belong to it."""
    url, payload = _payload(
        product,
        {"o1": _opt(_G1), "c1": _composite(None)},
        [("o1", "opt"), ("c1", "composite")],
    )
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:600]


@PRODUCTS
def test_an_sp_and_a_composite_both_linked_as_energy_is_refused(client, product):
    body = _refused(
        client,
        product,
        {"o1": _opt(_G1), "s1": _sp(_G1), "c1": _composite(_G1)},
        [("o1", "opt"), ("s1", "sp"), ("c1", "composite")],
        "energy_sp_and_composite_linked",
    )
    assert set(body["context"]) == {"sp_calculation_refs", "composite_calculation_refs"}
    assert len(body["context"]["sp_calculation_refs"]) == 1
    assert len(body["context"]["composite_calculation_refs"]) == 1


@PRODUCTS
def test_two_composites_on_one_optimisation_geometry_are_a_duplicate(client, product):
    body = _refused(
        client,
        product,
        {"o1": _opt(_G1), "c1": _composite(_G1), "c2": _composite(_G1)},
        [("o1", "opt"), ("c1", "composite"), ("c2", "composite")],
        "role_duplicate",
    )
    assert set(body["context"]) == {"opt_calculation_ref", "composite_calculation_refs"}
    assert len(body["context"]["composite_calculation_refs"]) == 2


@PRODUCTS
def test_a_composite_on_no_linked_optimisations_geometry_is_a_mismatch(client, product):
    body = _refused(
        client,
        product,
        {"o1": _opt(_G1), "c1": _composite(_G2)},
        [("o1", "opt"), ("c1", "composite")],
        "sp_geometry_mismatch",
    )
    assert set(body["context"]) == {"composite_calculation_ref"}


@PRODUCTS
def test_the_geometry_a_composite_produced_counts_for_the_comparison(client, product):
    """A program-run composite's own optimisation: the *output* link is compared too."""
    body = _refused(
        client,
        product,
        {"o1": _opt(_G1), "c1": _composite(_G2, geometry="output")},
        [("o1", "opt"), ("c1", "composite")],
        "sp_geometry_mismatch",
    )
    assert set(body["context"]) == {"composite_calculation_ref"}
    url, payload = _payload(
        product,
        {"o1": _opt(_G1), "c1": _composite(_G1, geometry="output")},
        [("o1", "opt"), ("c1", "composite")],
    )
    assert client.post(url, json=payload).status_code == 201


@PRODUCTS
def test_composites_at_two_levels_leave_the_energy_level_ambiguous(client, product):
    body = _refused(
        client,
        product,
        {
            "o1": _opt(_G1),
            "o2": _opt(_G2),
            "c1": _composite(_G1, _CBS_QB3),
            "c2": _composite(_G2, _G4),
        },
        [("o1", "opt"), ("o2", "opt"), ("c1", "composite"), ("c2", "composite")],
        "energy_level_ambiguous",
    )
    assert set(body["context"]) == {"composite_calculation_refs"}
    assert len(body["context"]["composite_calculation_refs"]) == 2


@PRODUCTS
def test_one_composite_per_conformer_at_one_level_is_accepted(client, product):
    url, payload = _payload(
        product,
        {"o1": _opt(_G1), "o2": _opt(_G2), "c1": _composite(_G1), "c2": _composite(_G2)},
        [("o1", "opt"), ("o2", "opt"), ("c1", "composite"), ("c2", "composite")],
    )
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:600]


@PRODUCTS
def test_an_optimisation_without_its_composite_is_a_coverage_gap(client, product):
    body = _refused(
        client,
        product,
        {"o1": _opt(_G1), "o2": _opt(_G2), "c1": _composite(_G1)},
        [("o1", "opt"), ("o2", "opt"), ("c1", "composite")],
        "energy_level_requires_sp",
    )
    assert set(body["context"]) == {"uncovered_opt_calculation_refs"}
    assert len(body["context"]["uncovered_opt_calculation_refs"]) == 1


def test_a_declared_energy_level_must_equal_the_composites(client):
    """R4' (thermo declares it; statmech has no such field on this route)."""
    url, payload = _payload(
        "thermo",
        {"o1": _opt(_G1), "c1": _composite(_G1)},
        [("o1", "opt"), ("c1", "composite")],
        energy_level_of_theory=_SP_LEVEL,
    )
    resp = client.post(url, json=payload)
    assert resp.status_code == 422, resp.text[:600]
    body = resp.json()
    assert body["code"] == "thermo_energy_level_contradiction", body
    assert set(body["context"]) == {
        "declared_level_of_theory_ref",
        "composite_calculation_refs",
        "composite_level_of_theory_ref",
    }

    url, payload = _payload(
        "thermo",
        {"o1": _opt(_G1), "c1": _composite(_G1)},
        [("o1", "opt"), ("c1", "composite")],
        energy_level_of_theory=_CBS_QB3,
    )
    assert client.post(url, json=payload).status_code == 201


def test_a_legacy_composite_role_on_an_sp_is_not_a_composite_for_these_rules(client):
    """The legacy shape stays accepted: an sp under role ``composite`` joins no composite rule.

    It would otherwise be counted as a second energy next to the real ``sp``
    and refused, which would turn the warn tier of decision 7 into a refusal.
    """
    url, payload = _payload(
        "thermo",
        {"o1": _opt(_G1), "s1": _sp(_G1), "s2": _sp(_G1, _CBS_QB3)},
        [("o1", "opt"), ("s1", "sp"), ("s2", "composite")],
    )
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:600]
    assert "composite_role_on_non_composite_calculation" in [w["code"] for w in resp.json()["warnings"]]
