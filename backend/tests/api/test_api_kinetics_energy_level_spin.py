"""A kinetics energy level is the method and basis, not the spin treatment (per species).

``H + H -> H2``-shaped deposit with five linked energies at one method and basis: four
unrestricted (the open-shell side) and one restricted (the closed-shell product). Spin
treatment stays in level identity, so these are two level rows; the kinetics declaration
is compared with each link modulo that one field.

Each test names the mutation it kills (spin field not excluded, another field excluded
by mistake).
"""

from __future__ import annotations

import pytest

from tests.api.test_api_bundle_provenance_warnings import _kinetics, _reaction_bundle

_REACTION = "/api/v1/uploads/computed-reaction"
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_BASE = {"method": "B3LYP", "basis": "def2-TZVP"}


def _sp(key: str, geometry_key: str, spin: str, **level) -> dict:
    return {
        "key": key,
        "type": "sp",
        "geometry_key": geometry_key,
        "software_release": _SOFTWARE,
        "level_of_theory": {**_BASE, "spin_treatment": spin, **level},
        "sp_electronic_energy_hartree": -0.5,
    }


def _mixed_spin_bundle(declared: dict | None, *, link_level: dict | None = None) -> dict:
    """Four unrestricted energies on the H side, one restricted on the H2 side, all linked."""
    link_level = link_level or {}
    bundle = _reaction_bundle(
        kinetics=[_kinetics(**({"energy_level_of_theory": declared} if declared is not None else {}))]
    )
    h, h2 = bundle["species"]
    h["calculations"] += [_sp(f"h-sp{i}", "h-geom", "unrestricted", **link_level) for i in range(4)]
    h2["calculations"].append(_sp("h2-sp", "h2-geom", "restricted", **link_level))
    bundle["kinetics"][0]["source_calculations"] = [
        *({"calculation_key": f"h-sp{i}", "role": "reactant_energy"} for i in range(4)),
        {"calculation_key": "h2-sp", "role": "product_energy"},
    ]
    return bundle


def _codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


def test_a_declaration_without_spin_treatment_is_accepted_for_mixed_spin_links(client):
    """Kills: spin_treatment not excluded from the comparison."""
    resp = client.post(_REACTION, json=_mixed_spin_bundle(dict(_BASE)))
    assert resp.status_code == 201, resp.text[:800]
    assert "missing_level_of_theory_provenance" not in _codes(resp)


def test_a_declared_spin_treatment_must_still_match_every_link(client):
    """Declaring unrestricted while a restricted energy is linked is still wrong."""
    resp = client.post(_REACTION, json=_mixed_spin_bundle({**_BASE, "spin_treatment": "unrestricted"}))
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"
    assert len(resp.json()["context"]["energy_calculation_refs"]) == 1


def test_a_declared_spin_treatment_matching_uniform_links_is_accepted(client):
    bundle = _mixed_spin_bundle({**_BASE, "spin_treatment": "unrestricted"})
    bundle["species"][1]["calculations"][-1]["level_of_theory"]["spin_treatment"] = "unrestricted"
    resp = client.post(_REACTION, json=bundle)
    assert resp.status_code == 201, resp.text[:800]


def test_a_declaration_at_another_basis_is_refused(client):
    """Kills: basis excluded by mistake."""
    resp = client.post(_REACTION, json=_mixed_spin_bundle({"method": "B3LYP", "basis": "def2-QZVP"}))
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"


def test_a_declaration_at_another_method_is_refused(client):
    resp = client.post(_REACTION, json=_mixed_spin_bundle({"method": "wB97X-D", "basis": "def2-TZVP"}))
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"


@pytest.mark.parametrize(
    ("link_extra", "declared_extra"),
    [
        ({"core_treatment": "frozen_core"}, {"core_treatment": "all_electron"}),
        ({"core_treatment": "frozen_core"}, {}),
        ({}, {"core_treatment": "all_electron"}),
        ({"pno_truncation": "tight"}, {"pno_truncation": "normal"}),
        ({"ri_approximation": "rijcosx"}, {}),
        ({"relativistic_hamiltonian": "none"}, {"relativistic_hamiltonian": "dkh2"}),
        ({"dispersion": "d3bj"}, {}),
    ],
    ids=["core", "core_link_only", "core_declared_only", "pno", "ri", "relativistic", "dispersion"],
)
def test_a_declaration_differing_in_another_identity_field_is_refused(client, link_extra, declared_extra):
    """Kills: any field other than spin_treatment dropped from the spin-agnostic key."""
    bundle = _mixed_spin_bundle({**_BASE, **declared_extra}, link_level=link_extra)
    resp = client.post(_REACTION, json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"


def test_name_spellings_of_one_level_still_agree(client):
    """The comparison normalises names the way the hash does (def2-tzvp is Def2TZVP)."""
    resp = client.post(_REACTION, json=_mixed_spin_bundle({"method": "b3lyp", "basis": "Def2TZVP"}))
    assert resp.status_code == 201, resp.text[:800]


def test_the_standalone_route_finds_source_calculations_modulo_spin(client):
    """The lookup of a declared level's calculations uses the same rule as the consistency check."""
    from tests.api.test_api_composite_kinetics_energy_level import (
        _H_ATOM,
        _METHANE,
        _METHYL,
        _deposit,
        _sp_calc,
    )
    from tests.api.test_api_composite_kinetics_energy_level import _kinetics as _standalone

    level = {"method": "B3LYP", "basis": "6-31G(d)"}
    _deposit(client, _METHYL, primary=_sp_calc({**level, "spin_treatment": "unrestricted"}))
    _deposit(client, _H_ATOM, primary=_sp_calc({**level, "spin_treatment": "unrestricted"}))
    _deposit(client, _METHANE, primary=_sp_calc({**level, "spin_treatment": "restricted"}))
    resp = _standalone(client, energy_level=level)
    assert resp.status_code == 201, resp.text[:600]


def test_the_spin_agnostic_key_is_the_hash_payload_minus_spin():
    """One source of truth: a field added to the hash payload is compared automatically."""
    from tckdb_schemas.fragments.calculation import LevelOfTheoryRef

    from app.services.calculation_resolution import _level_of_theory_payload, spin_agnostic_level_key

    ref = LevelOfTheoryRef(
        method="DLPNO-CCSD(T)",
        basis="cc-pVTZ",
        core_treatment="window",
        relativistic_hamiltonian="none",
        pno_truncation="tight",
        ri_approximation="rijcosx",
        spin_treatment="unrestricted",
    )
    full = _level_of_theory_payload(ref)
    key = spin_agnostic_level_key(ref)
    assert set(full) - set(key) == {"spin_treatment"}
    assert {k: v for k, v in full.items() if k != "spin_treatment"} == key
