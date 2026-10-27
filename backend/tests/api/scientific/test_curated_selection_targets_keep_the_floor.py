"""A select request, and the network batch evaluate, name records through ``resolve_selection_handle`` (floor=True).

ADR 0023 step 7: only a DETAIL read follows the withheld set. A select chooses among records and a batch evaluate runs a
population of fits, so the refs they name must clear the approved record floor, exactly as for a list. The records below are
``not_reviewed`` or ``under_review``: a detail serves them (``test_curated_detail_by_ref.py``), a select target answers the
same 404 as an unknown ref. The network, kinetics and batch-evaluate tests fail when ``resolve_selection_handle`` is called
with ``floor=False``; the structure select also checks its subject's visibility on its own (``assert_subject_visible``), so
its test pins the behaviour but is not the one that catches the flag.

The control in each test is the exploratory profile: the same request is NOT a 404 there, so the curated 404 is the floor's.
"""

from __future__ import annotations

import pytest

from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"


@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _post(client, path, body, profile):
    return client.post(f"{BASE}{path}", params={"profile": profile}, json=body)


def _is_floor_404(client, path, body):
    exploratory = _post(client, path, body, "exploratory")
    curated = _post(client, path, body, "curated")
    assert exploratory.status_code != 404, f"control: exploratory must reach the target ({exploratory.text})"
    assert curated.status_code == 404, curated.text
    assert curated.json()["code"] == "handle_not_found"


def test_a_structure_select_target_entry_below_the_floor_is_a_404(client, world):
    tse = world.hidden["tse_h"].public_ref
    _is_floor_404(client, f"/transition-state-entries/{tse}/evidence/select", {})


def test_a_network_select_below_the_floor_is_a_404(client, world):
    from tests.api.scientific._curated_sweep_inputs import _network_question

    network = world.hidden["net_h"].public_ref
    _is_floor_404(client, f"/networks/{network}/kinetics/select", _network_question(world))


def test_a_kinetics_select_target_entry_below_the_floor_is_a_404(client, world):
    tse = world.hidden["tse_h"].public_ref
    rxe = world.extra["reaction_entry"].public_ref
    body = {
        "direction": "forward",
        "target": {"kind": "resolved_channel", "transition_state_entry_ref": tse},
        "coefficient_basis": "elementary_coefficient",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure": {"kind": "independent"},
    }
    _is_floor_404(client, f"/reaction-entries/{rxe}/kinetics/select", body)


def test_the_network_batch_evaluate_is_a_population_and_keeps_the_floor(client, world):
    """D-a: an under-review network is a 404 here as it is on ``/kinetics/select`` and ``export-selected``."""
    network = world.hidden["net_h"].public_ref
    _is_floor_404(client, f"/networks/{network}/kinetics/evaluate", {"temperature_k": [1000.0], "pressure_bar": [1.0]})
    visible = world.visible["net_v"].public_ref
    assert _post(client, f"/networks/{visible}/kinetics/evaluate", {"temperature_k": [1000.0], "pressure_bar": [1.0]}, "curated").status_code == 200
