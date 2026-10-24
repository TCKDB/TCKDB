"""``filters`` and ``eligibility`` on the select methods: the client sends exactly what the caller stated.

Every test drives ``httpx.MockTransport``. Behaviour with the live server is covered by
``backend/tests/api/scientific/test_select_filters_eligibility_*.py``.
"""

from __future__ import annotations

import json

import httpx

from conftest import make_client

SPECIES = "spe_abc123def456"
REACTION = "rxe_abc123def456"
NETWORK = "net_abc123def456"
FILTERS = {"software": "orca"}
ELIGIBILITY = {"include_rejected": True}
KINETICS = {
    "direction": "forward", "target": {"kind": "whole_reaction"}, "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0, "temperature_max_k": 1500.0, "pressure": {"kind": "independent"},
}
NETWORK_Q = {
    "coefficient_basis": "kernel", "temperature_min_k": 500.0, "temperature_max_k": 1500.0, "pressure_min_bar": 0.5,
    "pressure_max_bar": 5.0, "bath": {"components": [{"species_ref": SPECIES}]}, "partition": {"retained": ["a" * 64]},
    "scope": "single_channel", "channel_key": "assoc", "observable": "product_resolved_coefficient",
}


def _bodies(call):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"outcome": "sole_eligible_candidate"})

    client, _ = make_client(handler)
    with client:
        call(client)
    return [json.loads(r.content) for r in seen]


def test_thermo_sends_the_options_on_both_the_select_and_the_manifest_call():
    target = {"kind": "equilibrium_ensemble"}
    select, manifest = (
        _bodies(lambda c: c.select_species_thermo(SPECIES, target=target, filters=FILTERS, eligibility=ELIGIBILITY))[0],
        _bodies(lambda c: c.get_species_thermo_selection_manifest(SPECIES, target=target, filters=FILTERS, eligibility=ELIGIBILITY))[0],
    )
    assert select == manifest == {"target": target, "filters": FILTERS, "eligibility": ELIGIBILITY}


def test_options_not_given_are_not_sent():
    (body,) = _bodies(lambda c: c.select_species_thermo(SPECIES, target={"kind": "equilibrium_ensemble"}))
    assert set(body) == {"target"}


def test_kinetics_and_network_send_the_options():
    (kinetics,) = _bodies(lambda c: c.select_reaction_kinetics(REACTION, **KINETICS, filters=FILTERS, eligibility=ELIGIBILITY))
    assert kinetics["filters"] == FILTERS and kinetics["eligibility"] == ELIGIBILITY
    (network,) = _bodies(lambda c: c.select_network_kinetics(NETWORK, **NETWORK_Q, filters={"method": "wb97xd"}, eligibility=ELIGIBILITY))
    assert network["filters"] == {"method": "wb97xd"} and network["eligibility"] == ELIGIBILITY


def test_structure_methods_send_the_options_and_the_manifest_twin_passes_them_through():
    for call in (
        lambda c: c.select_species_calculations(SPECIES, filters=FILTERS, eligibility=ELIGIBILITY),
        lambda c: c.select_conformer_basins(SPECIES, filters=FILTERS, eligibility=ELIGIBILITY),
        lambda c: c.select_transition_state_evidence("tse_abc123def456", filters=FILTERS, eligibility=ELIGIBILITY),
        lambda c: c.get_species_calculation_selection_manifest(SPECIES, filters=FILTERS, eligibility=ELIGIBILITY),
    ):
        (body,) = _bodies(call)
        assert body == {"filters": FILTERS, "eligibility": ELIGIBILITY}


def test_statmech_sends_the_request_as_given_options_included():
    request = {
        "intent": "qualify", "task": "within_basin_thermal", "filters": FILTERS, "eligibility": ELIGIBILITY,
        "target": {"kind": "species_entry", "species_entry_ref": SPECIES, "scope": {"kind": "basin"}},
    }
    (body,) = _bodies(lambda c: c.select_statmech_models(request))
    assert body["filters"] == FILTERS and body["eligibility"] == ELIGIBILITY
