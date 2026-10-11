"""The captured facts survive a JSON round trip exactly: a manifest that cannot be replayed is not a manifest."""

from __future__ import annotations

import json

import pytest

from app.services.statmech_selection.facts import CandidateFacts
from tests.services.statmech_selection._support import declared, legacy, member, src, ts_declared


def _through_json(facts: CandidateFacts) -> CandidateFacts:
    return CandidateFacts.from_dict(json.loads(json.dumps(facts.to_dict())))


@pytest.mark.parametrize(
    "facts",
    [
        declared(),
        declared(scope="ensemble", members=(member("m1"), member("m2", relative_energy_kj_mol=1.5, region_key="r"))),
        ts_declared(),
        legacy(),
    ],
    ids=["basin", "ensemble", "ts", "legacy"],
)
def test_every_candidate_kind_round_trips_exactly(facts):
    again = _through_json(facts)
    assert again == facts
    assert again.to_dict() == facts.to_dict()


def test_the_round_trip_keeps_what_distinguishes_two_candidates():
    a, b = declared(), declared(recipe={"energy_zero": "minimum"})
    assert _through_json(a) != _through_json(b)
    assert src("x", "geometry", "c1") != src("x", "geometry", "c2")
