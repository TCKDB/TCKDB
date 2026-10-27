"""ADR 0023, step 2: under ``curated`` a nested ref to a record that is merely below the floor is shown with its badge.

The family modules (``test_curated_ref_guard_*_family.py``) pin the other half: a nested ref to a REJECTED or DEPRECATED
record is withheld. They build the sweep world with its hidden records withheld (``build_sweep_world(withheld=True)``).
This module builds the same world with the hidden records left as they are built, ``not_reviewed`` (no review row) or
``under_review``, and pins that the same pointers now name them, with a review badge where the model has one:

* an observation of an unlisted conformer group names the group (``ConformerGroupCoreBlock.review``);
* an entry of an unlisted transition state names its parent, which is context (``TransitionStateCoreBlock.review``);
* a solve or a fit of an unlisted network names the network.

Each is the twin of a withheld test in the family modules, on the same route. Since step 7 the record itself (``record``) is
served too: an unlisted record answers its own detail route, badged (``test_curated_detail_by_ref.py``).
"""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"
UNLISTED = {RecordReviewStatus.not_reviewed.value, RecordReviewStatus.under_review.value}


def _get(client, path, profile, **params):
    return client.get(f"{BASE}{path}", params={**params, "profile": profile})


@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def test_an_observation_of_an_unlisted_group_names_the_group_with_its_badge(client, world):
    """Twin of ``test_a_visible_observation_of_a_hidden_group_stays_visible_with_the_group_blanked``."""
    group = world.hidden["cg_h"].public_ref
    observation = world.visible["co_v2"].public_ref
    cur = _get(client, f"/conformer-observations/{observation}", "curated")
    assert cur.status_code == 200, cur.text
    block = cur.json()["record"]["conformer_group"]
    assert block is not None and block["conformer_group_ref"] == group
    assert block["review"]["status"] in UNLISTED
    exp = _get(client, f"/conformer-observations/{observation}", "exploratory")
    assert exp.json()["record"]["conformer_group"]["conformer_group_ref"] == group  # same as exploratory


def test_an_entry_of_an_unlisted_transition_state_names_its_parent_with_its_badge(client, world):
    """Twin of ``test_a_visible_entry_of_a_hidden_transition_state_stays_visible_with_the_owner_blanked``."""
    parent = world.hidden["ts_h"].public_ref
    entry = world.visible["tse_v"].public_ref
    cur = _get(client, f"/transition-state-entries/{entry}", "curated", include="entries")
    assert cur.status_code == 200, cur.text
    record = cur.json()["record"]
    assert record["transition_state"] is not None
    assert record["transition_state"]["transition_state_ref"] == parent
    assert record["transition_state"]["review"]["status"] in UNLISTED
    assert all(e["transition_state"]["transition_state_ref"] == parent for e in record["entries"])
    browse = _get(client, "/transition-states/browse", "curated", limit=100).json()["records"]
    row = next(r for r in browse if r["transition_state_entry"]["transition_state_entry_ref"] == entry)
    assert row["transition_state"]["transition_state_ref"] == parent


def test_a_solve_or_fit_of_an_unlisted_network_names_the_network(client, world):
    """Twin of ``test_a_visible_solve_or_fit_of_a_hidden_network_stays_visible_with_the_owner_blanked``."""
    network = world.hidden["net_h"].public_ref
    solve = world.visible["nsolve_v_under_hidden_net"].public_ref
    fit = world.visible["nkin_v_under_hidden_net"].public_ref

    solve_record = _get(client, f"/network-solves/{solve}", "curated", include="all").json()["record"]
    # ``NetworkContextSummary`` has no review badge field: the owner is named and nothing more.
    assert solve_record["network"]["network_ref"] == network
    assert "review" not in solve_record["network"]

    fit_record = _get(client, f"/network-kinetics/{fit}", "curated", include="all").json()["record"]
    assert fit_record["network"]["network_ref"] == network

    rows = _get(client, "/network-solves/search", "curated", has_chebyshev="true", limit=100).json()["records"]
    by_ref = {r["network_solve"]["network_solve_ref"]: r for r in rows}
    assert by_ref[solve]["network"]["network_ref"] == network
